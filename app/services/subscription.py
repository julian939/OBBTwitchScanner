from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import Streamer, Stream, Subscription
from app.integrations.twitch import twitch_api


def add_streamer(username: str, db: Session) -> dict:
    """
    Add a streamer to track.
    Creates DB record and EventSub subscriptions.
    Returns streamer info or raises exception.
    """
    user = twitch_api.get_user(username.lower())
    if not user:
        raise ValueError(f"Twitch user '{username}' not found")

    user_id = user["id"]
    login = user["login"]
    display_name = user["display_name"]
    profile_image_url = user.get("profile_image_url", "")

    # Check if already tracked
    existing = db.query(Streamer).filter(Streamer.id == user_id).first()
    if existing:
        raise ValueError(f"Already tracking {display_name}")

    # Create streamer record
    streamer = Streamer(
        id=user_id,
        login=login,
        display_name=display_name,
        profile_image_url=profile_image_url,
        is_live=twitch_api.is_stream_live(user_id)
    )
    db.add(streamer)

    # Create EventSub subscriptions
    for event_type in ["stream.online", "stream.offline"]:
        result = twitch_api.create_eventsub_subscription(event_type, user_id)

        if result.get("status") != "already_exists":
            sub = Subscription(
                id=result["id"],
                streamer_id=user_id,
                type=event_type,
                status=result["status"]
            )
            db.add(sub)

    db.commit()

    return {
        "id": user_id,
        "login": login,
        "display_name": display_name,
        "is_live": streamer.is_live
    }


def remove_streamer(username: str, db: Session) -> None:
    """
    Stop tracking a streamer.
    Deletes EventSub subscriptions and DB records.
    """
    streamer = db.query(Streamer).filter(Streamer.login == username.lower()).first()
    if not streamer:
        raise ValueError(f"Not tracking '{username}'")

    # Delete EventSub subscriptions at Twitch
    subscriptions = db.query(Subscription).filter(Subscription.streamer_id == streamer.id).all()
    for sub in subscriptions:
        twitch_api.delete_eventsub_subscription(sub.id)

    # Delete from database
    db.query(Subscription).filter(Subscription.streamer_id == streamer.id).delete()
    db.query(Stream).filter(Stream.streamer_id == streamer.id).delete()
    db.query(Streamer).filter(Streamer.id == streamer.id).delete()
    db.commit()


def sync_subscriptions(db: Session) -> dict:
    """
    Sync local subscription records with Twitch.
    Returns stats about what was synced.
    """
    twitch_subs = twitch_api.get_eventsub_subscriptions()
    local_subs = {s.id: s for s in db.query(Subscription).all()}

    added = 0
    removed = 0

    # Add missing local records
    for sub in twitch_subs:
        if sub["id"] not in local_subs:
            streamer_id = sub["condition"].get("broadcaster_user_id")
            streamer = db.query(Streamer).filter(Streamer.id == streamer_id).first()

            if streamer:
                new_sub = Subscription(
                    id=sub["id"],
                    streamer_id=streamer_id,
                    type=sub["type"],
                    status=sub["status"]
                )
                db.add(new_sub)
                added += 1

    # Remove stale local records
    twitch_sub_ids = {s["id"] for s in twitch_subs}
    for sub_id, sub in local_subs.items():
        if sub_id not in twitch_sub_ids:
            db.delete(sub)
            removed += 1

    db.commit()

    return {
        "twitch_subscriptions": len(twitch_subs),
        "added_locally": added,
        "removed_stale": removed
    }