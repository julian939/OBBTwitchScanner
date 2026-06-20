from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class StreamTrackerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.guild_scheduled_events = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._notification_task = None
        self.dispatch_loop: asyncio.AbstractEventLoop | None = None

    async def setup_hook(self):
        from app.integrations.discord.commands import register_all
        from app.integrations.discord.notifications import notification_listener

        logger.info("Discord setup_hook gestartet")
        self.dispatch_loop = asyncio.get_running_loop()
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        register_all(self, self.tree)
        guild = discord.Object(id=settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self._notification_task = asyncio.create_task(notification_listener(self))
        logger.info("Discord commands synchronisiert")

    async def on_connect(self):
        logger.info("Discord erfolgreich verbunden")

    async def on_ready(self):
        logger.info("Discord bot ready als %s", self.user)

    def has_active_event(self) -> bool:
        guild = self.get_guild(settings.discord_guild_id)
        if not guild:
            return False
        for event in guild.scheduled_events:
            if event.status == discord.EventStatus.active:
                return True
        return False


bot = StreamTrackerBot()


async def run_bot():
    token = settings.discord_bot_token
    if not token:
        logger.warning("Discord-Bot-Token fehlt, Bot wird übersprungen")
        return
    try:
        logger.info("Discord-Bot startet")
        await bot.start(token)
    except Exception as e:
        logger.exception("Discord-Bot-Fehler: %s", e)
