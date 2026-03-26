from __future__ import annotations

import asyncio
from app.database.database import SessionLocal
from app.services.reconciliation import reconcile_live_states
from app.services.points import award_live_points, LIVE_POINTS_INTERVAL_MINUTES, EVENT_MULTIPLIER
from app.services.roles import sync_leaderboard_roles
from app.config import get_settings

settings = get_settings()


async def periodic_reconciliation():
    interval = settings.reconciliation_interval_minutes * 60
    while True:
        await asyncio.sleep(interval)
        print("🔄 Running periodic reconciliation...")
        db = SessionLocal()
        try:
            result = reconcile_live_states(db)
            print(f"✅ Reconciliation done: {result}")

            # Sync leaderboard roles after streams close (points change)
            await sync_leaderboard_roles(db)
        except Exception as e:
            print(f"❌ Reconciliation failed: {e}")
        finally:
            db.close()


async def periodic_name_refresh():
    """Daily: refresh login/display_name/profile_image_url for all streamers via Twitch ID."""
    interval = 24 * 60 * 60
    while True:
        await asyncio.sleep(interval)
        print("🔁 Running daily name refresh...")
        from app.integrations.twitch import twitch_api
        from app.database.models import Streamer
        db = SessionLocal()
        try:
            streamers = db.query(Streamer).all()
            updated = 0
            for streamer in streamers:
                data = twitch_api.get_user_by_id(streamer.id)
                if not data:
                    continue
                changed = False
                if streamer.login != data["login"]:
                    print(f"  🔄 Login: {streamer.login} → {data['login']}")
                    streamer.login = data["login"]
                    changed = True
                if streamer.display_name != data["display_name"]:
                    print(f"  🔄 Display: {streamer.display_name} → {data['display_name']}")
                    streamer.display_name = data["display_name"]
                    changed = True
                if data.get("profile_image_url") and streamer.profile_image_url != data["profile_image_url"]:
                    streamer.profile_image_url = data["profile_image_url"]
                    changed = True
                if changed:
                    updated += 1
            db.commit()
            print(f"✅ Name refresh done: {updated}/{len(streamers)} updated")
        except Exception as e:
            print(f"❌ Name refresh failed: {e}")
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
        return None


def _save_last_backup_date():
    from datetime import date
    open(_last_backup_path(), "w").write(date.today().isoformat())


async def _send_backup():
    """Send the SQLite DB file to the configured Discord backup channel."""
    import os
    import discord

    if not settings.discord_backup_channel_id:
        print("⚠️ Backup skipped: discord_backup_channel_id not configured")
        return

    db_url = settings.database_url
    db_path = db_url.replace("sqlite:///", "", 1)
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.getcwd(), db_path)

    if not os.path.exists(db_path):
        print(f"❌ Backup failed: DB not found at {db_path}")
        return

    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    if size_mb > 24:
        print(f"❌ Backup failed: DB too large ({size_mb:.1f} MB)")
        return

    from app.integrations.discord_bot import bot
    try:
        channel = bot.get_channel(settings.discord_backup_channel_id) or await bot.fetch_channel(settings.discord_backup_channel_id)
    except Exception:
        print(f"❌ Backup failed: channel {settings.discord_backup_channel_id} not found")
        return

    await channel.send(
        content=f"Daily backup · `{size_mb:.2f} MB`",
        file=discord.File(db_path, filename="stream_tracker.db"),
    )
    print(f"✅ Backup sent to channel {settings.discord_backup_channel_id}")


async def periodic_backup():
    """Daily: send SQLite DB backup at the configured hour. Catches up missed backups after restarts."""
    from datetime import date

    # On startup: check if today's backup was already sent
    last = _last_backup_date()
    today = date.today().isoformat()
    import datetime as dt
    already_past = dt.datetime.now().hour >= settings.backup_hour

    if last != today and already_past:
        print("💾 Missed backup detected — sending now...")
        try:
            await _send_backup()
            _save_last_backup_date()
        except Exception as e:
            print(f"❌ Catch-up backup failed: {e}")

    while True:
        secs = _seconds_until_hour(settings.backup_hour)
        print(f"💾 Next backup in {secs/3600:.1f}h (at {settings.backup_hour:02d}:00)")
        await asyncio.sleep(secs)
        print("💾 Running daily backup...")
        try:
            await _send_backup()
            _save_last_backup_date()
        except Exception as e:
            print(f"❌ Backup failed: {e}")


async def periodic_live_points():
    interval = LIVE_POINTS_INTERVAL_MINUTES * 60
    while True:
        await asyncio.sleep(interval)
        db = SessionLocal()
        try:
            # Check if Discord event is active
            from app.integrations.discord_bot import bot
            multiplier = EVENT_MULTIPLIER if bot.has_active_event() else 1

            awarded = award_live_points(db, multiplier=multiplier)
            if awarded > 0:
                suffix = f" (x{multiplier} event bonus!)" if multiplier > 1 else ""
                print(f"💰 Live points awarded: {awarded}{suffix}")

                # Sync leaderboard roles after points awarded
                await sync_leaderboard_roles(db)
        except Exception as e:
            print(f"❌ Live points failed: {e}")
        finally:
            db.close()