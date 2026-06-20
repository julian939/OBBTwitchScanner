from __future__ import annotations

import logging

from app.integrations.discord.commands import (
    register as _register_mod,
    live,
    leaderboard,
    streamer,
    stats,
    info,
    admin_cmd,
)

logger = logging.getLogger(__name__)


def register_all(bot, tree):
    @tree.error
    async def on_error(interaction, error):
        logger.exception("Discord-Commandfehler: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred.", ephemeral=True)

    _register_mod.register(bot, tree)
    live.register(bot, tree)
    leaderboard.register(bot, tree)
    streamer.register(bot, tree)
    stats.register(bot, tree)
    info.register(bot, tree)
    admin_cmd.register(bot, tree)
