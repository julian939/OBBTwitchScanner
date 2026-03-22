from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from sqlalchemy import func

from app.config import get_settings
from app.database.database import SessionLocal
from app.database.models import Streamer, Stream, PointTransaction
from app.integrations.discord.constants import ACCENT
from app.integrations.discord.helpers import fmt_dur, random_tip
from app.integrations.discord.views import PaginatorView

settings = get_settings()


def build_live_page(streamer, open_stream, pts, db, stream_info=None, bot=None):
    now = datetime.now(timezone.utc)
    embed = discord.Embed(color=ACCENT)

    uptime = "—"
    if open_stream:
        started = open_stream.started_at.replace(tzinfo=timezone.utc)
        delta = now - started
        uptime = fmt_dur(int(delta.total_seconds() / 60))

    author_name = streamer.login
    if streamer.discord_id and bot:
        guild = bot.get_guild(settings.discord_guild_id)
        if guild:
            member = guild.get_member(int(streamer.discord_id))
            if member:
                author_name = f"{streamer.login} ({member.display_name})"

    embed.set_author(
        name=f"OBB Live  ·  {author_name}",
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
    return embed


def register(bot, tree):
    @tree.command(name="live", description="Who is currently streaming")
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
                if s.is_locked:
                    continue
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
                embed = build_live_page(s, open_stream, pts, db, stream_info=info, bot=bot)
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                return

            pages = []
            for s, info in tracked_live:
                open_stream = db.query(Stream).filter(Stream.streamer_id == s.id, Stream.ended_at.is_(None)).first()
                pts = db.query(func.sum(PointTransaction.points)).filter(
                    PointTransaction.streamer_id == s.id).scalar() or 0
                pages.append(build_live_page(s, open_stream, pts, db, stream_info=info, bot=bot))

            view = PaginatorView(pages)
            msg = await interaction.followup.send(embed=pages[0], view=view, ephemeral=ephemeral)
            view.message = msg
        finally:
            db.close()
