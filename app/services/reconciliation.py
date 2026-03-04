from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import Streamer, Stream, PointTransaction
from app.integrations.twitch import twitch_api
from app.services.points import award_stream_end_points
from app.services.notification import queue_offline_notification, queue_live_notification
from app.services.roles import assign_live_role, remove_live_role
from app.services.stream_tracker import (
    _get_streamer_lock,
    _is_offline_too_short,
    _cancel_pending_offline,
    has_pending_offline,
    handle_category_change,
)
from app.utils.categories import is_tracked_category


def _get_stream_points_summary(stream_id: int, db: Session) -> list:
    """Aggregate ALL points for a stream (including periodic live points)."""
    rows = (
        db.query(
            PointTransaction.reason,
            func.sum(PointTransaction.points).label("total"),
        )
        .filter(PointTransaction.stream_id == stream_id)
        .group_by(PointTransaction.reason)
        .all()
    )
    return [(row.reason, row.total) for row in rows]


def _close_stream_and_notify(streamer, open_stream, now, db):
    """Close an open stream, award points, and queue offline notification."""
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

    award_stream_end_points(streamer.id, open_stream, db, multiplier=multiplier)

    points_list = _get_stream_points_summary(open_stream.id, db)

    total_points = (
        db.query(func.sum(PointTransaction.points))
        .filter(PointTransaction.streamer_id == streamer.id)
        .scalar() or 0
    )

    if duration_minutes > 0:
        if _is_offline_too_short(duration_minutes):
            print(f"⏳ {streamer.display_name} offline notification suppressed ({duration_minutes}min too short)")
        else:
            queue_offline_notification(
                streamer_login=streamer.login,
                streamer_display_name=streamer.display_name,
                profile_image_url=streamer.profile_image_url or "",
                game_name=open_stream.game_name or "",
                duration_minutes=duration_minutes,
                points_awarded=points_list,
                total_points=total_points,
            )

    return duration_minutes


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
            # If a delayed offline timer is running, let it handle this —
            # the streamer might reconnect within the grace period
            if has_pending_offline(streamer.id):
                continue

            streamer.is_live = False
            fixed_offline += 1

            if streamer.discord_id:
                remove_live_role(streamer.discord_id)

            if open_stream:
                _close_stream_and_notify(streamer, open_stream, now, db)
                streams_closed += 1

        # Case 2: Twitch says live
        elif actually_live:
            if not streamer.is_live:
                fixed_online += 1
            streamer.is_live = True

            # Cancel any pending offline timer — streamer is confirmed live
            _cancel_pending_offline(streamer.id)

            stream_info = _get_stream_info(streamer.id)
            game_name = stream_info.get("game_name", "") if stream_info else ""

            if not open_stream:
                # No open stream — open one if category is tracked
                if is_tracked_category(game_name):
                    lock = _get_streamer_lock(streamer.id)
                    with lock:
                        recheck = (
                            db.query(Stream)
                            .filter(Stream.streamer_id == streamer.id, Stream.ended_at.is_(None))
                            .first()
                        )
                        if recheck:
                            continue

                        started_at = stream_info["started_at"] if stream_info else now
                        db.add(Stream(streamer_id=streamer.id, started_at=started_at, game_name=game_name))
                        db.flush()
                        streams_opened += 1

                    if streamer.discord_id:
                        assign_live_role(streamer.discord_id)

                    started_at_str = started_at.isoformat() if hasattr(started_at, 'isoformat') else str(started_at)
                    queue_live_notification(
                        streamer_login=streamer.login,
                        streamer_display_name=streamer.display_name,
                        profile_image_url=streamer.profile_image_url or "",
                        game_name=game_name,
                        title=stream_info.get("title", "") if stream_info else "",
                        thumbnail_url=stream_info.get("thumbnail_url", "") if stream_info else "",
                        started_at=started_at_str,
                    )
                else:
                    if streamer.discord_id:
                        remove_live_role(streamer.discord_id)

            else:
                # Open stream exists — check for category change
                if open_stream.game_name != game_name:
                    handle_category_change(streamer.id, game_name, db)

                    # Check if a new stream was opened or old one closed
                    new_open = (
                        db.query(Stream)
                        .filter(Stream.streamer_id == streamer.id, Stream.ended_at.is_(None))
                        .first()
                    )
                    if new_open:
                        streams_opened += 1
                        if streamer.discord_id:
                            assign_live_role(streamer.discord_id)
                    else:
                        if streamer.discord_id:
                            remove_live_role(streamer.discord_id)
                    streams_closed += 1

                elif not is_tracked_category(game_name):
                    # Category still untracked — close stream
                    _close_stream_and_notify(streamer, open_stream, now, db)
                    streams_closed += 1
                    if streamer.discord_id:
                        remove_live_role(streamer.discord_id)
                else:
                    # Same tracked category — all good
                    if streamer.discord_id:
                        assign_live_role(streamer.discord_id)

        # Case 3: Both offline
        else:
            # If a delayed offline timer is running, let it handle this
            if has_pending_offline(streamer.id):
                if streamer.discord_id:
                    remove_live_role(streamer.discord_id)
                continue

            if streamer.discord_id:
                remove_live_role(streamer.discord_id)

            if open_stream:
                _close_stream_and_notify(streamer, open_stream, now, db)
                streams_closed += 1

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
    thumb = data[0].get("thumbnail_url", "")
    if thumb:
        thumb = thumb.replace("{width}", "440").replace("{height}", "248")
    return {
        "started_at": datetime.fromisoformat(started_at_str.replace("Z", "+00:00")),
        "title": data[0].get("title", ""),
        "game_name": data[0].get("game_name", ""),
        "thumbnail_url": thumb,
    }