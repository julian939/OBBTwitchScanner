from __future__ import annotations

import discord
from discord import app_commands
from sqlalchemy import func

from app.database.database import SessionLocal
from app.database.models import Streamer, RegistrationRequest
from app.database.enums import RegistrationStatus
from app.integrations.discord.constants import ACCENT
from app.integrations.discord.helpers import is_admin
from app.integrations.discord.admin import AddStreamerContextModal, AdminView


def register(bot, tree):
    @tree.context_menu(name="Add as Streamer")
    async def ctx_add_streamer(interaction: discord.Interaction, member: discord.Member):
        if not is_admin(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return
        modal = AddStreamerContextModal(
            target_discord_id=str(member.id),
            target_name=str(member),
        )
        await interaction.response.send_modal(modal)

    @tree.context_menu(name="Remove Streamer")
    async def ctx_remove_streamer(interaction: discord.Interaction, member: discord.Member):
        if not is_admin(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            streamer = db.query(Streamer).filter(Streamer.discord_id == str(member.id)).first()
            if not streamer:
                await interaction.followup.send(
                    f"```diff\n- {member.display_name} is not registered as a streamer\n```",
                    ephemeral=True,
                )
                return

            from app.services.subscription import remove_streamer, sync_subscriptions
            display_name = streamer.display_name
            remove_streamer(streamer.login, db)
            sync_subscriptions(db)

            embed = discord.Embed(color=ACCENT)
            embed.description = (
                f"```diff\n- Removed {display_name}\n```\n"
                f"Discord: {member.mention}  ·  Twitch: `{streamer.login}`"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except (ValueError, Exception) as e:
            await interaction.followup.send(f"```diff\n- {e}\n```", ephemeral=True)
        finally:
            db.close()

    @tree.command(name="admin", description="Stream Tracker admin panel")
    async def cmd_admin(interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return
        db = SessionLocal()
        try:
            pending_count = (
                db.query(func.count(RegistrationRequest.id))
                .filter(RegistrationRequest.status == RegistrationStatus.PENDING)
                .scalar()
            )
            embed = discord.Embed(title="Admin Panel", description="Select an action below.", color=ACCENT)
            await interaction.response.send_message(embed=embed, view=AdminView(pending_count=pending_count),
                                                    ephemeral=True)
        finally:
            db.close()
