from __future__ import annotations

import asyncio
import random
import discord
from discord import app_commands, ui
from datetime import datetime, timezone, timedelta
from sqlalchemy import func

from app.config import get_settings
from app.database.database import SessionLocal
from app.database.models import Streamer, Stream, PointTransaction, RegistrationRequest
from app.database.enums import PointReason, RegistrationStatus
from app.services.notification import (
    notification_queue,
    LiveNotification,
    OfflineNotification,
)

settings = get_settings()

ACCENT = 0x9146FF #0x6441A5
OFFLINE_GRAY = 0x949494 #0x807e80

LEADERBOARD_PAGE_SIZE = 5

def random_tip() -> str:
    return random.choice(settings.discord_footer_tips)


def fmt_dur(minutes):
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m" if m else f"{h}h"


def cut_title(text, max_len=35):
    """Cut title at nearest space from max_len."""
    if not text or len(text) <= max_len:
        return text

    # Find nearest space before and after max_len
    space_before = text.rfind(" ", 0, max_len)
    space_after = text.find(" ", max_len)

    if space_before == -1 and space_after == -1:
        return text[:max_len] + "..."

    if space_before == -1:
        cut = space_after
    elif space_after == -1:
        cut = space_before
    else:
        cut = space_before if (max_len - space_before) <= (space_after - max_len) else space_after

    return text[:cut].rstrip() + "..."


def bar(value, maximum, length=20):
    if maximum <= 0:
        return "░" * length
    f = min(int((value / maximum) * length), length)
    f = max(f, 1) if value > 0 else 0
    return "█" * f + "░" * (length - f)


