from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from app.database.database import SessionLocal
from app.services.reconciliation import reconcile_live_states
from app.services.points import award_live_points, LIVE_POINTS_INTERVAL_MINUTES, EVENT_MULTIPLIER
from app.services.roles import sync_leaderboard_roles
from app.integrations.twitch import twitch_api
from app.database.models import Streamer
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


@lru_cache()
def _backup_timezone():
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(settings.backup_timezone)
    except Exception:
        logger.warning("Ungültige backup_timezone=%s, fallback auf UTC", settings.backup_timezone, exc_info=True)
        return timezone.utc


def _backup_now(utc_now: datetime | None = None) -> datetime:
    """Return the current time in the backup timezone."""
    utc_now = utc_now or datetime.now(timezone.utc)
    return utc_now.astimezone(_backup_timezone())


def _backup_today(utc_now: datetime | None = None) -> date:
    return _backup_now(utc_now).date()


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
    now = _backup_now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
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
    with open(_last_backup_path(), "w") as f:
        f.write(_backup_today().isoformat())


async def _perform_backup() -> bool:
    """Perform database backup locally and/or send to Discord. Returns True if successful."""
    import os
    import shutil
    import glob

    db_url = settings.database_url
    if "sqlite" not in db_url:
        logger.warning("Backups werden nur für SQLite-Datenbanken unterstützt (database_url=%s)", db_url)
        return False

    db_path = db_url.replace("sqlite:///", "", 1)
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.getcwd(), db_path)

    if not os.path.exists(db_path):
        logger.error("Backup fehlgeschlagen: DB nicht gefunden unter %s", db_path)
        return False

    local_success = False
    discord_success = False

    # 1. Local backup copy
    if settings.backup_storage_path:
        try:
            os.makedirs(settings.backup_storage_path, exist_ok=True)
            today_str = _backup_today().isoformat()
            backup_filename = f"stream_tracker_{today_str}.db"
            local_backup_path = os.path.join(settings.backup_storage_path, backup_filename)

            # Copy database file
            shutil.copy2(db_path, local_backup_path)
            logger.info("Lokales Backup erstellt unter %s", local_backup_path)
            local_success = True

            # Rotation: keep last 7 backups matching stream_tracker_*.db
            pattern = os.path.join(settings.backup_storage_path, "stream_tracker_*.db")
            backup_files = sorted(glob.glob(pattern))
            if len(backup_files) > 7:
                files_to_delete = backup_files[:-7]
                for f in files_to_delete:
                    try:
                        os.remove(f)
                        logger.info("Altes Backup gelöscht: %s", f)
                    except Exception as e:
                        logger.warning("Fehler beim Löschen des alten Backups %s: %s", f, e)
        except Exception:
            logger.exception("Lokales Backup fehlgeschlagen")

    # 2. Discord backup
    if settings.discord_backup_channel_id:
        try:
            import discord
            from app.integrations.discord_bot import bot

            await bot.wait_until_ready()

            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            if size_mb > 24:
                logger.error("Discord-Backup fehlgeschlagen: DB zu groß (%.1f MB)", size_mb)
            else:
                channel = bot.get_channel(settings.discord_backup_channel_id) or await bot.fetch_channel(settings.discord_backup_channel_id)
                await channel.send(
                    content=f"Daily backup · `{size_mb:.2f} MB`",
                    file=discord.File(db_path, filename="stream_tracker.db"),
                )
                logger.info("Backup an Discord-Channel %s gesendet", settings.discord_backup_channel_id)
                discord_success = True
        except Exception:
            logger.exception("Discord-Backup fehlgeschlagen")

    # Success if at least one target succeeded.
    # Note: If neither is configured, we return False to avoid saving a .last_backup.
    return local_success or discord_success


async def periodic_backup():
    """Daily: send SQLite DB backup at the configured hour. Catches up missed backups after restarts."""
    logger.info("Scheduler-Task gestartet: backup (hour=%s, tz=%s)", settings.backup_hour, settings.backup_timezone)

    # On startup: check if today's backup was already sent
    last = _last_backup_date()
    today = _backup_today().isoformat()
    already_past = _backup_now().hour >= settings.backup_hour

    if last != today and already_past:
        logger.info("Verpasstes Backup erkannt, starte jetzt")
        try:
            if await _perform_backup():
                _save_last_backup_date()
        except Exception as e:
            logger.exception("Catch-up-Backup fehlgeschlagen: %s", e)

    while True:
        secs = _seconds_until_hour(settings.backup_hour)
        logger.info(
            "Nächstes Backup in %.1fh (um %02d:00 %s)",
            secs / 3600,
            settings.backup_hour,
            settings.backup_timezone,
        )
        await asyncio.sleep(secs)
        logger.info("Tägliches Backup gestartet")
        try:
            if await _perform_backup():
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
