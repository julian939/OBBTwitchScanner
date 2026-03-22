"""
Temporary image hosting for Discord embeds.

Stores generated PNGs in memory with a short TTL so Discord can
fetch them via URL instead of file attachments (fixes mobile rendering).
"""

from __future__ import annotations

import io
import uuid
import time

import discord
from fastapi import APIRouter
from fastapi.responses import Response

from app.config import get_settings

settings = get_settings()

router = APIRouter()

_IMAGE_TTL_SECONDS = 910  # 15 minutes and 10 seconds (buffer)
_store: dict[str, tuple[bytes, float]] = {}  # id -> (png_bytes, created_at)


def store_image(png_bytes: bytes) -> str:
    """Store a PNG and return its unique ID."""
    _cleanup()
    image_id = uuid.uuid4().hex
    _store[image_id] = (png_bytes, time.time())
    return image_id


def get_image_url(image_id: str) -> str:
    """Get the full URL for a stored image."""
    base = settings.base_url.rstrip("/")
    return f"{base}/images/{image_id}.png?t={int(time.time())}"


def _cleanup():
    """Remove expired images."""
    now = time.time()
    expired = [k for k, (_, created) in _store.items() if now - created > _IMAGE_TTL_SECONDS]
    for k in expired:
        del _store[k]


ACCENT = 0x9146FF  # Twitch purple


def embed_with_image(png_bytes: bytes, filename: str) -> tuple[discord.Embed, discord.File | None]:
    """Return (embed, file_or_none). Pass file as kwarg only when not None."""
    embed = discord.Embed(color=ACCENT)
    if settings.local_dev:
        file = discord.File(io.BytesIO(png_bytes), filename=filename)
        embed.set_image(url=f"attachment://{filename}")
        return embed, file
    else:
        url = get_image_url(store_image(png_bytes))
        embed.set_image(url=url)
        return embed, None


@router.get("/images/{image_id}.png")
async def serve_image(image_id: str):
    _cleanup()
    entry = _store.get(image_id)
    if not entry:
        return Response(status_code=404)
    png_bytes, _ = entry
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )