from __future__ import annotations

from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from sqlalchemy import func

from app.database.database import SessionLocal
from app.database.models import Streamer, Stream, PointTransaction
from app.integrations.discord.constants import ACCENT
from app.integrations.discord.helpers import (
    fmt_dur,
    get_streamer_total_minutes,
    get_live_minutes,
    random_tip,
    find_streamer,
)


def register(bot, tree):
    @tree.command(name="streamer", description="Stats for a specific streamer")
    @app_commands.describe(name="Twitch or Discord Name", public="Show the response to everyone")
    async def cmd_streamer(interaction, name: str, public: bool = False):
        ephemeral = not public
        await interaction.response.defer(ephemeral=ephemeral)
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            s = find_streamer(name, db, interaction.guild)
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

            title = s.login
            if s.discord_id:
                guild = interaction.guild
                if guild:
                    member = guild.get_member(int(s.discord_id))
                    if member:
                        title = f"{s.login} ({member.display_name})"

            embed = discord.Embed(
                title=title,
                url=f"https://twitch.tv/{s.login}",
                color=ACCENT,
            )

            if s.is_locked:
                embed.description = "```diff\n- LOCKED (admin)\n```"
            elif is_live:
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
