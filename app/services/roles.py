from __future__ import annotations

import asyncio
from app.config import get_settings

settings = get_settings()


async def _assign_live_role(discord_id: str) -> None:
    """Assign the live role to a Discord user."""
    if not settings.discord_live_role_id or not discord_id:
        return

    try:
        from app.integrations.discord_bot import bot
        guild = bot.get_guild(settings.discord_guild_id)
        if not guild:
            return

        member = guild.get_member(int(discord_id))
        if not member:
            try:
                member = await guild.fetch_member(int(discord_id))
            except Exception:
                return

        role = guild.get_role(settings.discord_live_role_id)
        if not role:
            return

        if role not in member.roles:
            await member.add_roles(role, reason="Streamer went live (tracked category)")
            print(f"🏷️ Assigned live role to {member.display_name}")
    except Exception as e:
        print(f"❌ Failed to assign live role to {discord_id}: {e}")


async def _remove_live_role(discord_id: str) -> None:
    """Remove the live role from a Discord user."""
    if not settings.discord_live_role_id or not discord_id:
        return

    try:
        from app.integrations.discord_bot import bot
        guild = bot.get_guild(settings.discord_guild_id)
        if not guild:
            return

        member = guild.get_member(int(discord_id))
        if not member:
            try:
                member = await guild.fetch_member(int(discord_id))
            except Exception:
                return

        role = guild.get_role(settings.discord_live_role_id)
        if not role:
            return

        if role in member.roles:
            await member.remove_roles(role, reason="Streamer went offline / untracked category")
            print(f"🏷️ Removed live role from {member.display_name}")
    except Exception as e:
        print(f"❌ Failed to remove live role from {discord_id}: {e}")


def assign_live_role(discord_id: str) -> None:
    """Schedule live role assignment from sync context."""
    if not discord_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_assign_live_role(discord_id))
    except RuntimeError:
        pass


def remove_live_role(discord_id: str) -> None:
    """Schedule live role removal from sync context."""
    if not discord_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_remove_live_role(discord_id))
    except RuntimeError:
        pass