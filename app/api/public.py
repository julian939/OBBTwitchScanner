from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta

from app.api.dependencies import get_db
from app.database.models import Streamer, Stream, PointTransaction

router = APIRouter()


@router.get("/")
def health_check():
    """Health check endpoint."""
    return {"status": "running", "service": "Twitch Stream Tracker"}


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """Global tracking statistics."""
    streamers = db.query(Streamer).all()
    currently_live = [s for s in streamers if s.is_live]

    total_minutes = (
        db.query(func.sum(Stream.duration_minutes))
        .filter(Stream.duration_minutes.isnot(None))
        .scalar() or 0
    )

    total_streams = (
        db.query(func.count(Stream.id))
        .filter(Stream.ended_at.isnot(None))
        .scalar()
    )

    return {
        "tracked_streamers": len(streamers),
        "currently_live": len(currently_live),
        "live_streamers": [
            {"display_name": s.display_name, "login": s.login}
            for s in currently_live
        ],
        "total_streams_tracked": total_streams,
        "total_hours_tracked": round(total_minutes / 60, 1),
    }


@router.get("/leaderboard")
def get_leaderboard(db: Session = Depends(get_db)):
    """Streamer leaderboard ranked by points."""
    results = (
        db.query(
            Streamer.login,
            Streamer.display_name,
            Streamer.is_live,
            func.coalesce(func.sum(PointTransaction.points), 0).label("total_points"),
        )
        .outerjoin(PointTransaction, PointTransaction.streamer_id == Streamer.id)
        .group_by(Streamer.id)
        .order_by(func.coalesce(func.sum(PointTransaction.points), 0).desc())
        .all()
    )

    return {
        "leaderboard": [
            {
                "rank": i,
                "login": r.login,
                "display_name": r.display_name,
                "is_live": r.is_live,
                "total_points": r.total_points,
            }
            for i, r in enumerate(results, start=1)
        ]
    }


@router.get("/leaderboard/hours")
def get_leaderboard_by_hours(db: Session = Depends(get_db)):
    """Streamer leaderboard ranked by total hours streamed."""
    results = (
        db.query(
            Streamer.login,
            Streamer.display_name,
            func.count(Stream.id).label("stream_count"),
            func.coalesce(func.sum(Stream.duration_minutes), 0).label("total_minutes"),
            func.coalesce(func.avg(Stream.duration_minutes), 0).label("avg_minutes"),
        )
        .outerjoin(Stream, (Stream.streamer_id == Streamer.id) & (Stream.duration_minutes.isnot(None)))
        .group_by(Streamer.id)
        .order_by(func.coalesce(func.sum(Stream.duration_minutes), 0).desc())
        .all()
    )

    return {
        "leaderboard": [
            {
                "rank": i,
                "login": r.login,
                "display_name": r.display_name,
                "stream_count": r.stream_count,
                "total_hours": round(r.total_minutes / 60, 1),
                "avg_stream_hours": round(r.avg_minutes / 60, 1),
            }
            for i, r in enumerate(results, start=1)
        ]
    }


@router.get("/streamer/{username}")
def get_streamer_stats(username: str, db: Session = Depends(get_db)):
    """Detailed statistics for a specific streamer."""
    streamer = db.query(Streamer).filter(Streamer.login == username.lower()).first()
    if not streamer:
        raise HTTPException(404, f"Streamer '{username}' not found")

    # All streams, newest first
    streams = (
        db.query(Stream)
        .filter(Stream.streamer_id == streamer.id)
        .order_by(Stream.started_at.desc())
        .all()
    )
    completed = [s for s in streams if s.duration_minutes is not None]
    total_minutes = sum(s.duration_minutes for s in completed)

    # Total points
    total_points = (
        db.query(func.sum(PointTransaction.points))
        .filter(PointTransaction.streamer_id == streamer.id)
        .scalar() or 0
    )

    # Points grouped by reason
    points_breakdown = (
        db.query(
            PointTransaction.reason,
            func.sum(PointTransaction.points).label("points"),
            func.count(PointTransaction.id).label("count"),
        )
        .filter(PointTransaction.streamer_id == streamer.id)
        .group_by(PointTransaction.reason)
        .all()
    )

    # Longest stream
    longest = max(completed, key=lambda s: s.duration_minutes, default=None)

    # Last 7 days activity
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent_count = sum(1 for s in completed if s.started_at.replace(tzinfo=timezone.utc) >= week_ago)
    recent_minutes = sum(s.duration_minutes for s in completed if s.started_at.replace(tzinfo=timezone.utc) >= week_ago)

    # Current streak (consecutive days with at least one stream)
    streak = _calculate_streak(completed)

    return {
        "login": streamer.login,
        "display_name": streamer.display_name,
        "is_live": streamer.is_live,
        "points": {
            "total": total_points,
            "breakdown": [
                {"reason": r.reason, "points": r.points, "count": r.count}
                for r in points_breakdown
            ],
        },
        "stats": {
            "total_streams": len(completed),
            "total_hours": round(total_minutes / 60, 1),
            "avg_stream_hours": round(total_minutes / len(completed) / 60, 1) if completed else 0,
            "longest_stream_hours": round(longest.duration_minutes / 60, 1) if longest else 0,
            "current_streak_days": streak,
        },
        "last_7_days": {
            "streams": recent_count,
            "hours": round(recent_minutes / 60, 1),
        },
        "recent_streams": [
            {
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "duration_hours": round(s.duration_minutes / 60, 1) if s.duration_minutes else None,
            }
            for s in streams[:10]
        ],
    }


def _calculate_streak(completed_streams: list) -> int:
    """Calculate consecutive days with at least one stream."""
    if not completed_streams:
        return 0

    # Unique dates that had a stream
    stream_dates = sorted(
        {s.started_at.date() for s in completed_streams},
        reverse=True,
    )

    today = datetime.now(timezone.utc).date()

    # Streak only counts if streamed today or yesterday
    if stream_dates[0] < today - timedelta(days=1):
        return 0

    streak = 1
    for i in range(len(stream_dates) - 1):
        if (stream_dates[i] - stream_dates[i + 1]).days == 1:
            streak += 1
        else:
            break

    return streak