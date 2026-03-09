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