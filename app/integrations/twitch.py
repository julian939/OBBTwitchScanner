from __future__ import annotations

import logging

import httpx
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


@dataclass
class CachedToken:
    access_token: str
    expires_at: datetime


class TwitchAPI:
    BASE_URL = "https://api.twitch.tv/helix"
    AUTH_URL = "https://id.twitch.tv/oauth2/token"

    def __init__(self):
        self._cached_token: CachedToken | None = None

    def _get_app_access_token(self) -> str:
        """Holt oder erneuert App Access Token."""
        now = datetime.now(timezone.utc)

        if self._cached_token and self._cached_token.expires_at > now + timedelta(minutes=5):
            return self._cached_token.access_token

        response = httpx.post(
            self.AUTH_URL,
            params={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "grant_type": "client_credentials"
            }
        )
        response.raise_for_status()
        data = response.json()

        self._cached_token = CachedToken(
            access_token=data["access_token"],
            expires_at=now + timedelta(seconds=data["expires_in"])
        )

        return self._cached_token.access_token

    def _headers(self) -> dict:
        return {
            "Client-ID": settings.twitch_client_id,
            "Authorization": f"Bearer {self._get_app_access_token()}",
            "Content-Type": "application/json"
        }

    def get_user(self, login: str) -> dict | None:
        """User-Info per Login-Name."""
        response = httpx.get(
            f"{self.BASE_URL}/users",
            params={"login": login},
            headers=self._headers()
        )
        response.raise_for_status()
        data = response.json()["data"]
        return data[0] if data else None

    def get_user_by_id(self, user_id: str) -> dict | None:
        """User-Info per ID."""
        response = httpx.get(
            f"{self.BASE_URL}/users",
            params={"id": user_id},
            headers=self._headers()
        )
        response.raise_for_status()
        data = response.json()["data"]
        return data[0] if data else None

    def is_stream_live(self, user_id: str) -> bool:
        """Prüft ob User gerade streamt."""
        response = httpx.get(
            f"{self.BASE_URL}/streams",
            params={"user_id": user_id},
            headers=self._headers()
        )
        response.raise_for_status()
        return len(response.json()["data"]) > 0

    def create_eventsub_subscription(
        self,
        event_type: str,
        broadcaster_user_id: str
    ) -> dict:
        """Erstellt EventSub Subscription."""
        if not (10 <= len(settings.webhook_secret) <= 100):
            raise ValueError("webhook_secret must be between 10 and 100 characters long")

        payload = {
            "type": event_type,
            "version": "1",
            "condition": {
                "broadcaster_user_id": str(broadcaster_user_id)
            },
            "transport": {
                "method": "webhook",
                "callback": settings.webhook_callback_url,
                "secret": settings.webhook_secret
            }
        }

        response = httpx.post(
            f"{self.BASE_URL}/eventsub/subscriptions",
            headers=self._headers(),
            json=payload
        )

        if response.status_code == 409:
            return {"status": "already_exists"}

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            logger.error(
                "Twitch EventSub create failed: status=%s body=%s payload=%s",
                response.status_code,
                response.text,
                payload,
            )
            raise
        return response.json()["data"][0]

    def delete_eventsub_subscription(self, subscription_id: str) -> bool:
        """Löscht EventSub Subscription."""
        response = httpx.delete(
            f"{self.BASE_URL}/eventsub/subscriptions",
            params={"id": subscription_id},
            headers=self._headers()
        )
        return response.status_code == 204

    def get_eventsub_subscriptions(self) -> list[dict]:
        """Holt alle aktiven EventSub Subscriptions."""
        subscriptions = []
        cursor = None

        while True:
            params = {}
            if cursor:
                params["after"] = cursor

            response = httpx.get(
                f"{self.BASE_URL}/eventsub/subscriptions",
                params=params,
                headers=self._headers()
            )
            response.raise_for_status()
            data = response.json()

            subscriptions.extend(data["data"])

            cursor = data.get("pagination", {}).get("cursor")
            if not cursor:
                break

        return subscriptions

    def get_stream_info(self, user_id: str) -> dict | None:
        """Get current stream details including game, title and thumbnail."""
        response = httpx.get(
            f"{self.BASE_URL}/streams",
            params={"user_id": user_id},
            headers=self._headers()
        )
        response.raise_for_status()
        data = response.json()["data"]

        if not data:
            return None

        stream = data[0]
        # Twitch thumbnail URL has {width}x{height} placeholders
        thumbnail_url = stream.get("thumbnail_url", "")
        thumbnail_url = thumbnail_url.replace("{width}", "1280").replace("{height}", "720")

        return {
            "title": stream.get("title", ""),
            "game_name": stream.get("game_name", ""),
            "thumbnail_url": thumbnail_url,
            "started_at": stream.get("started_at", ""),
            "viewer_count": stream.get("viewer_count", 0),
        }


    def get_viewer_counts(self, user_ids: list[str]) -> dict[str, int]:
        """Get current viewer counts for multiple streamers in one API call."""
        result = {}
        # Twitch API supports up to 100 user_ids per request
        for i in range(0, len(user_ids), 100):
            batch = user_ids[i:i + 100]
            response = httpx.get(
                f"{self.BASE_URL}/streams",
                params=[("user_id", uid) for uid in batch],
                headers=self._headers(),
            )
            response.raise_for_status()
            for stream in response.json()["data"]:
                result[stream["user_id"]] = stream.get("viewer_count", 0)
        return result


twitch_api = TwitchAPI()