def get_live_minutes(stream, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    started = stream.started_at.replace(tzinfo=timezone.utc)
    return (now - started).total_seconds() / 60


def get_streamer_total_minutes(streamer_id, db, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    completed_min = (
        db.query(func.sum(Stream.duration_minutes))
        .filter(Stream.streamer_id == streamer_id, Stream.duration_minutes.isnot(None))
        .scalar() or 0
    )
    open_stream = (
        db.query(Stream)
        .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
        .first()
    )
    live_min = get_live_minutes(open_stream, now) if open_stream else 0
    return completed_min + live_min


def get_all_streams_count(streamer_id, db):
    return db.query(func.count(Stream.id)).filter(Stream.streamer_id == streamer_id).scalar()


def get_global_total_minutes(db, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    completed = (
        db.query(func.sum(Stream.duration_minutes))
        .filter(Stream.duration_minutes.isnot(None))
        .scalar() or 0
    )
    open_streams = db.query(Stream).filter(Stream.ended_at.is_(None)).all()
    live = sum(get_live_minutes(s, now) for s in open_streams)
    return completed + live


def get_global_stream_count(db):
    return db.query(func.count(Stream.id)).scalar()


def get_streak(streamer_id, db):
    """Get current streak for a streamer."""
    from app.services.points import _get_current_streak
    return _get_current_streak(streamer_id, db)


def is_admin(interaction: discord.Interaction) -> bool:
    if not settings.discord_admin_role_ids:
        return False
    return any(role.id in settings.discord_admin_role_ids for role in interaction.user.roles)


def get_game_channel(bot_instance, game_name: str):
    """Get the Discord channel for a game, or None."""
    if not game_name or not settings.discord_game_channels:
        return None
    channel_id = settings.discord_game_channels.get(game_name)
    if not channel_id:
        return None
    return bot_instance.get_channel(channel_id)


async def _square_thumbnail(url: str, top_padding: int = 20) -> discord.File | None:
    """Download thumbnail and add top padding to align with author text."""
    import aiohttp
    from PIL import Image
    import io

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()

        img = Image.open(io.BytesIO(data)).convert("RGBA")
        w, h = img.size
        new_h = h + top_padding
        padded = Image.new("RGBA", (w, new_h), (0, 0, 0, 0))
        padded.paste(img, (0, top_padding))

        buf = io.BytesIO()
        padded.save(buf, format="PNG")
        buf.seek(0)
        return discord.File(buf, filename="thumb.png")
    except Exception:
        return None


# ── Pagination View ───────────────────────────────────────────

class LiveLinkView(ui.View):
    def __init__(self, twitch_url: str):
        super().__init__(timeout=None)
        self.add_item(ui.Button(
            style=discord.ButtonStyle.link,
            label="  Watch",
            url=twitch_url,
            emoji=discord.PartialEmoji(name="Twitch", id=1478792581742723183)
        ))

class PaginatorView(ui.View):
    def __init__(self, pages, current=0):
        super().__init__(timeout=300)
        self.pages = pages
        self.current = current
        self.message = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1
        self.counter.label = f"{self.current + 1}/{len(self.pages)}"

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass

    @ui.button(label="◂", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction, button):
        self.current = max(0, self.current - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction, button):
        pass

    @ui.button(label="▸", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction, button):
        self.current = min(len(self.pages) - 1, self.current + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)


class ImagePaginatorView(ui.View):
    """Paginator for image-based pages (e.g. leaderboard)."""

    def __init__(self, image_bytes_list, current=0):
        super().__init__(timeout=300)
        self.image_bytes_list = image_bytes_list  # list of PNG bytes
        self.current = current
        self.message = None  # set after sending
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.image_bytes_list) - 1
        self.counter.label = f"{self.current + 1}/{len(self.image_bytes_list)}"

    def _current_file(self):
        import io
        buf = io.BytesIO(self.image_bytes_list[self.current])
        return discord.File(buf, filename="leaderboard.png")

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass

    @ui.button(label="◂", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction, button):
        self.current = max(0, self.current - 1)
        self._update_buttons()
        await interaction.response.edit_message(attachments=[self._current_file()], view=self)

    @ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction, button):
        pass

    @ui.button(label="▸", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction, button):
        self.current = min(len(self.image_bytes_list) - 1, self.current + 1)
        self._update_buttons()
        await interaction.response.edit_message(attachments=[self._current_file()], view=self)


# ── Registration ───────────────────────────────────────────────

class RegisterModal(ui.Modal, title="Register as Streamer"):
    twitch_name = ui.TextInput(
        label="Your Twitch Username",
        placeholder="e.g. gronkh",
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            discord_id = str(interaction.user.id)
            discord_username = str(interaction.user)
            twitch_username = self.twitch_name.value.strip().lower()

            existing_pending = (
                db.query(RegistrationRequest)
                .filter(
                    RegistrationRequest.discord_id == discord_id,
                    RegistrationRequest.status == RegistrationStatus.PENDING,
                )
                .first()
            )
            if existing_pending:
                await interaction.followup.send(
                    "```diff\n- You already have a pending registration request\n```",
                    ephemeral=True,
                )
                return

            existing_streamer = db.query(Streamer).filter(Streamer.discord_id == discord_id).first()
            if existing_streamer:
                await interaction.followup.send(
                    f"```diff\n- You are already registered as {existing_streamer.display_name}\n```",
                    ephemeral=True,
                )
                return

            from app.integrations.twitch import twitch_api
            twitch_user = twitch_api.get_user(twitch_username)
            if not twitch_user:
                await interaction.followup.send(
                    f"```diff\n- Twitch user '{twitch_username}' not found\n```",
                    ephemeral=True,
                )
                return

            existing_twitch = db.query(Streamer).filter(Streamer.login == twitch_username).first()
            if existing_twitch:
                if existing_twitch.discord_id:
                    await interaction.followup.send(
                        f"```diff\n- '{twitch_user['display_name']}' is already registered by another user\n```",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        f"```diff\n- '{twitch_user['display_name']}' is already being tracked\n```\n"
                        f"Contact an admin to link your Discord account.",
                        ephemeral=True,
                    )
                return

            existing_twitch_request = (
                db.query(RegistrationRequest)
                .filter(
                    RegistrationRequest.twitch_username == twitch_username,
                    RegistrationRequest.status == RegistrationStatus.PENDING,
                )
                .first()
            )
            if existing_twitch_request:
                await interaction.followup.send(
                    f"```diff\n- There is already a pending request for '{twitch_user['display_name']}'\n```",
                    ephemeral=True,
                )
                return

            request = RegistrationRequest(
                discord_id=discord_id,
                discord_username=discord_username,
                twitch_username=twitch_username,
                status=RegistrationStatus.PENDING,
            )
            db.add(request)
            db.commit()

            embed = discord.Embed(color=ACCENT)
            embed.description = (
                f"```diff\n+ Registration request submitted!\n```\n"
                f"Twitch: **{twitch_user['display_name']}** (`{twitch_username}`)\n\n"
                f"An admin will review your request."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # Notify admins in registration channel
            channel_id = settings.discord_registration_channel_id
            admin_ids = settings.discord_admin_user_ids
            if channel_id:
                try:
                    channel = interaction.client.get_channel(channel_id)
                    if channel is None:
                        channel = await interaction.client.fetch_channel(channel_id)
                    mentions = " ".join(f"<@{uid}>" for uid in admin_ids)
                    admin_embed = discord.Embed(
                        title="New Registration Request",
                        color=ACCENT,
                        description=(
                            f"**{discord_username}** wants to register as a streamer.\n\n"
                            f"Twitch: **{twitch_user['display_name']}** (`{twitch_username}`)\n"
                            f"Use `/admin` to review."
                        ),
                    )
                    await channel.send(content=mentions or None, embed=admin_embed)
                except Exception:
                    pass  # channel not found or no permissions

        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()


# ── Pending Registration Review ────────────────────────────────

class RegistrationReviewView(ui.View):
    def __init__(self, request_id: int, twitch_username: str, discord_id: str, discord_username: str):
        super().__init__(timeout=300)
        self.request_id = request_id
        self.twitch_username = twitch_username
        self.discord_id = discord_id
        self.discord_username = discord_username

    @ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        db = SessionLocal()
        try:
            req = db.query(RegistrationRequest).filter(RegistrationRequest.id == self.request_id).first()
            if not req or req.status != RegistrationStatus.PENDING:
                await interaction.followup.send("```diff\n- Request no longer pending\n```", ephemeral=True)
                return

            from app.services.subscription import add_streamer
            from app.services.reconciliation import reconcile_live_states
            result = add_streamer(self.twitch_username, db, discord_id=self.discord_id)
            reconcile_live_states(db)

            req.status = RegistrationStatus.APPROVED
            req.reviewed_at = datetime.now(timezone.utc)
            req.reviewed_by = str(interaction.user)
            db.commit()

            for item in self.children:
                item.disabled = True

            result_embed = discord.Embed(color=ACCENT)
            result_embed.description = (
                f"~~**Registration Request #{self.request_id}**~~\n\n"
                f"Discord: <@{self.discord_id}> (`{self.discord_username}`)\n"
                f"Twitch: `{self.twitch_username}`\n\n"
                f"```diff\n+ Approved by {interaction.user.display_name}\n```"
            )
            await interaction.edit_original_response(embed=result_embed, view=self)

            await _send_registration_dm(
                interaction.guild, self.discord_id,
                approved=True, twitch_name=result["display_name"],
            )

        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()

    @ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        db = SessionLocal()
        try:
            req = db.query(RegistrationRequest).filter(RegistrationRequest.id == self.request_id).first()
            if not req or req.status != RegistrationStatus.PENDING:
                await interaction.followup.send("```diff\n- Request no longer pending\n```", ephemeral=True)
                return

            req.status = RegistrationStatus.REJECTED
            req.reviewed_at = datetime.now(timezone.utc)
            req.reviewed_by = str(interaction.user)
            db.commit()

            for item in self.children:
                item.disabled = True

            result_embed = discord.Embed(color=OFFLINE_GRAY)
            result_embed.description = (
                f"~~**Registration Request #{self.request_id}**~~\n\n"
                f"Discord: <@{self.discord_id}> (`{self.discord_username}`)\n"
                f"Twitch: `{self.twitch_username}`\n\n"
                f"```diff\n- Rejected by {interaction.user.display_name}\n```"
            )
            await interaction.edit_original_response(embed=result_embed, view=self)

            await _send_registration_dm(
                interaction.guild, self.discord_id,
                approved=False, twitch_name=self.twitch_username,
            )

        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()


async def _send_registration_dm(guild, discord_id: str, approved: bool, twitch_name: str):
    try:
        member = guild.get_member(int(discord_id))
        if not member:
            member = await guild.fetch_member(int(discord_id))
        if not member:
            return

        if approved:
            embed = discord.Embed(color=ACCENT)
            embed.description = (
                f"```diff\n+ Your registration has been approved!\n```\n"
                f"Your Twitch account **{twitch_name}** is now being tracked.\n"
                f"Happy streaming! 🎮"
            )
        else:
            embed = discord.Embed(color=OFFLINE_GRAY)
            embed.description = (
                f"```diff\n- Your registration has been rejected\n```\n"
                f"Your request to register **{twitch_name}** was not approved.\n"
                f"Contact an admin if you think this is a mistake."
            )

        await member.send(embed=embed)
    except discord.Forbidden:
        print(f"⚠️ Cannot DM user {discord_id} (DMs disabled)")
    except Exception as e:
        print(f"❌ Failed to send registration DM: {e}")


# ── Admin UI ───────────────────────────────────────────────────

class AddStreamerModal(ui.Modal, title="Add Streamer"):
    username = ui.TextInput(label="Twitch Username", placeholder="e.g. gronkh", max_length=50)
    discord_user_id = ui.TextInput(
        label="Discord User ID (optional)",
        placeholder="e.g. 123456789012345678",
        required=False,
        max_length=20,
    )

    def __init__(self, prefill_discord_id: str | None = None):
        super().__init__()
        if prefill_discord_id:
            self.discord_user_id.default = prefill_discord_id

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            discord_id = self.discord_user_id.value.strip() or None
            if discord_id:
                try:
                    int(discord_id)
                except ValueError:
                    await interaction.followup.send("```diff\n- Invalid Discord User ID\n```", ephemeral=True)
                    return

            from app.services.subscription import add_streamer
            from app.services.reconciliation import reconcile_live_states
            result = add_streamer(self.username.value, db, discord_id=discord_id)
            reconcile_live_states(db)
            embed = discord.Embed(color=ACCENT)
            desc = (
                f"```diff\n+ Added {result['display_name']}\n```\n"
                f"Login: `{result['login']}`  ·  ID: `{result['id']}`\n"
                f"Status: {'◉ Live' if result['is_live'] else '○ Offline'}"
            )
            if discord_id:
                desc += f"\nDiscord: <@{discord_id}>"
            if result.get("linked"):
                desc += "\n*(Discord account linked to existing streamer)*"
            embed.description = desc
            await interaction.followup.send(embed=embed, ephemeral=True)
        except (ValueError, Exception) as e:
            await interaction.followup.send(f"```diff\n- {e}\n```", ephemeral=True)
        finally:
            db.close()


class AddStreamerContextModal(ui.Modal, title="Add as Streamer"):
    username = ui.TextInput(label="Twitch Username", placeholder="e.g. gronkh", max_length=50)

    def __init__(self, target_discord_id: str, target_name: str):
        super().__init__()
        self.target_discord_id = target_discord_id
        self.target_name = target_name

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            from app.services.subscription import add_streamer
            from app.services.reconciliation import reconcile_live_states
            result = add_streamer(self.username.value, db, discord_id=self.target_discord_id)
            reconcile_live_states(db)
            embed = discord.Embed(color=ACCENT)
            desc = (
                f"```diff\n+ Added {result['display_name']}\n```\n"
                f"Login: `{result['login']}`  ·  ID: `{result['id']}`\n"
                f"Discord: <@{self.target_discord_id}>\n"
                f"Status: {'◉ Live' if result['is_live'] else '○ Offline'}"
            )
            if result.get("linked"):
                desc += "\n*(Discord account linked to existing streamer)*"
            embed.description = desc
            await interaction.followup.send(embed=embed, ephemeral=True)
        except (ValueError, Exception) as e:
            await interaction.followup.send(f"```diff\n- {e}\n```", ephemeral=True)
        finally:
            db.close()


class RemoveStreamerModal(ui.Modal, title="Remove Streamer"):
    username = ui.TextInput(label="Twitch Username", placeholder="e.g. gronkh", max_length=50)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            from app.services.subscription import remove_streamer
            from app.services.subscription import sync_subscriptions
            remove_streamer(self.username.value, db)
            sync_subscriptions(db)
            embed = discord.Embed(color=ACCENT)
            embed.description = f"```diff\n- Removed {self.username.value}\n```"
            await interaction.followup.send(embed=embed, ephemeral=True)
        except (ValueError, Exception) as e:
            await interaction.followup.send(f"```diff\n- {e}\n```", ephemeral=True)
        finally:
            db.close()


class UpdatePointsModal(ui.Modal, title="Update Points"):
    username = ui.TextInput(label="Twitch Username", placeholder="e.g. gronkh", max_length=50)
    points = ui.TextInput(label="Points (negative to deduct)", placeholder="e.g. 500 or -200")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            pts = int(self.points.value)
            streamer = db.query(Streamer).filter(Streamer.login == self.username.value.lower()).first()
            if not streamer:
                await interaction.followup.send(f"```diff\n- Streamer '{self.username.value}' not found\n```", ephemeral=True)
                return

            current = db.query(func.sum(PointTransaction.points)).filter(PointTransaction.streamer_id == streamer.id).scalar() or 0

            if current + pts < 0:
                await interaction.followup.send(
                    f"```diff\n- Cannot deduct {abs(pts):,} pts — {streamer.display_name} only has {current:,} pts\n```",
                    ephemeral=True,
                )
                return

            tx = PointTransaction(
                streamer_id=streamer.id,
                points=pts,
                reason=PointReason.MANUAL_ADJUSTMENT,
            )
            db.add(tx)
            db.commit()

            total = current + pts
            embed = discord.Embed(color=ACCENT)
            embed.description = (
                f"```diff\n{'+ ' if pts >= 0 else '- '}{abs(pts):,} pts for {streamer.display_name}\n```\n"
                f"New total: **{total:,}** pts"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except ValueError:
            await interaction.followup.send("```diff\n- Invalid number\n```", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()


class AdminSelect(ui.Select):
    def __init__(self, pending_count: int = 0):
        pending_label = f"Pending Registrations ({pending_count})" if pending_count else "Pending Registrations"
        options = [
            discord.SelectOption(label="Add Streamer", value="add", description="Track a new streamer", emoji="➕"),
            discord.SelectOption(label="Remove Streamer", value="remove", description="Stop tracking a streamer", emoji="➖"),
            discord.SelectOption(label="Update Points", value="points", description="Add or deduct points", emoji="💰"),
            discord.SelectOption(label="List Streamers", value="list", description="Show all tracked streamers", emoji="📋"),
            discord.SelectOption(label=pending_label, value="pending", description="Review registration requests", emoji="📝"),
            discord.SelectOption(label="Sync & Reconcile", value="sync", description="Sync subs and reconcile states", emoji="🔄"),
        ]
        super().__init__(placeholder="Select an action...", options=options)

    async def callback(self, interaction):
        choice = self.values[0]

        if choice == "add":
            await interaction.response.send_modal(AddStreamerModal())

        elif choice == "remove":
            await interaction.response.send_modal(RemoveStreamerModal())

        elif choice == "points":
            await interaction.response.send_modal(UpdatePointsModal())

        elif choice == "list":
            db = SessionLocal()
            try:
                streamers = db.query(Streamer).all()
                if not streamers:
                    await interaction.response.send_message("No streamers tracked.", ephemeral=True)
                    return
                t = "```\n"
                t += f" {'Status':<8} {'Name':<18} {'Login':<16} {'Discord'}\n"
                t += f" {'─'*8} {'─'*18} {'─'*16} {'─'*20}\n"
                for s in streamers:
                    st = "◉ Live" if s.is_live else "○ Off"
                    t += f" {st:<8} {s.display_name:<18} {s.login:<16} {s.discord_id or '—'}\n"
                t += "```"
                embed = discord.Embed(title=f"Tracked Streamers  ·  {len(streamers)}", description=t, color=ACCENT)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            finally:
                db.close()

        elif choice == "pending":
            await interaction.response.defer(ephemeral=True)
            db = SessionLocal()
            try:
                pending = (
                    db.query(RegistrationRequest)
                    .filter(RegistrationRequest.status == RegistrationStatus.PENDING)
                    .order_by(RegistrationRequest.created_at.asc())
                    .all()
                )

                if not pending:
                    embed = discord.Embed(color=ACCENT)
                    embed.description = "```\n  No pending registration requests.\n```"
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                for req in pending:
                    embed = discord.Embed(color=ACCENT)
                    embed.description = (
                        f"**Registration Request #{req.id}**\n\n"
                        f"Discord: <@{req.discord_id}> (`{req.discord_username}`)\n"
                        f"Twitch: `{req.twitch_username}`\n"
                        f"Submitted: <t:{int(req.created_at.replace(tzinfo=timezone.utc).timestamp())}:R>"
                    )
                    view = RegistrationReviewView(
                        request_id=req.id,
                        twitch_username=req.twitch_username,
                        discord_id=req.discord_id,
                        discord_username=req.discord_username,
                    )
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

            except Exception as e:
                await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
            finally:
                db.close()

        elif choice == "sync":
            await interaction.response.defer(ephemeral=True)
            db = SessionLocal()
            try:
                from app.services.subscription import sync_subscriptions
                from app.services.reconciliation import reconcile_live_states
                s = sync_subscriptions(db)
                r = reconcile_live_states(db)
                embed = discord.Embed(title="Sync & Reconcile", color=ACCENT)
                embed.description = (
                    f"```\n"
                    f"  Subscriptions\n"
                    f"  ├ Twitch       {s['twitch_subscriptions']:>6}\n"
                    f"  ├ Added        {s['added_locally']:>6}\n"
                    f"  └ Removed      {s['removed_stale']:>6}\n\n"
                    f"  Reconciliation\n"
                    f"  ├ Checked      {r['streamers_checked']:>6}\n"
                    f"  ├ Fixed On     {r['fixed_online']:>6}\n"
                    f"  ├ Fixed Off    {r['fixed_offline']:>6}\n"
                    f"  ├ Opened       {r['streams_opened']:>6}\n"
                    f"  └ Closed       {r['streams_closed']:>6}\n"
                    f"```"
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
            finally:
                db.close()


class AdminView(ui.View):
    def __init__(self, pending_count: int = 0):
        super().__init__(timeout=120)
        self.add_item(AdminSelect(pending_count=pending_count))


# ── Bot ────────────────────────────────────────────────────────

class StreamTrackerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.guild_scheduled_events = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._notification_task = None

    async def setup_hook(self):
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        self._register_commands()
        guild = discord.Object(id=settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self._notification_task = asyncio.create_task(self._notification_listener())
        print("✅ Discord commands synced")

    async def on_ready(self):
        print(f"🤖 Discord bot connected as {self.user}")
        """
        try:
            # ── TEST: send sample live + offline notification ──
            await asyncio.sleep(2)  # wait for channels to be cached
            from app.services.notification import LiveNotification, OfflineNotification
            from app.integrations.twitch import twitch_api

            # Fetch real avatar + preview from Twitch
            _test_login = "therealknossi24"
            _test_name = "TheRealKnossi24"
            _test_avatar = ""
            try:
                user_info = twitch_api.get_user(_test_login)
                if user_info:
                    _test_avatar = user_info.get("profile_image_url", "")
                    _test_name = user_info.get("display_name", _test_name)
            except Exception as e:
                print(f"⚠️ Twitch API error: {e}")

            _test_thumb = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{_test_login}-320x180.jpg"

            test_live = LiveNotification(
                streamer_login=_test_login,
                streamer_display_name=_test_name,
                profile_image_url=_test_avatar,
                game_name="Oh Baby! Kart",
                title="Testing the new notification. tu tu tu tu tu tuuuuuuuu",
                thumbnail_url=_test_thumb,
                started_at="",
                twitch_url=f"https://twitch.tv/{_test_login}",
            )

            test_offline = OfflineNotification(
                streamer_login=_test_login,
                streamer_display_name=_test_name,
                profile_image_url=_test_avatar,
                game_name="Oh Baby! Kart",
                duration_minutes=1470,
                points_awarded=[("streak_bonus", 1), ("daily_bonus", 1), ("stream_time", 1470)],
                total_points=24844,
                twitch_url=f"https://twitch.tv/{_test_login}",
            )

            print(f"🔍 Looking for channel for 'Oh Baby! Kart'...")
            print(f"🔍 discord_game_channels = {settings.discord_game_channels}")
            channel = get_game_channel(self, "Oh Baby! Kart")
            print(f"🔍 Channel result: {channel}")

            if channel:
                # Look up discord_id for test
                _test_discord_id = None
                try:
                    db = SessionLocal()
                    streamer = db.query(Streamer).filter(Streamer.login == _test_login).first()
                    if streamer and streamer.discord_id:
                        _test_discord_id = streamer.discord_id
                    db.close()
                except Exception:
                    pass

                embed, thumb_file = await self._build_live_embed(test_live, discord_id=1365043190796779661)
                kwargs = {"embed": embed, "view": LiveLinkView(test_live.twitch_url)}
                if thumb_file:
                    kwargs["file"] = thumb_file
                await channel.send(**kwargs)

                await channel.send(embed=self._build_offline_embed(test_offline))
                print("🧪 Test notifications sent")
            else:
                print("⚠️ No channel for test notifications")
            # ── END TEST ──
            

        except Exception as e:
            print(f"❌ on_ready error: {e}")
            import traceback
            traceback.print_exc()
        """

    def has_active_event(self) -> bool:
        guild = self.get_guild(settings.discord_guild_id)
        if not guild:
            return False
        for event in guild.scheduled_events:
            if event.status == discord.EventStatus.active:
                return True
        return False

    # ── Notification Listener ──────────────────────────────────

    async def _notification_listener(self):
        await self.wait_until_ready()
        print(f"📡 Notification listener ready")
        while True:
            try:
                n = await notification_queue.get()
                channel = get_game_channel(self, n.game_name)
                if not channel:
                    print(f"⚠️ No channel configured for '{n.game_name}', skipping notification")
                    continue

                if isinstance(n, LiveNotification):
                    # Look up discord_id for tag in embed
                    discord_id = None
                    try:
                        db = SessionLocal()
                        streamer = db.query(Streamer).filter(Streamer.login == n.streamer_login).first()
                        if streamer and streamer.discord_id:
                            discord_id = streamer.discord_id
                        db.close()
                    except Exception:
                        pass
                    embed, thumb_file = await self._build_live_embed(n, discord_id=discord_id)
                    kwargs = {"embed": embed, "view": LiveLinkView(n.twitch_url)}
                    if thumb_file:
                        kwargs["file"] = thumb_file
                    await channel.send(**kwargs)
                elif isinstance(n, OfflineNotification):
                    await channel.send(embed=self._build_offline_embed(n))
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Notification error: {e}")
                import traceback
                traceback.print_exc()

    # ── Notification Embeds ────────────────────────────────────

    async def _build_live_embed(self, n, discord_id=None):
        author = n.streamer_display_name
        if discord_id:
            member = self.get_user(int(discord_id))
            if member:
                author += f" ({member.display_name})"
        author += " is live"

        embed = discord.Embed(color=ACCENT)
        embed.set_author(
            name=author,
            url=n.twitch_url,
            icon_url=n.profile_image_url if n.profile_image_url else None,
        )
        desc = ""
        if n.title:
            desc += f"**{cut_title(n.title)}**"
        embed.description = desc

        thumb_file = None
        if n.thumbnail_url:
            thumb_file = await _square_thumbnail(n.thumbnail_url)
            if thumb_file:
                embed.set_thumbnail(url="attachment://thumb.png")
            else:
                embed.set_thumbnail(url=n.thumbnail_url)

        return embed, thumb_file

    def _build_offline_embed(self, n):
        h, m = divmod(n.duration_minutes, 60)
        dur = f"{h}h {m}m" if h else f"{m}m"

        pts_parts = []
        bonus_parts = []
        for reason, pts in n.points_awarded:
            label = reason.split("_")[0].title()
            if reason in ("daily_bonus", "streak_bonus"):
                bonus_parts.append(f"{label}")
            else:
                pts_parts.append(f"+{pts:,} {label}")
        all_parts = pts_parts + bonus_parts
        pts_line = "  ·  ".join(all_parts) if all_parts else ""

        embed = discord.Embed(color=OFFLINE_GRAY)
        embed.set_author(
            name=f"{n.streamer_display_name} went offline",
            icon_url=n.profile_image_url if n.profile_image_url else None,
        )
        desc = ""
        if pts_line:
            desc += f"{pts_line}\n"
        embed.set_footer(text=f"Duration: {dur}  ·  " + desc)
        return embed

    # ── Build Live Page Embed ──────────────────────────────────

    def _build_live_page(self, streamer, open_stream, pts, db, stream_info=None):
        now = datetime.now(timezone.utc)
        embed = discord.Embed(color=ACCENT)

        uptime = "—"
        if open_stream:
            started = open_stream.started_at.replace(tzinfo=timezone.utc)
            delta = now - started
            uptime = fmt_dur(int(delta.total_seconds() / 60))

        embed.set_author(
            name=f"OBB Live  ·  {streamer.display_name}",
            url=f"https://twitch.tv/{streamer.login}",
            icon_url=streamer.profile_image_url or None,
        )

        game = stream_info.get("game_name") if stream_info else None
        title = stream_info.get("title") if stream_info else None
        thumbnail = stream_info.get("thumbnail_url") if stream_info else None

        desc = ""
        if game:
            desc += f"**{game}**\n"
        if title:
            desc += f"```{title}```"
        embed.description = desc

        if thumbnail:
            embed.set_image(url=thumbnail)
        if streamer.profile_image_url:
            embed.set_thumbnail(url=streamer.profile_image_url)

        embed.set_footer(text=f"⏱ {uptime}  ·  twitch.tv/{streamer.login}")
        #embed.timestamp = now
        return embed

    # ── Leaderboard Builder ────────────────────────────────────

    def _build_leaderboard_images(self, entries):
        """Build paginated leaderboard images as list of PNG bytes."""
        from app.integrations.leaderboard_image import render_leaderboard
        import io

        if not entries:
            return None

        total_pages = (len(entries) + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE
        image_bytes = []

        for page_start in range(0, len(entries), LEADERBOARD_PAGE_SIZE):
            page_entries = entries[page_start:page_start + LEADERBOARD_PAGE_SIZE]
            page_num = page_start // LEADERBOARD_PAGE_SIZE + 1

            img = render_leaderboard(page_entries, page=page_num, total_pages=total_pages, tip=random_tip())
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            image_bytes.append(buf.getvalue())

        return image_bytes

    # ── Slash Commands ─────────────────────────────────────────

    def _register_commands(self):

        @self.tree.error
        async def on_error(interaction, error):
            print(f"❌ Command error: {error}")
            import traceback
            traceback.print_exception(type(error), error, error.__traceback__)
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred.", ephemeral=True)

        # ── /register ──

        @self.tree.command(name="register", description="Register as a streamer")
        async def cmd_register(interaction: discord.Interaction):
            await interaction.response.send_modal(RegisterModal())

        # ── Context Menu: Add as Streamer ──

        @self.tree.context_menu(name="Add as Streamer")
        async def ctx_add_streamer(interaction: discord.Interaction, member: discord.Member):
            if not is_admin(interaction):
                await interaction.response.send_message("No permission.", ephemeral=True)
                return
            modal = AddStreamerContextModal(
                target_discord_id=str(member.id),
                target_name=str(member),
            )
            await interaction.response.send_modal(modal)

        # ── Context Menu: Remove Streamer ──

        @self.tree.context_menu(name="Remove Streamer")
        async def ctx_remove_streamer(interaction: discord.Interaction, member: discord.Member):
            if not is_admin(interaction):
                await interaction.response.send_message("No permission.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            db = SessionLocal()
            try:
                streamer = db.query(Streamer).filter(Streamer.discord_id == str(member.id)).first()
                if not streamer:
                    await interaction.followup.send(
                        f"```diff\n- {member.display_name} is not registered as a streamer\n```",
                        ephemeral=True,
                    )
                    return

                from app.services.subscription import remove_streamer
                from app.services.subscription import sync_subscriptions
                display_name = streamer.display_name
                remove_streamer(streamer.login, db)
                sync_subscriptions(db)

                embed = discord.Embed(color=ACCENT)
                embed.description = (
                    f"```diff\n- Removed {display_name}\n```\n"
                    f"Discord: {member.mention}  ·  Twitch: `{streamer.login}`"
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

            except (ValueError, Exception) as e:
                await interaction.followup.send(f"```diff\n- {e}\n```", ephemeral=True)
            finally:
                db.close()

        # ── /live ──

        @self.tree.command(name="live", description="Who is currently streaming")
        @app_commands.describe(public="Show the response to everyone")
        async def cmd_live(interaction, public: bool = False):
            ephemeral = not public
            await interaction.response.defer(ephemeral=ephemeral)
            db = SessionLocal()
            try:
                from app.utils.categories import is_tracked_category
                from app.integrations.twitch import TwitchAPI
                twitch = TwitchAPI()

                streamers = db.query(Streamer).all()
                live_streamers = [s for s in streamers if s.is_live]

                tracked_live = []
                for s in live_streamers:
                    info = twitch.get_stream_info(s.id)
                    if info and is_tracked_category(info.get("game_name")):
                        tracked_live.append((s, info))

                if not tracked_live:
                    embed = discord.Embed(
                        title="OBB Live Streamers",
                        description="```\n  No one is streaming right now.\n```",
                        color=ACCENT,
                    )
                    embed.set_footer(text=random_tip())
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                    return

                if len(tracked_live) == 1:
                    s, info = tracked_live[0]
                    open_stream = db.query(Stream).filter(Stream.streamer_id == s.id, Stream.ended_at.is_(None)).first()
                    pts = db.query(func.sum(PointTransaction.points)).filter(
                        PointTransaction.streamer_id == s.id).scalar() or 0
                    embed = self._build_live_page(s, open_stream, pts, db, stream_info=info)
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                    return

                pages = []
                for s, info in tracked_live:
                    open_stream = db.query(Stream).filter(Stream.streamer_id == s.id, Stream.ended_at.is_(None)).first()
                    pts = db.query(func.sum(PointTransaction.points)).filter(
                        PointTransaction.streamer_id == s.id).scalar() or 0
                    pages.append(self._build_live_page(s, open_stream, pts, db, stream_info=info))

                view = PaginatorView(pages)
                msg = await interaction.followup.send(embed=pages[0], view=view, ephemeral=ephemeral)
                view.message = msg
            finally:
                db.close()

        # ── /leaderboard ──

        @self.tree.command(name="leaderboard", description="Points leaderboard")
        @app_commands.describe(public="Show the response to everyone")
        async def cmd_leaderboard(interaction, public: bool = False):
            ephemeral = not public
            await interaction.response.defer(ephemeral=ephemeral)
            db = SessionLocal()
            try:
                from app.utils.categories import is_streamer_tracked_live

                now = datetime.now(timezone.utc)

                results = (
                    db.query(
                        Streamer.display_name, Streamer.login, Streamer.is_live,
                        Streamer.id.label("sid"),
                        func.coalesce(func.sum(PointTransaction.points), 0).label("pts"),
                    )
                    .outerjoin(PointTransaction, PointTransaction.streamer_id == Streamer.id)
                    .group_by(Streamer.id)
                    .order_by(func.coalesce(func.sum(PointTransaction.points), 0).desc())
                    .all()
                )

                entries = []
                for i, r in enumerate(results):
                    tracked_live = r.is_live and is_streamer_tracked_live(r.sid)
                    mins = get_streamer_total_minutes(r.sid, db, now)
                    streams = get_all_streams_count(r.sid, db)
                    streak = get_streak(r.sid, db)

                    entries.append({
                        "rank": i + 1,
                        "display_name": r.display_name,
                        "login": r.login,
                        "is_live": r.is_live,
                        "tracked_live": tracked_live,
                        "pts": r.pts,
                        "hours": round(mins / 60, 1),
                        "streams": streams,
                        "streak": streak,
                    })

                image_bytes = self._build_leaderboard_images(entries)

                if image_bytes is None:
                    embed = discord.Embed(title="OBB Streamer Leaderboard", color=ACCENT)
                    embed.description = "```\n  No data yet.\n```"
                    embed.set_footer(text=random_tip())
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                elif len(image_bytes) == 1:
                    import io
                    buf = io.BytesIO(image_bytes[0])
                    file = discord.File(buf, filename="leaderboard.png")
                    await interaction.followup.send(file=file, ephemeral=ephemeral)
                else:
                    view = ImagePaginatorView(image_bytes)
                    import io
                    buf = io.BytesIO(image_bytes[0])
                    file = discord.File(buf, filename="leaderboard.png")
                    msg = await interaction.followup.send(file=file, view=view, ephemeral=ephemeral)
                    view.message = msg
            finally:
                db.close()

        # ── /streamer ──

        @self.tree.command(name="streamer", description="Stats for a specific streamer")
        @app_commands.describe(name="Twitch username", public="Show the response to everyone")
        async def cmd_streamer(interaction, name: str, public: bool = False):
            ephemeral = not public
            await interaction.response.defer(ephemeral=ephemeral)
            db = SessionLocal()
            try:
                now = datetime.now(timezone.utc)
                s = db.query(Streamer).filter(Streamer.login == name.lower()).first()
                if not s:
                    await interaction.followup.send(f"Streamer `{name}` not found.", ephemeral=True)
                    return

                all_streams = db.query(Stream).filter(Stream.streamer_id == s.id).all()
                completed = [x for x in all_streams if x.duration_minutes is not None]
                open_stream = next((x for x in all_streams if x.ended_at is None), None)

                total_min = get_streamer_total_minutes(s.id, db, now)
                total_count = len(all_streams)

                longest_completed = max((x.duration_minutes for x in completed), default=0)
                longest_live = get_live_minutes(open_stream, now) if open_stream else 0
                longest_min = max(longest_completed, longest_live)

                total_pts = db.query(func.sum(PointTransaction.points)).filter(
                    PointTransaction.streamer_id == s.id).scalar() or 0

                breakdown = (
                    db.query(PointTransaction.reason, func.sum(PointTransaction.points).label("pts"))
                    .filter(PointTransaction.streamer_id == s.id)
                    .group_by(PointTransaction.reason).all()
                )

                from app.api.public import _calculate_streak
                streak = _calculate_streak(completed)

                week_ago = now - timedelta(days=7)
                recent = [x for x in all_streams if x.started_at.replace(tzinfo=timezone.utc) >= week_ago]
                recent_min = sum(
                    x.duration_minutes if x.duration_minutes else get_live_minutes(x, now)
                    for x in recent
                )

                is_live = s.is_live

                embed = discord.Embed(
                    title=s.display_name,
                    url=f"https://twitch.tv/{s.login}",
                    color=ACCENT,
                )

                if is_live:
                    from app.utils.categories import is_streamer_tracked_live
                    tracked = is_streamer_tracked_live(s.id)

                    if open_stream:
                        started = open_stream.started_at.replace(tzinfo=timezone.utc)
                        delta = now - started
                        uptime = fmt_dur(int(delta.total_seconds() / 60))
                        if tracked:
                            embed.description = f"```diff\n+ LIVE  ·  {uptime} uptime\n```"
                        else:
                            from app.integrations.twitch import TwitchAPI
                            twitch = TwitchAPI()
                            info = twitch.get_stream_info(s.id)
                            cat = info.get("game_name", "Unknown") if info else "Unknown"
                            embed.description = f"```diff\n+ LIVE  ·  {uptime} uptime\n- Category '{cat}' is not tracked\n```"
                    else:
                        if tracked:
                            embed.description = "```diff\n+ LIVE\n```"
                        else:
                            from app.integrations.twitch import TwitchAPI
                            twitch = TwitchAPI()
                            info = twitch.get_stream_info(s.id)
                            cat = info.get("game_name", "Unknown") if info else "Unknown"
                            embed.description = f"```diff\n+ LIVE\n- Category '{cat}' is not tracked\n```"
                else:
                    embed.description = "```\n  Offline\n```"

                if s.profile_image_url:
                    embed.set_thumbnail(url=s.profile_image_url)

                avg = round(total_min / total_count / 60, 1) if total_count else 0
                longest_str = fmt_dur(int(longest_min)) if longest_min > 0 else "—"
                streak_str = f"{streak}d" if streak else "—"

                overview = "```\n"
                overview += f"  Points       {total_pts:>10,}\n"
                overview += f"  Streams      {total_count:>10}\n"
                overview += f"  Total Time   {round(total_min / 60, 1):>9}h\n"
                overview += f"  Avg Stream   {avg:>9}h\n"
                overview += f"  Longest      {longest_str:>10}\n"
                overview += f"  Streak       {streak_str:>10}\n"
                overview += "```"
                embed.add_field(name="Overview", value=overview, inline=False)

                embed.add_field(
                    name="Last 7 Days",
                    value=f"`{len(recent)}` streams  ·  `{round(recent_min / 60, 1)}h`",
                    inline=False,
                )

                if breakdown:
                    bd = "```\n"
                    for r in breakdown:
                        label = r.reason.replace("_", " ").title()
                        bd += f"  {label:<16} {r.pts:>8,}\n"
                    bd += "```"
                    embed.add_field(name="Points Breakdown", value=bd, inline=False)

                embed.set_footer(text=random_tip())
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            finally:
                db.close()

        # ── /stats ──

        @self.tree.command(name="stats", description="Global tracking statistics")
        @app_commands.describe(public="Show the response to everyone")
        async def cmd_stats(interaction, public: bool = False):
            ephemeral = not public
            await interaction.response.defer(ephemeral=ephemeral)
            db = SessionLocal()
            try:
                from app.utils.categories import is_streamer_tracked_live
                from app.integrations.stats_image import render_stats, to_bytes

                now = datetime.now(timezone.utc)
                streamers = db.query(Streamer).all()
                tracked_live = [s for s in streamers if s.is_live and is_streamer_tracked_live(s.id)]
                total_streams = get_global_stream_count(db)
                total_min = get_global_total_minutes(db, now)
                total_pts = db.query(func.sum(PointTransaction.points)).scalar() or 0

                live_list = [(s.display_name, s.login) for s in tracked_live]

                img = render_stats({
                    "streamers": len(streamers),
                    "live_now": len(tracked_live),
                    "total_streams": total_streams,
                    "total_hours": round(total_min / 60, 1),
                    "total_points": total_pts,
                    "live_list": live_list,
                }, tip=random_tip())

                import io
                buf = io.BytesIO(to_bytes(img))
                file = discord.File(buf, filename="stats.png")
                await interaction.followup.send(file=file, ephemeral=ephemeral)
            finally:
                db.close()

        # ── /info ──

        @self.tree.command(name="info", description="How the stream tracker works")
        @app_commands.describe(public="Show the response to everyone")
        async def cmd_info(interaction, public: bool = False):
            ephemeral = not public
            from app.services.points import (
                POINTS_PER_MINUTE,
                DAILY_BONUS_POINTS,
                STREAK_BONUS_MULTIPLIER,
                EVENT_MULTIPLIER,
            )
            from app.integrations.info_image import render_info, to_bytes

            categories = settings.tracked_categories

            img = render_info(
                categories,
                tip=random_tip(),
                points_per_min=POINTS_PER_MINUTE,
                daily_bonus=DAILY_BONUS_POINTS,
                streak_multiplier=STREAK_BONUS_MULTIPLIER,
                event_multiplier=EVENT_MULTIPLIER,
            )

            import io
            buf = io.BytesIO(to_bytes(img))
            file = discord.File(buf, filename="info.png")
            await interaction.response.send_message(file=file, ephemeral=ephemeral)

        # ── /admin ──

        @self.tree.command(name="admin", description="Stream Tracker admin panel")
        async def cmd_admin(interaction):
            if not is_admin(interaction):
                await interaction.response.send_message("No permission.", ephemeral=True)
                return
            db = SessionLocal()
            try:
                pending_count = (
                    db.query(func.count(RegistrationRequest.id))
                    .filter(RegistrationRequest.status == RegistrationStatus.PENDING)
                    .scalar()
                )
                embed = discord.Embed(title="Admin Panel", description="Select an action below.", color=ACCENT)
                await interaction.response.send_message(embed=embed, view=AdminView(pending_count=pending_count),
                                                        ephemeral=True)
            finally:
                db.close()


bot = StreamTrackerBot()


async def run_bot():
    token = settings.discord_bot_token
    if not token:
        print("⚠️ No Discord bot token configured, skipping bot")
        return
    try:
        await bot.start(token)
    except Exception as e:
        print(f"❌ Discord bot error: {e}")