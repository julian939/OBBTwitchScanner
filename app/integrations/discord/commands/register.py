from __future__ import annotations

import discord

from app.integrations.discord.registration import RegisterModal


def register(bot, tree):
    @tree.command(name="register", description="Register as a streamer")
    async def cmd_register(interaction: discord.Interaction):
        await interaction.response.send_modal(RegisterModal())
