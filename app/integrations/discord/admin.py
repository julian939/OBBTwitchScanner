from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import ui
from sqlalchemy import func

from app.config import get_settings
from app.database.database import SessionLocal
from app.database.models import Streamer, Stream, PointTransaction, RegistrationRequest
from app.database.enums import PointReason, RegistrationStatus
from app.integrations.discord.constants import ACCENT
from app.integrations.discord.helpers import is_admin

settings = get_settings()


class AddStreamerModal(ui.Modal, title="Add Streamer"):
    username = ui.TextInput(label="Twitch Username", placeholder="e.g. KaiCenat", max_length=50)
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
    username = ui.TextInput(label="Twitch Username", placeholder="e.g. KaiCenat", max_length=50)

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
    username = ui.TextInput(label="Twitch or Discord Name", placeholder="e.g. KaiCenat", max_length=50)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            from app.services.subscription import remove_streamer, sync_subscriptions
            from app.integrations.discord.helpers import find_streamer
            streamer = find_streamer(self.username.value, db, interaction.guild)
            if not streamer:
                await interaction.followup.send(f"```diff\n- Streamer '{self.username.value}' not found\n```", ephemeral=True)
                return
            remove_streamer(streamer.login, db)
            sync_subscriptions(db)
            embed = discord.Embed(color=ACCENT)
            embed.description = f"```diff\n- Removed {streamer.display_name}\n```"
            await interaction.followup.send(embed=embed, ephemeral=True)
        except (ValueError, Exception) as e:
            await interaction.followup.send(f"```diff\n- {e}\n```", ephemeral=True)
        finally:
            db.close()


class RefreshStreamerModal(ui.Modal, title="Refresh Streamer"):
    discord_id = ui.TextInput(label="Discord User ID", placeholder="e.g. 123456789012345678", max_length=20)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            streamer = db.query(Streamer).filter(Streamer.discord_id == self.discord_id.value.strip()).first()
            if not streamer:
                await interaction.followup.send(
                    f"```diff\n- No streamer found for Discord ID '{self.discord_id.value}'\n```",
                    ephemeral=True,
                )
                return

            from app.integrations.twitch import twitch_api
            data = twitch_api.get_user_by_id(streamer.id)
            if not data:
                await interaction.followup.send("```diff\n- Could not fetch data from Twitch\n```", ephemeral=True)
                return

            old_login = streamer.login
            old_display = streamer.display_name

            streamer.login = data["login"]
            streamer.display_name = data["display_name"]
            streamer.profile_image_url = data.get("profile_image_url") or streamer.profile_image_url
            db.commit()

            embed = discord.Embed(color=ACCENT)
            changed = old_login != streamer.login or old_display != streamer.display_name
            if changed:
                embed.description = (
                    f"```diff\n+ Streamer refreshed\n```\n"
                    f"Login: `{old_login}` → `{streamer.login}`\n"
                    f"Display: `{old_display}` → `{streamer.display_name}`"
                )
            else:
                embed.description = f"```diff\n+ {streamer.login} — no changes\n```"
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()


class UpdatePointsModal(ui.Modal, title="Update Points"):
    username = ui.TextInput(label="Twitch or Discord Name", placeholder="e.g. KaiCenat", max_length=50)
    points = ui.TextInput(label="Points (negative to deduct)", placeholder="e.g. 500 or -200")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            pts = int(self.points.value)
            from app.integrations.discord.helpers import find_streamer
            streamer = find_streamer(self.username.value, db, interaction.guild)
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


class LockStreamerModal(ui.Modal, title="Lock / Unlock Streamer"):
    username = ui.TextInput(label="Twitch or Discord Name", placeholder="e.g. KaiCenat", max_length=50)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            from app.integrations.discord.helpers import find_streamer
            streamer = find_streamer(self.username.value, db, interaction.guild)
            if not streamer:
                await interaction.followup.send(f"```diff\n- Streamer '{self.username.value}' not found\n```", ephemeral=True)
                return

            new_locked = not streamer.is_locked
            streamer.is_locked = new_locked

            if new_locked:
                from app.services.stream_tracker import _cancel_pending_offline
                _cancel_pending_offline(streamer.id)

                open_stream = (
                    db.query(Stream)
                    .filter(Stream.streamer_id == streamer.id, Stream.ended_at.is_(None))
                    .first()
                )
                if open_stream:
                    now = datetime.now(timezone.utc)
                    started = open_stream.started_at.replace(tzinfo=timezone.utc)
                    duration_minutes = int((now - started).total_seconds() / 60)
                    open_stream.ended_at = now
                    open_stream.duration_minutes = duration_minutes

                    from app.services.points import award_stream_end_points, EVENT_MULTIPLIER
                    from app.integrations.discord.bot import bot as _bot
                    try:
                        multiplier = EVENT_MULTIPLIER if _bot.has_active_event() else 1
                    except Exception:
                        multiplier = 1
                    award_stream_end_points(streamer.id, open_stream, db, multiplier=multiplier, end_time=now)

                if streamer.discord_id:
                    from app.services.roles import remove_live_role
                    remove_live_role(streamer.discord_id)

            db.commit()

            from app.services.roles import schedule_leaderboard_role_sync
            schedule_leaderboard_role_sync()

            status = "🔒 Locked" if new_locked else "🔓 Unlocked"
            embed = discord.Embed(color=ACCENT)
            embed.description = f"```\n  {status}: {streamer.display_name}\n```"
            await interaction.followup.send(embed=embed, ephemeral=True)
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
            discord.SelectOption(label="Refresh Streamer", value="refresh", description="Sync name from Twitch", emoji="🔁"),
            discord.SelectOption(label="Update Points", value="points", description="Add or deduct points", emoji="💰"),
            discord.SelectOption(label="List Streamers", value="list", description="Show all tracked streamers", emoji="📋"),
            discord.SelectOption(label=pending_label, value="pending", description="Review registration requests", emoji="📝"),
            discord.SelectOption(label="Sync & Reconcile", value="sync", description="Sync subs and reconcile states", emoji="🔄"),
            discord.SelectOption(label="Lock / Unlock Streamer", value="lock", description="Toggle lock on a streamer", emoji="🔒"),
        ]
        super().__init__(placeholder="Select an action...", options=options)

    async def callback(self, interaction):
        choice = self.values[0]

        if choice == "add":
            await interaction.response.send_modal(AddStreamerModal())

        elif choice == "remove":
            await interaction.response.send_modal(RemoveStreamerModal())

        elif choice == "refresh":
            await interaction.response.send_modal(RefreshStreamerModal())

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
                    lock = "🔒" if s.is_locked else "  "
                    st = "◉ Live" if s.is_live else "○ Off"
                    t += f" {lock}{st:<8} {s.display_name:<18} {s.login:<16} {s.discord_id or '—'}\n"
                t += "```"
                embed = discord.Embed(title=f"Tracked Streamers  ·  {len(streamers)}", description=t, color=ACCENT)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            finally:
                db.close()

        elif choice == "pending":
            await interaction.response.defer(ephemeral=True)
            db = SessionLocal()
            try:
                from app.integrations.discord.registration import RegistrationReviewView
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

        elif choice == "lock":
            await interaction.response.send_modal(LockStreamerModal())

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
