from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.database.database import init_db, SessionLocal
from app.api import webhook, admin, public


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting Twitch Stream Tracker...")
    init_db()
    print("✅ Database initialized")

    # Re-register all EventSub subscriptions with current callback URL
    from app.integrations.twitch import twitch_api
    from app.database.models import Streamer, Subscription
    db = SessionLocal()
    try:
        # Delete all old subscriptions at Twitch
        old_subs = twitch_api.get_eventsub_subscriptions()
        for s in old_subs:
            twitch_api.delete_eventsub_subscription(s["id"])
        print(f"🗑 Deleted {len(old_subs)} old EventSub subscriptions")

        # Clear local subscription records
        db.query(Subscription).delete()
        db.commit()

        # Re-create for all tracked streamers
        streamers = db.query(Streamer).all()
        created = 0
        for st in streamers:
            for event_type in ["stream.online", "stream.offline"]:
                result = twitch_api.create_eventsub_subscription(event_type, st.id)
                if result.get("status") != "already_exists":
                    sub = Subscription(
                        id=result["id"],
                        streamer_id=st.id,
                        type=event_type,
                        status=result["status"],
                    )
                    db.add(sub)
                    created += 1
        db.commit()
        print(f"✅ Created {created} EventSub subscriptions")

        # Reconcile live states
        from app.services.reconciliation import reconcile_live_states
        result = reconcile_live_states(db)
        print(f"🔄 Startup reconciliation: {result}")
    finally:
        db.close()

    # Start background tasks
    from app.services.scheduler import periodic_reconciliation, periodic_live_points
    recon_task = asyncio.create_task(periodic_reconciliation())
    points_task = asyncio.create_task(periodic_live_points())
    print("⏰ Scheduled: reconciliation + live points")

    from app.integrations.discord_bot import run_bot
    bot_task = asyncio.create_task(run_bot())
    print("🤖 Discord bot starting...")

    yield

    recon_task.cancel()
    points_task.cancel()
    bot_task.cancel()

    from app.integrations.discord_bot import bot
    if not bot.is_closed():
        await bot.close()

    print("👋 Shutting down...")


app = FastAPI(title="Twitch Stream Tracker", lifespan=lifespan)

app.include_router(webhook.router)
app.include_router(admin.router)
app.include_router(public.router)