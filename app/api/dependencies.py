from __future__ import annotations

from fastapi import Query, HTTPException

from app.database.database import SessionLocal
from app.config import get_settings

settings = get_settings()


def get_db():
    """Yields database session, closes after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_admin_secret(secret: str = Query(..., alias="secret")) -> bool:
    """Dependency to protect admin endpoints."""
    if secret != settings.admin_secret:
        raise HTTPException(403, "Invalid admin secret")
    return True