from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import Streamer, Stream, PointTransaction
from app.services.points import award_stream_end_points
from app.services.notification import queue_live_notification, queue_offline_notification
from app.services.roles import assign_live_role, remove_live_role
from app.integrations.twitch import twitch_api
from app.utils.categories import is_tracked_category


def handle_stream_online(event: dict, db: Session) -> None:
    streamer_id = event["broadcaster_user_id"]
    streamer_name = event["broadcaster_user_name"]
    streamer_login = event["broadcaster_user_login"]
    started_at_str = event["started_at"]
    started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))

    streamer = db.query(Streamer).filter(Streamer.id == streamer_id).first()
    if not streamer:
        streamer = Streamer(
            id=streamer_id,
            login=streamer_login,
            display_name=streamer_name,
        )
        db.add(streamer)

    streamer.is_live = True
    streamer.display_name = streamer_name
    db.commit()

    # Fetch stream info with retry
    import time
    stream_info = None
    for attempt in range(3):
        time.sleep(2)
        stream_info = twitch_api.get_stream_info(streamer_id)
        if stream_info and stream_info.get("title"):
            break

    game_name = stream_info["game_name"] if stream_info else ""

    # Only open stream + notify if tracked category
    if not is_tracked_category(game_name):
        print(f"⏭️ {streamer_name} went live in '{game_name}' (untracked)")
        return

    existing = (
        db.query(Stream)
        .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
        .first()
    )
    if not existing:
        stream = Stream(streamer_id=streamer_id, started_at=started_at)
        db.add(stream)
        db.commit()

    print(f"🟢 {streamer_name} went live in '{game_name}' at {started_at}")

    # Assign live role
    if streamer.discord_id:
        assign_live_role(streamer.discord_id)

    queue_live_notification(
        streamer_login=streamer_login,
        streamer_display_name=streamer_name,
        profile_image_url=streamer.profile_image_url or "",
        game_name=game_name,
        title=stream_info["title"] if stream_info else "",
        thumbnail_url=stream_info["thumbnail_url"] if stream_info else "",
        started_at=started_at_str,
    )


def handle_stream_offline(event: dict, db: Session) -> int | None:
    streamer_id = event["broadcaster_user_id"]
    now = datetime.now(timezone.utc)

    streamer = db.query(Streamer).filter(Streamer.id == streamer_id).first()
    if not streamer:
        return None

    streamer.is_live = False

    # Remove live role
    if streamer.discord_id:
        remove_live_role(streamer.discord_id)

    open_stream = (
        db.query(Stream)
        .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
        .order_by(Stream.started_at.desc())
        .first()
    )

    duration_minutes = None
    points_list = []

    if open_stream:
        open_stream.ended_at = now
        started = open_stream.started_at.replace(tzinfo=timezone.utc)
        duration_minutes = int((now - started).total_seconds() / 60)
        open_stream.duration_minutes = duration_minutes

        try:
            from app.integrations.discord_bot import bot
            from app.services.points import EVENT_MULTIPLIER
            multiplier = EVENT_MULTIPLIER if bot.has_active_event() else 1
        except Exception:
            multiplier = 1

        transactions = award_stream_end_points(streamer_id, open_stream, db, multiplier=multiplier)
        points_list = [(tx.reason, tx.points) for tx in transactions]
        print(f"🔴 {streamer.display_name} went offline after {duration_minutes} min")
    else:
        print(f"⚠️ No open stream for {streamer.display_name}, just marking offline")

    db.commit()

    total_points = (
        db.query(func.sum(PointTransaction.points))
        .filter(PointTransaction.streamer_id == streamer_id)
        .scalar() or 0
    )

    if duration_minutes:
        queue_offline_notification(
            streamer_login=streamer.login,
            streamer_display_name=streamer.display_name,
            profile_image_url=streamer.profile_image_url or "",
            duration_minutes=duration_minutes,
            points_awarded=points_list,
            total_points=total_points,
        )

    return duration_minutes