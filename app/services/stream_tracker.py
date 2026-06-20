from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import Streamer, Stream, PointTransaction
from app.services.points import award_stream_end_points
from app.services.notification import queue_live_notification, queue_offline_notification
from app.services.roles import assign_live_role, remove_live_role, schedule_leaderboard_role_sync
from app.integrations.twitch import twitch_api
from app.utils.categories import is_tracked_category
from app.config import get_settings

_settings = get_settings()
logger = logging.getLogger(__name__)


def _is_offline_too_short(duration_minutes: int) -> bool:
    """Check if the stream was too short to notify."""
    return duration_minutes < _settings.notify_offline_min_duration_minutes


# ── Per-streamer locks (prevent race between webhook & reconciliation) ──

_stream_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_streamer_lock(streamer_id: str) -> threading.Lock:
    with _locks_lock:
        if streamer_id not in _stream_locks:
            _stream_locks[streamer_id] = threading.Lock()
        return _stream_locks[streamer_id]


# ── Delayed offline mechanism ─────────────────────────────────
# Instead of a live-cooldown we delay the offline finalization.
# If the streamer comes back within the delay window (crash/reconnect),
# the timer is cancelled and the stream continues seamlessly.

_pending_offlines: dict[str, threading.Timer] = {}
_pending_lock = threading.Lock()


def _cancel_pending_offline(streamer_id: str) -> bool:
    """Cancel a pending offline timer. Returns True if one was cancelled."""
    with _pending_lock:
        timer = _pending_offlines.pop(streamer_id, None)
    if timer:
        timer.cancel()
        logger.info("Pending-Offline für %s abgebrochen (Reconnect)", streamer_id)
        return True
    return False


def has_pending_offline(streamer_id: str) -> bool:
    """Check if a streamer has a pending offline timer (grace period active)."""
    with _pending_lock:
        return streamer_id in _pending_offlines


def _schedule_offline(streamer_id: str, delay_seconds: float, offline_at: datetime) -> None:
    """Schedule delayed offline finalization with the real offline timestamp."""
    with _pending_lock:
        # Cancel any existing timer first
        old = _pending_offlines.pop(streamer_id, None)
        if old:
            old.cancel()

        timer = threading.Timer(delay_seconds, _finalize_offline, args=[streamer_id, offline_at])
        timer.daemon = True
        _pending_offlines[streamer_id] = timer
        timer.start()


def _finalize_offline(streamer_id: str, offline_at: datetime) -> None:
    """
    Called after the delay. If the streamer is still offline,
    close the stream using the real offline timestamp for accurate duration/points.
    """
    from app.database.database import SessionLocal

    # Remove from pending map
    with _pending_lock:
        _pending_offlines.pop(streamer_id, None)

    lock = _get_streamer_lock(streamer_id)
    with lock:
        db = SessionLocal()
        try:
            streamer = db.query(Streamer).filter(Streamer.id == streamer_id).first()
            if not streamer:
                return

            if streamer.is_locked:
                return

            # If streamer came back online in the meantime → abort
            if streamer.is_live:
                logger.info("%s ist wieder live, Offline-Finalisierung wird übersprungen", streamer.display_name)
                return

            open_stream = (
                db.query(Stream)
                .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
                .order_by(Stream.started_at.desc())
                .first()
            )
            if not open_stream:
                logger.warning(
                    "Kein offener Stream für %s bei Finalisierung (evtl. bereits durch Reconciliation geschlossen)",
                    streamer.display_name,
                )
                return

            # Guard: if ended_at is already set, reconciliation beat us
            if open_stream.ended_at is not None:
                logger.warning("Stream für %s bereits geschlossen, Finalisierung übersprungen", streamer.display_name)
                return

            started = open_stream.started_at.replace(tzinfo=timezone.utc)
            duration_minutes = int((offline_at - started).total_seconds() / 60)

            open_stream.ended_at = offline_at
            open_stream.duration_minutes = duration_minutes

            # Event multiplier
            try:
                from app.integrations.discord_bot import bot
                from app.services.points import EVENT_MULTIPLIER
                multiplier = EVENT_MULTIPLIER if bot.has_active_event() else 1
            except Exception:
                logger.warning("Discord-Eventstatus konnte in Offline-Finalisierung nicht geprüft werden", exc_info=True)
                multiplier = 1

            award_stream_end_points(streamer_id, open_stream, db, multiplier=multiplier, end_time=offline_at)

            points_list = _get_stream_points_summary(open_stream.id, db)
            db.commit()

            total_points = (
                db.query(func.sum(PointTransaction.points))
                .filter(PointTransaction.streamer_id == streamer_id)
                .scalar() or 0
            )

            stream_game = open_stream.game_name or ""

            logger.info("Offline finalisiert für %s nach %s min", streamer.display_name, duration_minutes)

            if _is_offline_too_short(duration_minutes):
                logger.info(
                    "Offline-Benachrichtigung unterdrückt für %s (%smin < %smin)",
                    streamer.display_name,
                    duration_minutes,
                    _settings.notify_offline_min_duration_minutes,
                )
            else:
                queue_offline_notification(
                    streamer_id=streamer_id,
                    streamer_login=streamer.login,
                    streamer_display_name=streamer.display_name,
                    profile_image_url=streamer.profile_image_url or "",
                    game_name=stream_game,
                    duration_minutes=duration_minutes,
                    points_awarded=points_list,
                    total_points=total_points,
                )

            schedule_leaderboard_role_sync()

        except Exception as e:
            logger.exception("Offline-Finalisierung fehlgeschlagen für %s: %s", streamer_id, e)
        finally:
            db.close()


