from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import Streamer, Stream, PointTransaction
from app.integrations.twitch import twitch_api
from app.services.points import award_stream_end_points
from app.services.notification import queue_offline_notification
from app.utils.categories import is_tracked_category


def reconcile_live_states(db: Session) -> dict:
    streamers = db.query(Streamer).all()
    now = datetime.now(timezone.utc)

    fixed_online = 0
    fixed_offline = 0
    streams_opened = 0
    streams_closed = 0

    for streamer in streamers:
        actually_live = twitch_api.is_stream_live(streamer.id)

        open_stream = (
            db.query(Stream)
            .filter(Stream.streamer_id == streamer.id, Stream.ended_at.is_(None))
            .first()
        )

        # Case 1: DB says live, Twitch says offline
        if streamer.is_live and not actually_live:
            streamer.is_live = False
            fixed_offline += 1

            if open_stream:
                open_stream.ended_at = now
                started = open_stream.started_at.replace(tzinfo=timezone.utc)
                duration_minutes = int((now - started).total_seconds() / 60)
                open_stream.duration_minutes = duration_minutes
                streams_closed += 1

                try:
                    from app.integrations.discord_bot import bot
                    from app.services.points import EVENT_MULTIPLIER
                    multiplier = EVENT_MULTIPLIER if bot.has_active_event() else 1
                except Exception:
                    multiplier = 1

                transactions = award_stream_end_points(streamer.id, open_stream, db, multiplier=multiplier)
                points_list = [(tx.reason, tx.points) for tx in transactions]

                total_points = (
                    db.query(func.sum(PointTransaction.points))
                    .filter(PointTransaction.streamer_id == streamer.id)
                    .scalar() or 0
                )

                if duration_minutes > 0:
                    queue_offline_notification(
                        streamer_login=streamer.login,
                        streamer_display_name=streamer.display_name,
                        profile_image_url=streamer.profile_image_url or "",
                        duration_minutes=duration_minutes,
                        points_awarded=points_list,
                        total_points=total_points,
                    )

        # Case 2: Twitch says live
        elif actually_live:
            if not streamer.is_live:
                fixed_online += 1
            streamer.is_live = True

            # Only open stream if tracked category
            if not open_stream:
                stream_info = _get_stream_info(streamer.id)
                game_name = stream_info.get("game_name", "") if stream_info else ""

                if is_tracked_category(game_name):
                    started_at = stream_info["started_at"] if stream_info else now
                    db.add(Stream(streamer_id=streamer.id, started_at=started_at))
                    streams_opened += 1

            # Close stream if category switched to untracked
            elif open_stream:
                stream_info = _get_stream_info(streamer.id)
                game_name = stream_info.get("game_name", "") if stream_info else ""

                if not is_tracked_category(game_name):
                    open_stream.ended_at = now
                    started = open_stream.started_at.replace(tzinfo=timezone.utc)
                    duration_minutes = int((now - started).total_seconds() / 60)
                    open_stream.duration_minutes = duration_minutes
                    streams_closed += 1

                    try:
                        from app.integrations.discord_bot import bot
                        from app.services.points import EVENT_MULTIPLIER
                        multiplier = EVENT_MULTIPLIER if bot.has_active_event() else 1
                    except Exception:
                        multiplier = 1

                    award_stream_end_points(streamer.id, open_stream, db, multiplier=multiplier)

        # Case 3: Both offline
        else:
            if open_stream:
                open_stream.ended_at = now
                started = open_stream.started_at.replace(tzinfo=timezone.utc)
                duration_minutes = int((now - started).total_seconds() / 60)
                open_stream.duration_minutes = duration_minutes
                streams_closed += 1

                try:
                    from app.integrations.discord_bot import bot
                    from app.services.points import EVENT_MULTIPLIER
                    multiplier = EVENT_MULTIPLIER if bot.has_active_event() else 1
                except Exception:
                    multiplier = 1

                transactions = award_stream_end_points(streamer.id, open_stream, db, multiplier=multiplier)
                points_list = [(tx.reason, tx.points) for tx in transactions]

                total_points = (
                    db.query(func.sum(PointTransaction.points))
                    .filter(PointTransaction.streamer_id == streamer.id)
                    .scalar() or 0
                )

                if duration_minutes > 0:
                    queue_offline_notification(
                        streamer_login=streamer.login,
                        streamer_display_name=streamer.display_name,
                        profile_image_url=streamer.profile_image_url or "",
                        duration_minutes=duration_minutes,
                        points_awarded=points_list,
                        total_points=total_points,
                    )

    db.commit()

    return {
        "streamers_checked": len(streamers),
        "fixed_online": fixed_online,
        "fixed_offline": fixed_offline,
        "streams_opened": streams_opened,
        "streams_closed": streams_closed,
    }


def _get_stream_info(user_id: str) -> dict | None:
    import httpx
    response = httpx.get(
        f"{twitch_api.BASE_URL}/streams",
        params={"user_id": user_id},
        headers=twitch_api._headers(),
    )
    response.raise_for_status()
    data = response.json()["data"]
    if not data:
        return None
    started_at_str = data[0]["started_at"]
    return {
        "started_at": datetime.fromisoformat(started_at_str.replace("Z", "+00:00")),
        "title": data[0].get("title", ""),
        "game_name": data[0].get("game_name", ""),
    }