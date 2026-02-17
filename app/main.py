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

    # Reconcile on startup
    from app.services.reconciliation import reconcile_live_states
    db = SessionLocal()
    try:
        result = reconcile_live_states(db)
        print(f"🔄 Startup reconciliation: {result}")
    finally:
        db.close()

    # Start background tasks
    from app.services.scheduler import periodic_reconciliation, periodic_live_points
    recon_task = asyncio.create_task(periodic_reconciliation())
    points_task = asyncio.create_task(periodic_live_points())
    print("⏰ Scheduled: reconciliation + live points")

    # Start Discord bot
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