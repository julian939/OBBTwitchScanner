from __future__ import annotations

import asyncio
import logging
from app.database.database import SessionLocal
from app.services.reconciliation import reconcile_live_states
from app.services.points import award_live_points, LIVE_POINTS_INTERVAL_MINUTES, EVENT_MULTIPLIER
from app.services.roles import sync_leaderboard_roles
from app.integrations.twitch import twitch_api
from app.database.models import Streamer
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def periodic_reconciliation():
    interval = settings.reconciliation_interval_minutes * 60
    logger.info("Scheduler-Task gestartet: reconciliation (%sm)", settings.reconciliation_interval_minutes)
    while True:
        await asyncio.sleep(interval)
        logger.info("Periodische Reconciliation gestartet")
        db = SessionLocal()
        try:
            result = reconcile_live_states(db)
            logger.info("Periodische Reconciliation abgeschlossen: %s", result)

            # Sync leaderboard roles after streams close (points change)
            await sync_leaderboard_roles(db)
        except Exception as e:
            logger.exception("Periodische Reconciliation fehlgeschlagen: %s", e)
        finally:
            db.close()


async def periodic_name_refresh():
    """Daily: refresh login/display_name/profile_image_url for all streamers via Twitch ID."""
    interval = 24 * 60 * 60
    logger.info("Scheduler-Task gestartet: name_refresh")
    while True:
        await asyncio.sleep(interval)
        from app.integrations.twitch import twitch_api
        from app.database.models import Streamer
        db = SessionLocal()
        try:
            logger.info("Täglicher Name-Refresh gestartet")
            streamers = db.query(Streamer).all()
            updated = 0
            for streamer in streamers:
                data = twitch_api.get_user_by_id(streamer.id)
                if not data:
                    continue
                changed = False
                if streamer.login != data["login"]:
                    logger.info("Login geändert: %s -> %s", streamer.login, data["login"])
                    streamer.login = data["login"]
                    changed = True
                if streamer.display_name != data["display_name"]:
                    logger.info("Display-Name geändert: %s -> %s", streamer.display_name, data["display_name"])
                    streamer.display_name = data["display_name"]
                    changed = True
                if data.get("profile_image_url") and streamer.profile_image_url != data["profile_image_url"]:
                    streamer.profile_image_url = data["profile_image_url"]
                    changed = True
                if changed:
                    updated += 1
            db.commit()
            logger.info("Name-Refresh abgeschlossen: %s/%s aktualisiert", updated, len(streamers))
        except Exception as e:
            logger.exception("Name-Refresh fehlgeschlagen: %s", e)
        finally:
            db.close()


def _last_backup_path() -> str:
    import os
    directory = settings.backup_storage_path
    if directory:
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, ".last_backup")
    return ".last_backup"


def _seconds_until_hour(hour: int) -> float:
    """Return seconds until the next occurrence of the given hour (today or tomorrow)."""
    from datetime import datetime
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        # Already past today's target — aim for tomorrow
        from datetime import timedelta
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _last_backup_date() -> str | None:
    """Return the date string of the last successful backup, or None."""
    import os
    path = _last_backup_path()
    if not os.path.exists(path):
        return None
    try:
        return open(path).read().strip()
    except Exception:
        logger.debug("Konnte .last_backup nicht lesen", exc_info=True)
        return None


def _save_last_backup_date():
    from datetime import date
    open(_last_backup_path(), "w").write(date.today().isoformat())


async def _send_backup() -> bool:
    """Send the SQLite DB file to the configured Discord backup channel. Returns True on success."""
    import os
    import discord

    if not settings.discord_backup_channel_id:
        logger.warning("Backup übersprungen: discord_backup_channel_id nicht konfiguriert")
        return False

    from app.integrations.discord_bot import bot
    await bot.wait_until_ready()

    db_url = settings.database_url
    db_path = db_url.replace("sqlite:///", "", 1)
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.getcwd(), db_path)

    if not os.path.exists(db_path):
        logger.error("Backup fehlgeschlagen: DB nicht gefunden unter %s", db_path)
        return False

    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    if size_mb > 24:
        logger.error("Backup fehlgeschlagen: DB zu groß (%.1f MB)", size_mb)
        return False

    try:
        channel = bot.get_channel(settings.discord_backup_channel_id) or await bot.fetch_channel(settings.discord_backup_channel_id)
    except Exception:
        logger.exception("Backup fehlgeschlagen: Channel %s nicht gefunden", settings.discord_backup_channel_id)
        return False

    await channel.send(
        content=f"Daily backup · `{size_mb:.2f} MB`",
        file=discord.File(db_path, filename="stream_tracker.db"),
    )
    logger.info("Backup an Channel %s gesendet", settings.discord_backup_channel_id)
    return True


async def periodic_backup():
    """Daily: send SQLite DB backup at the configured hour. Catches up missed backups after restarts."""
    from datetime import date
    logger.info("Scheduler-Task gestartet: backup (hour=%s)", settings.backup_hour)

    # On startup: check if today's backup was already sent
    last = _last_backup_date()
    today = date.today().isoformat()
    import datetime as dt
    already_past = dt.datetime.now().hour >= settings.backup_hour

    if last != today and already_past:
        logger.info("Verpasstes Backup erkannt, sende jetzt")
        try:
            if await _send_backup():
                _save_last_backup_date()
        except Exception as e:
            logger.exception("Catch-up-Backup fehlgeschlagen: %s", e)

    while True:
        secs = _seconds_until_hour(settings.backup_hour)
        logger.info("Nächstes Backup in %.1fh (um %02d:00)", secs / 3600, settings.backup_hour)
        await asyncio.sleep(secs)
        logger.info("Tägliches Backup gestartet")
        try:
            if await _send_backup():
                _save_last_backup_date()
        except Exception as e:
            logger.exception("Backup fehlgeschlagen: %s", e)


async def periodic_live_points():
    interval = LIVE_POINTS_INTERVAL_MINUTES * 60
    logger.info("Scheduler-Task gestartet: live_points (%sm)", LIVE_POINTS_INTERVAL_MINUTES)
    while True:
        await asyncio.sleep(interval)
        db = SessionLocal()
        try:
            # Check if Discord event is active
            from app.integrations.discord_bot import bot
            event_mult = EVENT_MULTIPLIER if bot.has_active_event() else 1

            # Fetch viewer counts for all live streamers
            live_streamer_ids = [
                s.id for s in db.query(Streamer).filter(Streamer.is_live == True).all()
            ]
            try:
                viewer_counts = twitch_api.get_viewer_counts(live_streamer_ids) if live_streamer_ids else {}
            except Exception as e:
                logger.warning("Viewer-Counts konnten nicht geladen werden: %s", e)
                viewer_counts = {}

            awarded = award_live_points(db, event_multiplier=event_mult, viewer_counts=viewer_counts)
            if awarded > 0:
                suffix = f" (x{event_mult} event bonus!)" if event_mult > 1 else ""
                logger.info("Live-Punkte vergeben: %s%s", awarded, suffix)

                # Sync leaderboard roles after points awarded
                await sync_leaderboard_roles(db)
        except Exception as e:
            logger.exception("Live-Punkte fehlgeschlagen: %s", e)
        finally:
            db.close()