# ── Stream Online ─────────────────────────────────────────────

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

    # Cancel any pending offline → streamer reconnected
    was_pending = _cancel_pending_offline(streamer_id)

    # Fetch stream info with retry
    import time
    stream_info = None
    for attempt in range(3):
        time.sleep(2)
        stream_info = twitch_api.get_stream_info(streamer_id)
        if stream_info and stream_info.get("title"):
            break

    game_name = stream_info["game_name"] if stream_info else ""

    if streamer.is_locked:
        logger.info("%s ist gesperrt, Online-Event wird ignoriert", streamer_name)
        return

    if not is_tracked_category(game_name):
        logger.info("%s ging live in '%s' (untracked)", streamer_name, game_name)
        return

    lock = _get_streamer_lock(streamer_id)
    with lock:
        existing = (
            db.query(Stream)
            .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
            .first()
        )

        if existing:
            if existing.game_name == game_name:
                # Same category → stream continues (reconnect case)
                logger.info("%s reconnectet, Stream läuft weiter (%s)", streamer_name, game_name)
                return
            else:
                # Different category → close old stream, open new one
                _close_stream_for_category_change(streamer, existing, game_name, db)

        stream = Stream(streamer_id=streamer_id, started_at=started_at, game_name=game_name)
        db.add(stream)
        db.commit()

    logger.info("%s ging live in '%s' um %s", streamer_name, game_name, started_at)

    # Assign live role
    if streamer.discord_id:
        assign_live_role(streamer.discord_id)

    # Send live notification (no cooldown needed — delayed offline handles reconnects)
    queue_live_notification(
        streamer_id=streamer_id,
        streamer_login=streamer_login,
        streamer_display_name=streamer_name,
        profile_image_url=streamer.profile_image_url or "",
        game_name=game_name,
        title=stream_info["title"] if stream_info else "",
        thumbnail_url=stream_info["thumbnail_url"] if stream_info else "",
        started_at=started_at_str,
    )


# ── Stream Offline ────────────────────────────────────────────

def handle_stream_offline(event: dict, db: Session) -> None:
    """
    Mark streamer as offline and schedule delayed finalization.
    The actual stream closing + notification happens after the delay,
    giving the streamer time to reconnect (crash/disconnect).
    """
    streamer_id = event["broadcaster_user_id"]

    streamer = db.query(Streamer).filter(Streamer.id == streamer_id).first()
    if not streamer:
        return

    streamer.is_live = False
    db.commit()

    # Remove live role immediately (visual feedback)
    if streamer.discord_id:
        remove_live_role(streamer.discord_id)

    # Check if there's even an open stream to finalize
    open_stream = (
        db.query(Stream)
        .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
        .first()
    )
    if not open_stream:
        logger.warning("Kein offener Stream für %s, nur offline markiert", streamer.display_name)
        return

    delay = _settings.notify_offline_delay_minutes * 60
    offline_at = datetime.now(timezone.utc)
    _schedule_offline(streamer_id, delay, offline_at)
    logger.info(
        "%s ging offline, Finalisierung in %smin",
        streamer.display_name,
        _settings.notify_offline_delay_minutes,
    )


# ── Category Change ───────────────────────────────────────────

