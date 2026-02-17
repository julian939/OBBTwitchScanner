from __future__ import annotations

import asyncio
import discord
from discord import app_commands, ui
from datetime import datetime, timezone, timedelta
from sqlalchemy import func

from app.config import get_settings
from app.database.database import SessionLocal
from app.database.models import Streamer, Stream, PointTransaction
from app.services.notification import (
    notification_queue,
    LiveNotification,
    OfflineNotification,
)

settings = get_settings()

ACCENT = 0xE91E8C
PURPLE = 0x9146FF
DARK = 0x5B2D8E
SUCCESS = 0x2ECC71
BG_DARK = 0x1E1F22
GOLD = 0xF5C842
EMBED_BG = 0x2B2D31

TEST_PFP = "https://static-cdn.jtvnbs.net/jtv_user_pictures/asmongold-profile_image-f7ddcbd0332f5d28-300x300.png"
TEST_PREVIEW = "https://static-cdn.jtvnbs.net/previews-ttv/live_user_asmongold-1280x720.jpg"


def fmt_dur(minutes):
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m" if m else f"{h}h"


def bar(value, maximum, length=10):
    if maximum <= 0:
        return "░" * length
    f = min(int((value / maximum) * length), length)
    return "▓" * max(f, 1) + "░" * (length - max(f, 1))


def get_live_minutes(stream, now=None):
    """Get current duration of an open stream in minutes."""
    if now is None:
        now = datetime.now(timezone.utc)
    started = stream.started_at.replace(tzinfo=timezone.utc)
    return (now - started).total_seconds() / 60


def get_streamer_total_minutes(streamer_id, db, now=None):
    """Get total streamed minutes including currently live stream."""
    if now is None:
        now = datetime.now(timezone.utc)

    # Completed streams
    completed_min = (
        db.query(func.sum(Stream.duration_minutes))
        .filter(Stream.streamer_id == streamer_id, Stream.duration_minutes.isnot(None))
        .scalar() or 0
    )

    # Open stream (currently live)
    open_stream = (
        db.query(Stream)
        .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
        .first()
    )
    live_min = get_live_minutes(open_stream, now) if open_stream else 0

    return completed_min + live_min


def get_all_streams_count(streamer_id, db):
    """Count all streams including currently open ones."""
    return db.query(func.count(Stream.id)).filter(Stream.streamer_id == streamer_id).scalar()


def get_global_total_minutes(db, now=None):
    """Get total minutes across all streamers including live."""
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
    """Count all streams including open ones."""
    return db.query(func.count(Stream.id)).scalar()


# ── Live Command Pagination ────────────────────────────────────

class LivePaginatorView(ui.View):
    def __init__(self, pages, current=0):
        super().__init__(timeout=300)
        self.pages = pages
        self.current = current
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1
        self.counter.label = f"{self.current + 1}/{len(self.pages)}"

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


# ── Admin UI ───────────────────────────────────────────────────

class AddStreamerModal(ui.Modal, title="Add Streamer"):
    username = ui.TextInput(label="Twitch Username", placeholder="e.g. gronkh", max_length=50)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            from app.services.subscription import add_streamer
            result = add_streamer(self.username.value, db)
            embed = discord.Embed(color=SUCCESS)
            embed.description = (
                f"```diff\n+ Added {result['display_name']}\n```\n"
                f"Login: `{result['login']}`  ·  ID: `{result['id']}`\n"
                f"Status: {'◉ Live' if result['is_live'] else '○ Offline'}"
            )
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
            remove_streamer(self.username.value, db)
            embed = discord.Embed(color=BG_DARK)
            embed.description = f"```diff\n- Removed {self.username.value}\n```"
            await interaction.followup.send(embed=embed, ephemeral=True)
        except (ValueError, Exception) as e:
            await interaction.followup.send(f"```diff\n- {e}\n```", ephemeral=True)
        finally:
            db.close()


class AdminSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Add Streamer", value="add", description="Track a new streamer", emoji="➕"),
            discord.SelectOption(label="Remove Streamer", value="remove", description="Stop tracking a streamer", emoji="➖"),
            discord.SelectOption(label="List Streamers", value="list", description="Show all tracked streamers", emoji="📋"),
            discord.SelectOption(label="Sync & Reconcile", value="sync", description="Sync subs and reconcile states", emoji="🔄"),
        ]
        super().__init__(placeholder="Select an action...", options=options)

    async def callback(self, interaction):
        choice = self.values[0]

        if choice == "add":
            await interaction.response.send_modal(AddStreamerModal())

        elif choice == "remove":
            await interaction.response.send_modal(RemoveStreamerModal())

        elif choice == "list":
            db = SessionLocal()
            try:
                streamers = db.query(Streamer).all()
                if not streamers:
                    await interaction.response.send_message("No streamers tracked.", ephemeral=True)
                    return
                t = "```\n"
                t += f" {'Status':<8} {'Name':<18} {'Login':<16} {'ID'}\n"
                t += f" {'─'*8} {'─'*18} {'─'*16} {'─'*12}\n"
                for s in streamers:
                    st = "◉ Live" if s.is_live else "○ Off"
                    t += f" {st:<8} {s.display_name:<18} {s.login:<16} {s.id}\n"
                t += "```"
                embed = discord.Embed(title=f"Tracked Streamers  ·  {len(streamers)}", description=t, color=PURPLE)
                await interaction.response.send_message(embed=embed, ephemeral=True)
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
                embed = discord.Embed(title="Sync & Reconcile", color=PURPLE)
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
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(AdminSelect())


# ── Bot ────────────────────────────────────────────────────────

class StreamTrackerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
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
        #await self._send_test_notifications()

    async def _send_test_notifications(self):
        channel_id = settings.discord_notification_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if not channel:
            return
        live = LiveNotification(
            streamer_login="teststreamer",
            streamer_display_name="TestStreamer",
            profile_image_url=TEST_PFP,
            game_name="Just Chatting",
            title="Testing the Stream Tracker Bot!",
            thumbnail_url=TEST_PREVIEW,
            started_at="2026-02-17T12:00:00Z",
            twitch_url="https://twitch.tv/teststreamer",
        )
        await channel.send(content="` ── Test: Live ──── `", embed=self._build_live_embed(live))
        await asyncio.sleep(1)
        offline = OfflineNotification(
            streamer_login="teststreamer",
            streamer_display_name="TestStreamer",
            profile_image_url=TEST_PFP,
            duration_minutes=263,
            points_awarded=[("stream_time", 2630), ("daily_bonus", 500), ("streak_bonus", 300)],
            total_points=14580,
            twitch_url="https://twitch.tv/teststreamer",
        )
        await channel.send(content="` ── Test: Offline ── `", embed=self._build_offline_embed(offline))
        print("📌 Test notifications sent")

    async def _notification_listener(self):
        await self.wait_until_ready()
        channel_id = settings.discord_notification_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if not channel:
            return
        print(f"📡 Listening on #{channel.name}")
        while True:
            try:
                n = await notification_queue.get()
                if isinstance(n, LiveNotification):
                    await channel.send(embed=self._build_live_embed(n))
                elif isinstance(n, OfflineNotification):
                    await channel.send(embed=self._build_offline_embed(n))
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Notification error: {e}")

    def _build_live_embed(self, n):
        embed = discord.Embed(color=ACCENT)
        embed.set_author(
            name=f"{n.streamer_display_name} is now live",
            url=n.twitch_url,
            icon_url=n.profile_image_url if n.profile_image_url else None,
        )
        desc = ""
        if n.game_name:
            desc += f"**{n.game_name}**\n"
        if n.title:
            desc += f"```{n.title}```"
        desc += f"\n[Watch on Twitch  →]({n.twitch_url})"
        embed.description = desc
        if n.thumbnail_url:
            embed.set_image(url=n.thumbnail_url)
        if n.profile_image_url:
            embed.set_thumbnail(url=n.profile_image_url)
        embed.set_footer(text=f"twitch.tv/{n.streamer_login}")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    def _build_offline_embed(self, n):
        h, m = divmod(n.duration_minutes, 60)
        dur = f"{h}h {m}m" if h else f"{m}m"
        embed = discord.Embed(color=DARK)
        embed.set_author(
            name=f"{n.streamer_display_name} went offline",
            icon_url=n.profile_image_url if n.profile_image_url else None,
        )
        lines = [f"Duration: **{dur}**\n"]
        if n.points_awarded:
            lines.append("```")
            for reason, pts in n.points_awarded:
                label = reason.replace("_", " ").title()
                lines.append(f"  +{pts:>6,}  {label}")
            lines.append(f"  {'─' * 22}")
            lines.append(f"  {n.total_points:>7,}  Total")
            lines.append("```")
        embed.description = "\n".join(lines)
        if n.profile_image_url:
            embed.set_thumbnail(url=n.profile_image_url)
        embed.set_footer(text=f"twitch.tv/{n.streamer_login}")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    # ── Build Live Page Embed ──────────────────────────────────

    def _build_live_page(self, streamer, open_stream, pts, db):
        """Build an embed for a single live streamer page."""
        now = datetime.now(timezone.utc)
        embed = discord.Embed(color=ACCENT)

        uptime = "—"
        if open_stream:
            started = open_stream.started_at.replace(tzinfo=timezone.utc)
            delta = now - started
            uptime = fmt_dur(int(delta.total_seconds() / 60))

        embed.set_author(
            name=f"{streamer.display_name}  ·  Live",
            url=f"https://twitch.tv/{streamer.login}",
            icon_url=streamer.profile_image_url or None,
        )

        game = None
        title = None
        thumbnail = None
        try:
            from app.integrations.twitch import TwitchAPI
            twitch = TwitchAPI()
            info = twitch.get_stream_info(streamer.id)
            if info:
                game = info.get("game_name")
                title = info.get("title")
                thumbnail = info.get("thumbnail_url")
        except Exception:
            pass

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
        embed.timestamp = now
        return embed

    # ── Slash Commands ─────────────────────────────────────────

    def _register_commands(self):

        @self.tree.error
        async def on_error(interaction, error):
            print(f"❌ Command error: {error}")
            import traceback
            traceback.print_exception(type(error), error, error.__traceback__)
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred.", ephemeral=True)

        # ── /live ──

        @self.tree.command(name="live", description="Who is currently streaming")
        async def cmd_live(interaction):
            await interaction.response.defer()
            db = SessionLocal()
            try:
                streamers = db.query(Streamer).all()
                live = [s for s in streamers if s.is_live]

                if not live:
                    embed = discord.Embed(
                        title="Live Streamers",
                        description="```\n  No one is streaming right now.\n```",
                        color=EMBED_BG,
                    )
                    embed.set_footer(text="Stream Tracker")
                    embed.timestamp = datetime.now(timezone.utc)
                    await interaction.followup.send(embed=embed)
                    return

                if len(live) == 1:
                    s = live[0]
                    open_stream = db.query(Stream).filter(Stream.streamer_id == s.id, Stream.ended_at.is_(None)).first()
                    pts = db.query(func.sum(PointTransaction.points)).filter(
                        PointTransaction.streamer_id == s.id).scalar() or 0
                    embed = self._build_live_page(s, open_stream, pts, db)
                    await interaction.followup.send(embed=embed)
                    return

                pages = []
                for s in live:
                    open_stream = db.query(Stream).filter(Stream.streamer_id == s.id, Stream.ended_at.is_(None)).first()
                    pts = db.query(func.sum(PointTransaction.points)).filter(
                        PointTransaction.streamer_id == s.id).scalar() or 0
                    pages.append(self._build_live_page(s, open_stream, pts, db))

                view = LivePaginatorView(pages)
                await interaction.followup.send(embed=pages[0], view=view)
            finally:
                db.close()

        # ── /leaderboard ──

        @self.tree.command(name="leaderboard", description="Points leaderboard")
        async def cmd_leaderboard(interaction):
            db = SessionLocal()
            try:
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

                max_pts = results[0].pts if results else 1
                medals = ["1st", "2nd", "3rd"]
                total_pts = 0

                t = "```\n"
                t += f" {'Rank':<6} {'Streamer':<18} {'Points':>10} {'Hours':>7} {'Streams':>8}\n"
                t += f" {'─'*6} {'─'*18} {'─'*10} {'─'*7} {'─'*8}\n"
                for i, r in enumerate(results):
                    rank = medals[i] if i < 3 else f"{i+1}th"
                    st = "◉" if r.is_live else " "
                    mins = get_streamer_total_minutes(r.sid, db, now)
                    streams = get_all_streams_count(r.sid, db)
                    t += f" {rank:<6}{st} {r.display_name:<17} {r.pts:>10,} {round(mins/60,1):>6}h {streams:>8}\n"
                    total_pts += r.pts
                t += "```"

                bar_lines = []
                for i, r in enumerate(results):
                    m = ["🥇", "🥈", "🥉"]
                    prefix = m[i] if i < 3 else f"` {i+1}.`"
                    b = bar(r.pts, max_pts, 16)
                    bar_lines.append(f"{prefix} `{b}` **{r.pts:,}**")

                embed = discord.Embed(title="Leaderboard", color=GOLD)
                embed.description = t + "\n" + "\n".join(bar_lines)

                total_min = get_global_total_minutes(db, now)
                total_str = get_global_stream_count(db)
                embed.set_footer(text=f"{total_pts:,} pts  ·  {total_str} streams  ·  {round(total_min/60,1)}h")
                embed.timestamp = now
                await interaction.response.send_message(embed=embed)
            finally:
                db.close()

        # ── /streamer ──

        @self.tree.command(name="streamer", description="Stats for a specific streamer")
        @app_commands.describe(name="Twitch username")
        async def cmd_streamer(interaction, name: str):
            db = SessionLocal()
            try:
                now = datetime.now(timezone.utc)
                s = db.query(Streamer).filter(Streamer.login == name.lower()).first()
                if not s:
                    await interaction.response.send_message(f"Streamer `{name}` not found.", ephemeral=True)
                    return

                # All streams (completed + open)
                all_streams = db.query(Stream).filter(Stream.streamer_id == s.id).all()
                completed = [x for x in all_streams if x.duration_minutes is not None]
                open_stream = next((x for x in all_streams if x.ended_at is None), None)

                # Total minutes including live
                total_min = get_streamer_total_minutes(s.id, db, now)
                total_count = len(all_streams)

                # Longest: check completed + current live
                longest_completed = max((x.duration_minutes for x in completed), default=0)
                longest_live = get_live_minutes(open_stream, now) if open_stream else 0
                longest_min = max(longest_completed, longest_live)

                total_pts = db.query(func.sum(PointTransaction.points)).filter(PointTransaction.streamer_id == s.id).scalar() or 0

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
                    color=ACCENT if is_live else DARK,
                )

                if is_live and open_stream:
                    started = open_stream.started_at.replace(tzinfo=timezone.utc)
                    delta = now - started
                    embed.description = f"```diff\n+ LIVE  ·  {fmt_dur(int(delta.total_seconds()/60))} uptime\n```"
                elif is_live:
                    embed.description = "```diff\n+ LIVE\n```"
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
                overview += f"  Total Time   {round(total_min/60,1):>9}h\n"
                overview += f"  Avg Stream   {avg:>9}h\n"
                overview += f"  Longest      {longest_str:>10}\n"
                overview += f"  Streak       {streak_str:>10}\n"
                overview += "```"
                embed.add_field(name="Overview", value=overview, inline=False)

                embed.add_field(
                    name="Last 7 Days",
                    value=f"`{len(recent)}` streams  ·  `{round(recent_min/60,1)}h`",
                    inline=False,
                )

                if breakdown:
                    bd = "```\n"
                    for r in breakdown:
                        label = r.reason.replace("_", " ").title()
                        bd += f"  {label:<16} {r.pts:>8,}\n"
                    bd += "```"
                    embed.add_field(name="Points Breakdown", value=bd, inline=False)

                embed.set_footer(text=f"twitch.tv/{s.login}  ·  Stream Tracker")
                embed.timestamp = now
                await interaction.response.send_message(embed=embed)
            finally:
                db.close()

        # ── /stats ──

        @self.tree.command(name="stats", description="Global tracking statistics")
        async def cmd_stats(interaction):
            db = SessionLocal()
            try:
                now = datetime.now(timezone.utc)
                streamers = db.query(Streamer).all()
                live = [s for s in streamers if s.is_live]
                total_streams = get_global_stream_count(db)
                total_min = get_global_total_minutes(db, now)
                total_pts = db.query(func.sum(PointTransaction.points)).scalar() or 0

                embed = discord.Embed(title="Stream Tracker", color=PURPLE)
                t = "```\n"
                t += f"  Streamers     {len(streamers):>8}\n"
                t += f"  Live Now      {len(live):>8}\n"
                t += f"  Streams       {total_streams:>8}\n"
                t += f"  Hours         {round(total_min/60,1):>7}h\n"
                t += f"  Points        {total_pts:>8,}\n"
                t += "```"
                embed.description = t

                if live:
                    live_text = "\n".join(f"◉  [{s.display_name}](https://twitch.tv/{s.login})" for s in live)
                    embed.add_field(name="Currently Live", value=live_text, inline=False)

                embed.set_footer(text="Stream Tracker")
                embed.timestamp = now
                await interaction.response.send_message(embed=embed)
            finally:
                db.close()

        # ── /admin ──

        @self.tree.command(name="admin", description="Stream Tracker admin panel")
        async def cmd_admin(interaction):
            if not settings.discord_admin_role_id:
                await interaction.response.send_message("Admin role not configured.", ephemeral=True)
                return
            if not any(role.id == settings.discord_admin_role_id for role in interaction.user.roles):
                await interaction.response.send_message("No permission.", ephemeral=True)
                return
            embed = discord.Embed(title="Admin Panel", description="Select an action below.", color=PURPLE)
            await interaction.response.send_message(embed=embed, view=AdminView(), ephemeral=True)


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