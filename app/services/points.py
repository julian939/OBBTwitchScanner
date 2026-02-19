from __future__ import annotations

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import Streamer, Stream, PointTransaction
from app.database.enums import PointReason

POINTS_PER_MINUTE = 10
DAILY_BONUS_POINTS = 500
LIVE_POINTS_INTERVAL_MINUTES = 5
EVENT_MULTIPLIER = 2


def award_live_points(db: Session, multiplier: int = 1) -> int:
    from app.utils.categories import is_streamer_tracked_live

    now = datetime.now(timezone.utc)
    total_awarded = 0

    open_streams = (
        db.query(Stream)
        .join(Streamer)
        .filter(Stream.ended_at.is_(None), Streamer.is_live == True)
        .all()
    )

    for stream in open_streams:
        # Only award points if currently in a tracked category
        if not is_streamer_tracked_live(stream.streamer_id):
            continue

        if stream.last_points_at:
            since = stream.last_points_at.replace(tzinfo=timezone.utc)
        else:
            since = stream.started_at.replace(tzinfo=timezone.utc)

        elapsed = int((now - since).total_seconds() / 60)

        if elapsed < LIVE_POINTS_INTERVAL_MINUTES:
            continue

        points = elapsed * POINTS_PER_MINUTE * multiplier

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


def award_stream_end_points(streamer_id: str, stream: Stream, db: Session, multiplier: int = 1) -> list[PointTransaction]:
    transactions = []
    now = datetime.now(timezone.utc)

    if stream.last_points_at:
        since = stream.last_points_at.replace(tzinfo=timezone.utc)
    else:
        since = stream.started_at.replace(tzinfo=timezone.utc)

    remaining_minutes = int((now - since).total_seconds() / 60)

    if remaining_minutes > 0:
        time_points = remaining_minutes * POINTS_PER_MINUTE * multiplier
        tx = PointTransaction(
            streamer_id=streamer_id,
            points=time_points,
            reason=PointReason.STREAM_TIME,
            stream_id=stream.id,
        )
        db.add(tx)
        transactions.append(tx)

    if _is_first_stream_today(streamer_id, db):
        bonus = PointTransaction(
            streamer_id=streamer_id,
            points=DAILY_BONUS_POINTS,
            reason=PointReason.DAILY_BONUS,
            stream_id=stream.id,
        )
        db.add(bonus)
        transactions.append(bonus)

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