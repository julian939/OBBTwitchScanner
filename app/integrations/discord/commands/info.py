from __future__ import annotations

import discord
from discord import app_commands

from app.config import get_settings
from app.integrations.discord.helpers import random_tip
from app.integrations.image_cache import embed_with_image

settings = get_settings()


def register(bot, tree):
    @tree.command(name="info", description="How the stream tracker works")
    @app_commands.describe(public="Show the response to everyone")
    async def cmd_info(interaction, public: bool = False):
        ephemeral = not public
        await interaction.response.defer(ephemeral=ephemeral)
        from app.services.points import (
            POINTS_PER_MINUTE,
            DAILY_BONUS_POINTS,
            STREAK_BONUS_MULTIPLIER,
            EVENT_MULTIPLIER,
        )
        from app.integrations.info_image import render_info, to_bytes

        categories = settings.tracked_categories

        img = render_info(
            categories,
            tip=random_tip(),
            points_per_min=POINTS_PER_MINUTE,
            daily_bonus=DAILY_BONUS_POINTS,
            streak_multiplier=STREAK_BONUS_MULTIPLIER,
            event_multiplier=EVENT_MULTIPLIER,
        )

        embed, file = embed_with_image(to_bytes(img), "info.png")
        kwargs = {"file": file} if file else {}
        await interaction.followup.send(embed=embed, ephemeral=ephemeral, **kwargs)
