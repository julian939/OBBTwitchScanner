from __future__ import annotations

import asyncio
from app.database.database import SessionLocal
from app.services.reconciliation import reconcile_live_states
from app.services.points import award_live_points, LIVE_POINTS_INTERVAL_MINUTES, EVENT_MULTIPLIER
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
        except Exception as e:
            print(f"❌ Reconciliation failed: {e}")
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
        except Exception as e:
            print(f"❌ Live points failed: {e}")
        finally:
            db.close()