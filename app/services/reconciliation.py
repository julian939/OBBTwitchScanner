from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.database.models import Streamer, Stream
from app.integrations.twitch import twitch_api
from app.services.points import award_stream_end_points


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
                open_stream.duration_minutes = int((now - started).total_seconds() / 60)
                streams_closed += 1
                award_stream_end_points(streamer.id, open_stream, db)

        # Case 2: Twitch says live but no open stream record
        elif actually_live:
            streamer.is_live = True
            if not open_stream:
                if not streamer.is_live:
                    fixed_online += 1
                stream_info = _get_stream_info(streamer.id)
                started_at = stream_info["started_at"] if stream_info else now
                db.add(Stream(streamer_id=streamer.id, started_at=started_at))
                streams_opened += 1

        # Case 3: Both offline and no open stream — nothing to do
        else:
            if open_stream:
                # Orphaned open stream, close it
                open_stream.ended_at = now
                started = open_stream.started_at.replace(tzinfo=timezone.utc)
                open_stream.duration_minutes = int((now - started).total_seconds() / 60)
                streams_closed += 1
                award_stream_end_points(streamer.id, open_stream, db)

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