from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from sqlalchemy import func

from app.database.database import SessionLocal
from app.database.models import Streamer, PointTransaction
from app.integrations.discord.helpers import (
    get_global_stream_count,
    get_global_total_minutes,
    random_tip,
)
from app.integrations.image_cache import embed_with_image


def register(bot, tree):
    @tree.command(name="stats", description="Global tracking statistics")
    @app_commands.describe(public="Show the response to everyone")
    async def cmd_stats(interaction, public: bool = False):
        ephemeral = not public
        await interaction.response.defer(ephemeral=ephemeral)
        db = SessionLocal()
        try:
            from app.utils.categories import is_streamer_tracked_live
            from app.integrations.stats_image import render_stats, to_bytes

            now = datetime.now(timezone.utc)
            streamers = db.query(Streamer).all()
            tracked_live = [s for s in streamers if s.is_live and is_streamer_tracked_live(s.id)]
            total_streams = get_global_stream_count(db)
            total_min = get_global_total_minutes(db, now)
            total_pts = db.query(func.sum(PointTransaction.points)).scalar() or 0

            live_list = [(s.display_name, s.login) for s in tracked_live]

            img = render_stats({
                "streamers": len(streamers),
                "live_now": len(tracked_live),
                "total_streams": total_streams,
                "total_hours": round(total_min / 60, 1),
                "total_points": total_pts,
                "live_list": live_list,
            }, tip=random_tip())

            embed, file = embed_with_image(to_bytes(img), "stats.png")
            kwargs = {"file": file} if file else {}
            await interaction.followup.send(embed=embed, ephemeral=ephemeral, **kwargs)
        finally:
            db.close()
