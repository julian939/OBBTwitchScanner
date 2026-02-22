from __future__ import annotations

import json
from fastapi import APIRouter, Request, Response, Depends, HTTPException
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.config import get_settings
from app.database.models import ProcessedMessage
from app.utils.security import verify_twitch_signature, is_timestamp_valid
from app.services.stream_tracker import handle_stream_online, handle_stream_offline

settings = get_settings()
router = APIRouter()


@router.post("/webhook/twitch")
async def twitch_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle incoming Twitch EventSub webhooks."""

    # Get headers
    message_id = request.headers.get("Twitch-Eventsub-Message-Id", "")
    timestamp = request.headers.get("Twitch-Eventsub-Message-Timestamp", "")
    signature = request.headers.get("Twitch-Eventsub-Message-Signature", "")
    message_type = request.headers.get("Twitch-Eventsub-Message-Type", "")

    # Get raw body (handle client disconnect gracefully)
    try:
        body_bytes = await request.body()
    except ClientDisconnect:
        print("⚠️ Twitch webhook: client disconnected before body was read")
        return Response(status_code=200)

    # Validate headers
    if not all([message_id, timestamp, signature, message_type]):
        raise HTTPException(400, "Missing required Twitch headers")

    # Verify signature
    if not verify_twitch_signature(
            message_id, timestamp, body_bytes, signature, settings.webhook_secret
    ):
        raise HTTPException(403, "Invalid signature")

    # Check timestamp
    if not is_timestamp_valid(timestamp):
        raise HTTPException(403, "Timestamp too old")

    # Check for duplicate
    existing = db.query(ProcessedMessage).filter(ProcessedMessage.id == message_id).first()
    if existing:
        return Response(status_code=200)

    # Mark as processed
    db.add(ProcessedMessage(id=message_id))
    db.commit()

    # Parse body
    body = json.loads(body_bytes)

    # Handle verification challenge
    if message_type == "webhook_callback_verification":
        challenge = body["challenge"]
        return Response(content=challenge, media_type="text/plain")

    # Handle notification
    if message_type == "notification":
        event_type = body["subscription"]["type"]
        event = body["event"]

        if event_type == "stream.online":
            handle_stream_online(event, db)
        elif event_type == "stream.offline":
            handle_stream_offline(event, db)

        return Response(status_code=200)

    # Handle revocation
    if message_type == "revocation":
        # Subscription was revoked by Twitch
        # Could log or recreate here
        return Response(status_code=200)

    return Response(status_code=200)