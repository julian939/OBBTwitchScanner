from __future__ import annotations

import asyncio
from app.database.database import SessionLocal
from app.services.reconciliation import reconcile_live_states
from app.services.points import award_live_points, LIVE_POINTS_INTERVAL_MINUTES
from app.config import get_settings

settings = get_settings()


async def periodic_reconciliation():
    """Run reconciliation at a fixed interval."""
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
    """Award points for live streams at regular intervals."""
    interval = LIVE_POINTS_INTERVAL_MINUTES * 60
    while True:
        await asyncio.sleep(interval)
        db = SessionLocal()
        try:
            awarded = award_live_points(db)
            if awarded > 0:
                print(f"💰 Live points awarded: {awarded}")
        except Exception as e:
            print(f"❌ Live points failed: {e}")
        finally:
            db.close()