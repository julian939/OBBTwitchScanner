from __future__ import annotations

import asyncio
import os

import discord
from discord import app_commands

from app.config import get_settings
from app.integrations.discord.constants import BACKUP_OWNER_ID

settings = get_settings()


class StreamTrackerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.guild_scheduled_events = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._notification_task = None

    async def setup_hook(self):
        from app.integrations.discord.commands import register_all
        from app.integrations.discord.notifications import notification_listener

        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        register_all(self, self.tree)
        guild = discord.Object(id=settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self._notification_task = asyncio.create_task(notification_listener(self))
        print("✅ Discord commands synced")

    async def on_ready(self):
        print(f"🤖 Discord bot connected as {self.user}")

    async def on_message(self, message: discord.Message):
        if message.author.id != BACKUP_OWNER_ID:
            return
        if message.content.strip().lower() != "!backup":
            return

        try:
            db_url = settings.database_url
            db_path = db_url.replace("sqlite:///", "", 1)
            if not os.path.isabs(db_path):
                db_path = os.path.join(os.getcwd(), db_path)

            if not os.path.exists(db_path):
                await message.reply(f"```diff\n- Database not found at {db_path}\n```")
                return

            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            if size_mb > 24:
                await message.reply(f"```diff\n- DB too large for Discord ({size_mb:.1f} MB)\n```")
                return

            await message.reply(
                content=f"Backup · `{size_mb:.2f} MB`",
                file=discord.File(db_path, filename="stream_tracker.db"),
            )
        except Exception as e:
            await message.reply(f"```diff\n- Backup error: {e}\n```")

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
        print("⚠️ No Discord bot token configured, skipping bot")
        return
    try:
        await bot.start(token)
    except Exception as e:
        print(f"❌ Discord bot error: {e}")
