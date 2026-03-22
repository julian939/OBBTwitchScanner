from __future__ import annotations

import random
from datetime import datetime, timezone

import discord
from sqlalchemy import func

from app.config import get_settings
from app.database.models import Stream

settings = get_settings()


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


def find_streamer(name: str, db, guild=None):
    """Resolve a name to a Streamer. Tries Twitch login, display name, then Discord member name."""
    from app.database.models import Streamer
    s = db.query(Streamer).filter(Streamer.login == name.lower()).first()
    if s:
        return s
    s = db.query(Streamer).filter(Streamer.display_name.ilike(name)).first()
    if s:
        return s
    if guild:
        member = guild.get_member_named(name)
        if member:
            s = db.query(Streamer).filter(Streamer.discord_id == str(member.id)).first()
    return s


def is_admin(interaction: discord.Interaction) -> bool:
    user_role_ids = {role.id for role in interaction.user.roles}
    if not user_role_ids and interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        if member:
            user_role_ids = {role.id for role in member.roles}
    if settings.discord_admin_role_ids and user_role_ids & set(settings.discord_admin_role_ids):
        return True
    return bool(settings.discord_admin_user_ids and interaction.user.id in settings.discord_admin_user_ids)


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
