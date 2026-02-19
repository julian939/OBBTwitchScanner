from __future__ import annotations
from app.config import get_settings
from app.integrations.twitch import twitch_api

settings = get_settings()


def is_tracked_category(game_name: str | None) -> bool:
    """Check if a game/category is in the tracked list. Empty list = all tracked."""
    if not settings.tracked_categories:
        return True
    if not game_name:
        return False
    return game_name.lower() in [c.lower() for c in settings.tracked_categories]


def is_streamer_tracked_live(streamer_id: str) -> bool:
    """Check if a streamer is live AND in a tracked category."""
    info = twitch_api.get_stream_info(streamer_id)
    if not info:
        return False
    return is_tracked_category(info.get("game_name"))