from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import Streamer, Stream, PointTransaction
from app.services.points import award_stream_end_points
from app.services.notification import queue_live_notification, queue_offline_notification
from app.integrations.twitch import twitch_api


def handle_stream_online(event: dict, db: Session) -> None:
    """Process stream.online event."""
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

    # Avoid duplicate stream records
    existing = (
        db.query(Stream)
        .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
        .first()
    )
    if not existing:
        stream = Stream(streamer_id=streamer_id, started_at=started_at)
        db.add(stream)

    db.commit()
    print(f"🟢 {streamer_name} went live at {started_at}")

    # Fetch stream info for notification
    stream_info = twitch_api.get_stream_info(streamer_id)
    queue_live_notification(
        streamer_login=streamer_login,
        streamer_display_name=streamer_name,
        profile_image_url=streamer.profile_image_url or "",
        game_name=stream_info["game_name"] if stream_info else "",
        title=stream_info["title"] if stream_info else "",
        thumbnail_url=stream_info["thumbnail_url"] if stream_info else "",
        started_at=started_at_str,
    )


def handle_stream_offline(event: dict, db: Session) -> int | None:
    """Process stream.offline event. Returns duration in minutes."""
    streamer_id = event["broadcaster_user_id"]
    now = datetime.now(timezone.utc)

    streamer = db.query(Streamer).filter(Streamer.id == streamer_id).first()
    if not streamer:
        return None

    streamer.is_live = False

    open_stream: Stream | None = (
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
        duration = (now - started).total_seconds() / 60
        duration_minutes = int(duration)
        open_stream.duration_minutes = duration_minutes

        # Award points
        transactions = award_stream_end_points(streamer_id, open_stream, db)
        points_list = [(tx.reason, tx.points) for tx in transactions]

        print(f"🔴 {streamer.display_name} went offline after {duration_minutes} min")

    db.commit()

    # Get total points for notification
    total_points = (
        db.query(func.sum(PointTransaction.points))
        .filter(PointTransaction.streamer_id == streamer_id)
        .scalar() or 0
    )

    # Queue notification
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