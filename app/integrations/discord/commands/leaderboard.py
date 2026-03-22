from __future__ import annotations

import io
from datetime import datetime, timezone

import discord
from discord import app_commands
from sqlalchemy import func

from app.database.database import SessionLocal
from app.database.models import Streamer, PointTransaction
from app.integrations.discord.constants import ACCENT, LEADERBOARD_PAGE_SIZE
from app.config import get_settings

settings = get_settings()
from app.integrations.discord.helpers import (
    get_streamer_total_minutes,
    get_all_streams_count,
    get_streak,
    random_tip,
)
from app.integrations.discord.views import PaginatorView
from app.integrations.image_cache import embed_with_image


def build_leaderboard_images(entries):
    """Build paginated leaderboard images as list of PNG bytes."""
    from app.integrations.leaderboard_image import render_leaderboard

    if not entries:
        return None

    total_pages = (len(entries) + LEADERBOARD_PAGE_SIZE - 1) // LEADERBOARD_PAGE_SIZE
    image_bytes = []

    for page_start in range(0, len(entries), LEADERBOARD_PAGE_SIZE):
        page_entries = entries[page_start:page_start + LEADERBOARD_PAGE_SIZE]
        page_num = page_start // LEADERBOARD_PAGE_SIZE + 1

        img = render_leaderboard(page_entries, page=page_num, total_pages=total_pages, tip=random_tip())
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        image_bytes.append(buf.getvalue())

    return image_bytes


def register(bot, tree):
    @tree.command(name="leaderboard", description="Points leaderboard")
    @app_commands.describe(public="Show the response to everyone")
    async def cmd_leaderboard(interaction, public: bool = False):
        ephemeral = not public
        await interaction.response.defer(ephemeral=ephemeral)
        db = SessionLocal()
        try:
            from app.utils.categories import is_streamer_tracked_live

            now = datetime.now(timezone.utc)

            results = (
                db.query(
                    Streamer.display_name, Streamer.login, Streamer.is_live,
                    Streamer.id.label("sid"),
                    func.coalesce(func.sum(PointTransaction.points), 0).label("pts"),
                )
                .outerjoin(PointTransaction, PointTransaction.streamer_id == Streamer.id)
                .filter(Streamer.is_locked == False)
                .group_by(Streamer.id)
                .order_by(func.coalesce(func.sum(PointTransaction.points), 0).desc())
                .all()
            )

            entries = []
            for i, r in enumerate(results):
                tracked_live = r.is_live and is_streamer_tracked_live(r.sid)
                mins = get_streamer_total_minutes(r.sid, db, now)
                streams = get_all_streams_count(r.sid, db)
                streak = get_streak(r.sid, db)

                entries.append({
                    "rank": i + 1,
                    "display_name": r.display_name,
                    "login": r.login,
                    "is_live": r.is_live,
                    "tracked_live": tracked_live,
                    "pts": r.pts,
                    "hours": round(mins / 60, 1),
                    "streams": streams,
                    "streak": streak,
                })

            image_bytes = build_leaderboard_images(entries)

            if image_bytes is None:
                embed = discord.Embed(title="OBB Streamer Leaderboard", color=ACCENT)
                embed.description = "```\n  No data yet.\n```"
                embed.set_footer(text=random_tip())
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            elif len(image_bytes) == 1:
                embed, file = embed_with_image(image_bytes[0], "leaderboard.png")
                kwargs = {"file": file} if file else {}
                await interaction.followup.send(embed=embed, ephemeral=ephemeral, **kwargs)
            else:
                pages = []
                for img_bytes in image_bytes:
                    embed, _ = embed_with_image(img_bytes, "leaderboard.png")
                    pages.append(embed)

                image_bytes_for_view = image_bytes if settings.local_dev else None
                view = PaginatorView(pages, image_bytes=image_bytes_for_view)
                first_embed = pages[0]
                if settings.local_dev:
                    first_file = discord.File(io.BytesIO(image_bytes[0]), filename="leaderboard.png")
                    msg = await interaction.followup.send(embed=first_embed, file=first_file, view=view, ephemeral=ephemeral)
                else:
                    msg = await interaction.followup.send(embed=first_embed, view=view, ephemeral=ephemeral)
                view.message = msg
        finally:
            db.close()
