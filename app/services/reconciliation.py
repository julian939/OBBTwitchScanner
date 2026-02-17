from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.database.models import Streamer, Stream
from app.integrations.twitch import twitch_api
from app.services.points import award_stream_end_points


def reconcile_live_states(db: Session) -> dict:
    """
    Sync DB state with actual Twitch live status.
    Fixes missed online/offline events.
    Returns summary of changes made.
    """
    streamers = db.query(Streamer).all()
    now = datetime.now(timezone.utc)

    fixed_online = 0
    fixed_offline = 0
    streams_opened = 0
    streams_closed = 0

    for streamer in streamers:
        actually_live = twitch_api.is_stream_live(streamer.id)

        # Case 1: DB says live, Twitch says offline -> missed stream.offline
        if streamer.is_live and not actually_live:
            streamer.is_live = False
            fixed_offline += 1

            # Close any open streams
            open_stream = (
                db.query(Stream)
                .filter(Stream.streamer_id == streamer.id, Stream.ended_at.is_(None))
                .order_by(Stream.started_at.desc())
                .first()
            )
            if open_stream:
                open_stream.ended_at = now
                started = open_stream.started_at.replace(tzinfo=timezone.utc)
                duration = (now - started).total_seconds() / 60
                open_stream.duration_minutes = int(duration)
                streams_closed += 1

                # Award points for the closed stream
                award_stream_end_points(streamer.id, open_stream, db)

        # Case 2: DB says offline, Twitch says live -> missed stream.online
        if not streamer.is_live and actually_live:
            streamer.is_live = True
            fixed_online += 1

            # Check if there's already an open stream (shouldn't be, but safety check)
            open_stream = (
                db.query(Stream)
                .filter(Stream.streamer_id == streamer.id, Stream.ended_at.is_(None))
                .first()
            )
            if not open_stream:
                # Create stream record - we don't know exact start time,
                # so we use the stream data from Twitch API
                stream_info = _get_stream_info(streamer.id)
                started_at = stream_info["started_at"] if stream_info else now

                new_stream = Stream(
                    streamer_id=streamer.id,
                    started_at=started_at,
                )
                db.add(new_stream)
                streams_opened += 1
        # Case 3: DB says live AND Twitch says live, but no open stream record
        if streamer.is_live and actually_live:
            open_stream = (
                db.query(Stream)
                .filter(Stream.streamer_id == streamer.id, Stream.ended_at.is_(None))
                .first()
            )
            if not open_stream:
                stream_info = _get_stream_info(streamer.id)
                started_at = stream_info["started_at"] if stream_info else now

                new_stream = Stream(
                    streamer_id=streamer.id,
                    started_at=started_at,
                )
                db.add(new_stream)
                streams_opened += 1
    db.commit()

    return {
        "streamers_checked": len(streamers),
        "fixed_online": fixed_online,
        "fixed_offline": fixed_offline,
        "streams_opened": streams_opened,
        "streams_closed": streams_closed,
    }


def _get_stream_info(user_id: str) -> dict | None:
    """Get current stream info including started_at from Twitch."""
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