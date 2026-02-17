from __future__ import annotations

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import Streamer, Stream, PointTransaction
from app.database.enums import PointReason

# Configuration
POINTS_PER_MINUTE = 10
DAILY_BONUS_POINTS = 500
LIVE_POINTS_INTERVAL_MINUTES = 5  # Award points every 5 minutes


def award_live_points(db: Session) -> int:
    """
    Award points for all currently open streams.
    Called periodically by scheduler.
    Returns total points awarded.
    """
    now = datetime.now(timezone.utc)
    total_awarded = 0

    open_streams = (
        db.query(Stream)
        .join(Streamer)
        .filter(Stream.ended_at.is_(None), Streamer.is_live == True)
        .all()
    )

    for stream in open_streams:
        # Calculate minutes since last point award (or stream start)
        if stream.last_points_at:
            since = stream.last_points_at.replace(tzinfo=timezone.utc)
        else:
            since = stream.started_at.replace(tzinfo=timezone.utc)

        elapsed = int((now - since).total_seconds() / 60)

        if elapsed < LIVE_POINTS_INTERVAL_MINUTES:
            continue

        points = elapsed * POINTS_PER_MINUTE

        tx = PointTransaction(
            streamer_id=stream.streamer_id,
            points=points,
            reason=PointReason.STREAM_TIME,
            stream_id=stream.id,
        )
        db.add(tx)

        stream.last_points_at = now
        total_awarded += points

    db.commit()
    return total_awarded


def award_stream_end_points(streamer_id: str, stream: Stream, db: Session) -> list[PointTransaction]:
    """
    Award remaining time points + bonuses when stream ends.
    Only awards minutes not yet covered by live points.
    """
    transactions = []
    now = datetime.now(timezone.utc)

    # Calculate remaining minutes since last live points award
    if stream.last_points_at:
        since = stream.last_points_at.replace(tzinfo=timezone.utc)
    else:
        since = stream.started_at.replace(tzinfo=timezone.utc)

    remaining_minutes = int((now - since).total_seconds() / 60)

    # Award remaining stream time points
    if remaining_minutes > 0:
        time_points = remaining_minutes * POINTS_PER_MINUTE
        tx = PointTransaction(
            streamer_id=streamer_id,
            points=time_points,
            reason=PointReason.STREAM_TIME,
            stream_id=stream.id,
        )
        db.add(tx)
        transactions.append(tx)

    # Daily bonus: first completed stream of the day
    if _is_first_stream_today(streamer_id, db):
        bonus = PointTransaction(
            streamer_id=streamer_id,
            points=DAILY_BONUS_POINTS,
            reason=PointReason.DAILY_BONUS,
            stream_id=stream.id,
        )
        db.add(bonus)
        transactions.append(bonus)

    # Streak bonus: 3+ consecutive days
    streak = _get_current_streak(streamer_id, db)
    if streak >= 3:
        streak_points = streak * 100
        streak_tx = PointTransaction(
            streamer_id=streamer_id,
            points=streak_points,
            reason=PointReason.STREAK_BONUS,
            stream_id=stream.id,
        )
        db.add(streak_tx)
        transactions.append(streak_tx)

    db.commit()
    return transactions


def _is_first_stream_today(streamer_id: str, db: Session) -> bool:
    """Check if no daily bonus was awarded today."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = (
        db.query(func.count(PointTransaction.id))
        .filter(
            PointTransaction.streamer_id == streamer_id,
            PointTransaction.reason == PointReason.DAILY_BONUS,
            PointTransaction.created_at >= today_start,
        )
        .scalar()
    )
    return count == 0


def _get_current_streak(streamer_id: str, db: Session) -> int:
    """Calculate consecutive days with at least one completed stream."""
    streams = (
        db.query(Stream)
        .filter(
            Stream.streamer_id == streamer_id,
            Stream.duration_minutes.isnot(None),
        )
        .order_by(Stream.started_at.desc())
        .all()
    )
    if not streams:
        return 0

    stream_dates = sorted(
        {s.started_at.date() for s in streams},
        reverse=True,
    )

    today = datetime.now(timezone.utc).date()
    if stream_dates[0] < today - timedelta(days=1):
        return 0

    streak = 1
    for i in range(len(stream_dates) - 1):
        if (stream_dates[i] - stream_dates[i + 1]).days == 1:
            streak += 1
        else:
            break
    return streak