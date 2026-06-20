from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from app.logging_config import configure_logging

configure_logging()

from fastapi import FastAPI

from app.database.database import init_db, SessionLocal
from app.api import webhook, admin, public
from app.integrations.image_cache import router as image_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Lifespan gestartet")
    recon_task = points_task = name_refresh_task = backup_task = bot_task = None
    try:
        init_db()
        logger.info("Database initialisiert")

        # Re-register all EventSub subscriptions with current callback URL
        from app.integrations.twitch import twitch_api
        from app.database.models import Streamer, Subscription
        db = SessionLocal()
        try:
            old_subs = twitch_api.get_eventsub_subscriptions()
            for s in old_subs:
                twitch_api.delete_eventsub_subscription(s["id"])
            logger.info("Alte EventSub-Subscriptions gelöscht: %s", len(old_subs))

            db.query(Subscription).delete()
            db.commit()

            streamers = db.query(Streamer).all()
            created = 0
            failed = 0
            for st in streamers:
                for event_type in ["stream.online", "stream.offline"]:
                    try:
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
                    except Exception:
                        failed += 1
                        logger.exception(
                            "EventSub-Subscription fehlgeschlagen für streamer_id=%s event_type=%s",
                            st.id,
                            event_type,
                        )
            db.commit()
            logger.info("EventSub-Subscriptions erstellt: %s, fehlgeschlagen: %s", created, failed)

            from app.services.reconciliation import reconcile_live_states
            result = reconcile_live_states(db)
            logger.info("Startup-Reconciliation: %s", result)
        finally:
            db.close()

        from app.services.scheduler import (
            periodic_reconciliation,
            periodic_live_points,
            periodic_name_refresh,
            periodic_backup,
        )
        recon_task = asyncio.create_task(periodic_reconciliation())
        points_task = asyncio.create_task(periodic_live_points())
        name_refresh_task = asyncio.create_task(periodic_name_refresh())
        backup_task = asyncio.create_task(periodic_backup())
        logger.info("Scheduler gestartet")

        from app.integrations.discord_bot import run_bot
        bot_task = asyncio.create_task(run_bot())
        logger.info("Discord-Bot Task gestartet")

        yield
    except Exception:
        logger.exception("Lifespan fehlgeschlagen")
        raise
    finally:
        for task in (recon_task, points_task, name_refresh_task, backup_task, bot_task):
            if task is not None:
                task.cancel()

        try:
            from app.integrations.discord_bot import bot
            if not bot.is_closed():
                await bot.close()
        except Exception:
            logger.exception("Fehler beim Schließen des Discord-Bots")

        logger.info("Lifespan beendet")


app = FastAPI(title="Twitch Stream Tracker", lifespan=lifespan)

app.include_router(webhook.router)
app.include_router(admin.router)
app.include_router(public.router)
app.include_router(image_router)
