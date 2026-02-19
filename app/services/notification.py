from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class LiveNotification:
    """Data for a stream.online notification."""
    streamer_login: str
    streamer_display_name: str
    profile_image_url: str
    game_name: str
    title: str
    thumbnail_url: str
    started_at: str
    twitch_url: str


@dataclass
class OfflineNotification:
    """Data for a stream.offline notification."""
    streamer_login: str
    streamer_display_name: str
    profile_image_url: str
    duration_minutes: int
    points_awarded: list  # List of (reason, points) tuples
    total_points: int
    twitch_url: str


# Global queue for passing notifications from sync webhook to async bot
notification_queue: asyncio.Queue = asyncio.Queue()


def queue_live_notification(
    streamer_login: str,
    streamer_display_name: str,
    profile_image_url: str,
    game_name: str,
    title: str,
    thumbnail_url: str,
    started_at: str,
) -> None:
    """Queue a live notification (called from sync context)."""
    notification = LiveNotification(
        streamer_login=streamer_login,
        streamer_display_name=streamer_display_name,
        profile_image_url=profile_image_url or "",
        game_name=game_name,
        title=title,
        thumbnail_url=thumbnail_url,
        started_at=started_at,
        twitch_url=f"https://twitch.tv/{streamer_login}",
    )

    try:
        notification_queue.put_nowait(notification)
        print(f"📨 Queued live notification for {streamer_display_name}")
    except Exception as e:
        print(f"❌ Failed to queue notification: {e}")


def queue_offline_notification(
    streamer_login: str,
    streamer_display_name: str,
    profile_image_url: str,
    duration_minutes: int,
    points_awarded: list,
    total_points: int,
) -> None:
    """Queue an offline notification (called from sync context)."""
    notification = OfflineNotification(
        streamer_login=streamer_login,
        streamer_display_name=streamer_display_name,
        profile_image_url=profile_image_url or "",
        duration_minutes=duration_minutes,
        points_awarded=points_awarded,
        total_points=total_points,
        twitch_url=f"https://twitch.tv/{streamer_login}",
    )

    try:
        notification_queue.put_nowait(notification)
        print(f"📨 Queued offline notification for {streamer_display_name}")
    except Exception as e:
        print(f"❌ Failed to queue notification: {e}")