from __future__ import annotations

import asyncio

import discord

from app.config import get_settings
from app.database.database import SessionLocal
from app.database.models import Streamer
from app.services.notification import notification_queue, LiveNotification, OfflineNotification
from app.integrations.discord.constants import ACCENT, OFFLINE_GRAY
from app.integrations.discord.helpers import cut_title, _square_thumbnail, get_game_channel
from app.integrations.discord.views import LiveLinkView

settings = get_settings()

_MAX_SEND_ATTEMPTS = 3
_BASE_RETRY_DELAY = 2


async def _send_with_retry(channel, **kwargs):
    for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
        try:
            await channel.send(**kwargs)
            return
        except discord.errors.DiscordServerError:
            if attempt == _MAX_SEND_ATTEMPTS:
                raise
            delay = _BASE_RETRY_DELAY * (2 ** (attempt - 1))
            print(f"⚠️ Discord 5xx error, retry {attempt}/{_MAX_SEND_ATTEMPTS} in {delay}s")
            await asyncio.sleep(delay)
        except discord.errors.HTTPException as e:
            if e.status == 429 and attempt < _MAX_SEND_ATTEMPTS:
                delay = getattr(e, 'retry_after', _BASE_RETRY_DELAY * (2 ** (attempt - 1)))
                print(f"⚠️ Rate limited, retry {attempt}/{_MAX_SEND_ATTEMPTS} in {delay}s")
                await asyncio.sleep(delay)
            else:
                raise
        f = kwargs.get("file")
        if f is not None:
            f.reset(seek=True)


async def notification_listener(bot_instance):
    await bot_instance.wait_until_ready()
    print(f"📡 Notification listener ready")
    while True:
        try:
            n = await notification_queue.get()
            channel = get_game_channel(bot_instance, n.game_name)
            if not channel:
                print(f"⚠️ No channel configured for '{n.game_name}', skipping notification")
                continue

            if isinstance(n, LiveNotification):
                discord_id = None
                try:
                    db = SessionLocal()
                    streamer = db.query(Streamer).filter(Streamer.id == n.streamer_id).first()
                    if streamer and streamer.discord_id:
                        discord_id = streamer.discord_id
                    db.close()
                except Exception:
                    pass
                embed, thumb_file = await build_live_embed(n, bot_instance, discord_id=discord_id)
                kwargs = {"embed": embed, "view": LiveLinkView(n.twitch_url)}
                if thumb_file:
                    kwargs["file"] = thumb_file
                await _send_with_retry(channel, **kwargs)
            elif isinstance(n, OfflineNotification):
                await _send_with_retry(channel, embed=build_offline_embed(n))
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"❌ Notification error: {e}")
            import traceback
            traceback.print_exc()


async def build_live_embed(n, bot_instance, discord_id=None):
    author = n.streamer_login
    if discord_id:
        guild = bot_instance.get_guild(settings.discord_guild_id)
        member = guild.get_member(int(discord_id)) if guild else None
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


def build_offline_embed(n):
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
        name=f"{n.streamer_login} went offline",
        icon_url=n.profile_image_url if n.profile_image_url else None,
    )
    desc = ""
    if pts_line:
        desc += f"{pts_line}\n"
    embed.set_footer(text=f"Duration: {dur}  ·  " + desc)
    return embed
