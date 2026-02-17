from __future__ import annotations

import hmac
import hashlib
from datetime import datetime, timezone


def verify_twitch_signature(
        message_id: str,
        timestamp: str,
        body: bytes,
        expected_signature: str,
        secret: str
) -> bool:
    """
    Verify that the request actually comes from Twitch.

    Twitch signs: HMAC-SHA256(secret, message_id + timestamp + body)
    Header contains: "sha256=<signature>"
    """
    message = message_id.encode() + timestamp.encode() + body

    computed_hash = hmac.new(
        secret.encode(),
        message,
        hashlib.sha256
    ).hexdigest()

    expected_hash = expected_signature.removeprefix("sha256=")
    return hmac.compare_digest(computed_hash, expected_hash)


def is_timestamp_valid(timestamp: str, tolerance_seconds: int = 600) -> bool:
    """
            Check if timestamp is not older than 10 minutes.
            Protects against replay attacks.
    """
    try:
        # Twitch sends nanosecond precision, Python 3.9 only supports microseconds
        # Truncate fractional seconds to 6 digits
        ts = timestamp.replace("Z", "")
        if "." in ts:
            date_part, frac = ts.split(".")
            ts = f"{date_part}.{frac[:6]}"
        ts += "+00:00"

        message_time = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        age = abs((now - message_time).total_seconds())
        return age < tolerance_seconds
    except ValueError:
        return False