def _close_stream_for_category_change(
    streamer: Streamer,
    open_stream: Stream,
    new_game: str,
    db: Session,
) -> None:
    """
    Close the current stream because the category changed.
    Awards points and sends offline notification for the old category.
    """
    now = datetime.now(timezone.utc)
    started = open_stream.started_at.replace(tzinfo=timezone.utc)
    duration_minutes = int((now - started).total_seconds() / 60)

    open_stream.ended_at = now
    open_stream.duration_minutes = duration_minutes
    old_game = open_stream.game_name or ""

    # Event multiplier + viewer multiplier (stream is still live)
    try:
        from app.integrations.discord_bot import bot
        from app.services.points import EVENT_MULTIPLIER, get_viewer_multiplier
        from app.integrations.twitch import twitch_api
        event_mult = EVENT_MULTIPLIER if bot.has_active_event() else 1
        stream_info = twitch_api.get_stream_info(streamer.id)
        viewer_mult = get_viewer_multiplier(stream_info["viewer_count"]) if stream_info else 1.0
        multiplier = event_mult * viewer_mult
    except Exception:
        logger.warning("Discord-Eventstatus konnte beim Category-Change nicht geprüft werden", exc_info=True)
        multiplier = 1

    award_stream_end_points(streamer.id, open_stream, db, multiplier=multiplier, end_time=now)

    points_list = _get_stream_points_summary(open_stream.id, db)
    db.commit()

    total_points = (
        db.query(func.sum(PointTransaction.points))
        .filter(PointTransaction.streamer_id == streamer.id)
        .scalar() or 0
    )

    logger.info(
        "%s wechselte von '%s' zu '%s' nach %smin",
        streamer.display_name,
        old_game,
        new_game,
        duration_minutes,
    )

    if not _is_offline_too_short(duration_minutes):
        queue_offline_notification(
            streamer_id=streamer.id,
            streamer_login=streamer.login,
            streamer_display_name=streamer.display_name,
            profile_image_url=streamer.profile_image_url or "",
            game_name=old_game,
            duration_minutes=duration_minutes,
            points_awarded=points_list,
            total_points=total_points,
        )

    schedule_leaderboard_role_sync()


def handle_category_change(streamer_id: str, new_game: str, db: Session) -> None:
    """
    Called by reconciliation when a live streamer's category changed.
    Closes the old stream and opens a new one if the new category is tracked.
    """
    lock = _get_streamer_lock(streamer_id)
    with lock:
        streamer = db.query(Streamer).filter(Streamer.id == streamer_id).first()
        if not streamer:
            return

        if streamer.is_locked:
            return

        open_stream = (
            db.query(Stream)
            .filter(Stream.streamer_id == streamer_id, Stream.ended_at.is_(None))
            .first()
        )

        # If no open stream but new category is tracked → open fresh
        if not open_stream:
            if is_tracked_category(new_game):
                now = datetime.now(timezone.utc)
                stream = Stream(streamer_id=streamer_id, started_at=now, game_name=new_game)
                db.add(stream)
                db.commit()
                logger.info("%s wechselte zu getracktem '%s', Stream geöffnet", streamer.display_name, new_game)

                stream_info = twitch_api.get_stream_info(streamer_id)
                queue_live_notification(
                    streamer_id=streamer_id,
                    streamer_login=streamer.login,
                    streamer_display_name=streamer.display_name,
                    profile_image_url=streamer.profile_image_url or "",
                    game_name=new_game,
                    title=stream_info["title"] if stream_info else "",
                    thumbnail_url=stream_info["thumbnail_url"] if stream_info else "",
                    started_at=now.isoformat(),
                )
            return

        # Same category → nothing to do
        if open_stream.game_name == new_game:
            return

        # Different category → close old stream
        _close_stream_for_category_change(streamer, open_stream, new_game, db)

        # If new category is tracked → open new stream + notify
        if is_tracked_category(new_game):
            now = datetime.now(timezone.utc)
            stream = Stream(streamer_id=streamer_id, started_at=now, game_name=new_game)
            db.add(stream)
            db.commit()
            logger.info("%s neuer Stream für '%s' geöffnet", streamer.display_name, new_game)

            stream_info = twitch_api.get_stream_info(streamer_id)
            queue_live_notification(
                streamer_id=streamer_id,
                streamer_login=streamer.login,
                streamer_display_name=streamer.display_name,
                profile_image_url=streamer.profile_image_url or "",
                game_name=new_game,
                title=stream_info["title"] if stream_info else "",
                thumbnail_url=stream_info["thumbnail_url"] if stream_info else "",
                started_at=now.isoformat(),
            )
        else:
            logger.info("%s wechselte zu ungetracktem '%s', Stream geschlossen", streamer.display_name, new_game)


# ── Helpers ───────────────────────────────────────────────────

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
