from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, verify_admin_secret
from app.database.models import Streamer
from app.services.subscription import add_streamer, remove_streamer, sync_subscriptions

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_secret)])


@router.post("/streamers/add")
def api_add_streamer(username: str, db: Session = Depends(get_db)):
    """Add a streamer to track."""
    try:
        streamer = add_streamer(username, db)
        return {"status": "success", "streamer": streamer}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/streamers/remove")
def api_remove_streamer(username: str, db: Session = Depends(get_db)):
    """Stop tracking a streamer."""
    try:
        remove_streamer(username, db)
        return {"status": "success", "removed": username}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/streamers")
def api_list_streamers(db: Session = Depends(get_db)):
    """List all tracked streamers."""
    streamers = db.query(Streamer).all()

    return {
        "count": len(streamers),
        "streamers": [
            {
                "id": s.id,
                "login": s.login,
                "display_name": s.display_name,
                "is_live": s.is_live
            }
            for s in streamers
        ]
    }


@router.get("/subscriptions/sync")
def api_sync_subscriptions(db: Session = Depends(get_db)):
    """Sync local subscription records with Twitch."""
    result = sync_subscriptions(db)
    return {"status": "synced", **result}


@router.get("/reconcile")
def api_reconcile(db: Session = Depends(get_db)):
    from app.services.reconciliation import reconcile_live_states
    result = reconcile_live_states(db)
    return {"status": "reconciled", **result}