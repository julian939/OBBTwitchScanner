from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import ui

from app.config import get_settings
from app.database.database import SessionLocal
from app.database.models import Streamer, RegistrationRequest
from app.database.enums import RegistrationStatus
from app.integrations.discord.constants import ACCENT, OFFLINE_GRAY
from app.integrations.discord.helpers import is_admin

settings = get_settings()
logger = logging.getLogger(__name__)


class RegisterModal(ui.Modal, title="Register as Streamer"):
    twitch_name = ui.TextInput(
        label="Your Twitch Username",
        placeholder="e.g. KaiCenat",
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = SessionLocal()
        try:
            discord_id = str(interaction.user.id)
            discord_username = str(interaction.user)
            twitch_username = self.twitch_name.value.strip().lower()

            existing_pending = (
                db.query(RegistrationRequest)
                .filter(
                    RegistrationRequest.discord_id == discord_id,
                    RegistrationRequest.status == RegistrationStatus.PENDING,
                )
                .first()
            )
            if existing_pending:
                await interaction.followup.send(
                    "```diff\n- You already have a pending registration request\n```",
                    ephemeral=True,
                )
                return

            existing_streamer = db.query(Streamer).filter(Streamer.discord_id == discord_id).first()
            if existing_streamer:
                await interaction.followup.send(
                    f"```diff\n- You are already registered as {existing_streamer.display_name}\n```",
                    ephemeral=True,
                )
                return

            from app.integrations.twitch import twitch_api
            twitch_user = twitch_api.get_user(twitch_username)
            if not twitch_user:
                await interaction.followup.send(
                    f"```diff\n- Twitch user '{twitch_username}' not found\n```",
                    ephemeral=True,
                )
                return

            existing_twitch = db.query(Streamer).filter(Streamer.login == twitch_username).first()
            if existing_twitch:
                if existing_twitch.discord_id:
                    await interaction.followup.send(
                        f"```diff\n- '{twitch_user['display_name']}' is already registered by another user\n```",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        f"```diff\n- '{twitch_user['display_name']}' is already being tracked\n```\n"
                        f"Contact an admin to link your Discord account.",
                        ephemeral=True,
                    )
                return

            existing_twitch_request = (
                db.query(RegistrationRequest)
                .filter(
                    RegistrationRequest.twitch_username == twitch_username,
                    RegistrationRequest.status == RegistrationStatus.PENDING,
                )
                .first()
            )
            if existing_twitch_request:
                await interaction.followup.send(
                    f"```diff\n- There is already a pending request for '{twitch_user['display_name']}'\n```",
                    ephemeral=True,
                )
                return

            request = RegistrationRequest(
                discord_id=discord_id,
                discord_username=discord_username,
                twitch_username=twitch_username,
                status=RegistrationStatus.PENDING,
            )
            db.add(request)
            db.commit()

            embed = discord.Embed(color=ACCENT)
            embed.description = (
                f"```diff\n+ Registration request submitted!\n```\n"
                f"Twitch: **{twitch_user['display_name']}** (`{twitch_username}`)\n\n"
                f"An admin will review your request."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            channel_id = settings.discord_registration_channel_id
            admin_ids = settings.discord_admin_user_ids
            if channel_id:
                try:
                    channel = interaction.client.get_channel(channel_id)
                    if channel is None:
                        channel = await interaction.client.fetch_channel(channel_id)
                    mentions = " ".join(f"<@{uid}>" for uid in admin_ids)
                    admin_embed = discord.Embed(
                        title="New Registration Request",
                        color=ACCENT,
                        description=(
                            f"**{discord_username}** wants to register as a streamer.\n\n"
                            f"Twitch: **{twitch_user['display_name']}** (`{twitch_username}`)\n"
                            f"Discord: <@{discord_id}>"
                        ),
                    )
                    review_view = RegistrationReviewView(
                        request_id=request.id,
                        twitch_username=twitch_username,
                        discord_id=discord_id,
                        discord_username=discord_username,
                    )
                    await channel.send(content=mentions or None, embed=admin_embed, view=review_view)
                except Exception:
                    logger.exception("Admin-Benachrichtigung für Registrierungsanfrage fehlgeschlagen")

        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()


class RegistrationReviewView(ui.View):
    def __init__(self, request_id: int, twitch_username: str, discord_id: str, discord_username: str):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.twitch_username = twitch_username
        self.discord_id = discord_id
        self.discord_username = discord_username

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_admin(interaction):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return False
        return True

    @ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        db = SessionLocal()
        try:
            req = db.query(RegistrationRequest).filter(RegistrationRequest.id == self.request_id).first()
            if not req or req.status != RegistrationStatus.PENDING:
                await interaction.followup.send("```diff\n- Request no longer pending\n```", ephemeral=True)
                return

            from app.services.subscription import add_streamer
            from app.services.reconciliation import reconcile_live_states
            result = add_streamer(self.twitch_username, db, discord_id=self.discord_id)
            reconcile_live_states(db)

            req.status = RegistrationStatus.APPROVED
            req.reviewed_at = datetime.now(timezone.utc)
            req.reviewed_by = str(interaction.user)
            db.commit()

            for item in self.children:
                item.disabled = True

            result_embed = discord.Embed(color=ACCENT)
            result_embed.description = (
                f"~~**Registration Request #{self.request_id}**~~\n\n"
                f"Discord: <@{self.discord_id}> (`{self.discord_username}`)\n"
                f"Twitch: `{self.twitch_username}`\n\n"
                f"```diff\n+ Approved by {interaction.user.display_name}\n```"
            )
            await interaction.edit_original_response(embed=result_embed, view=self)

            await _send_registration_dm(
                interaction.guild, self.discord_id,
                approved=True, twitch_name=result["display_name"],
            )

        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()

    @ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button):
        await interaction.response.defer()
        db = SessionLocal()
        try:
            req = db.query(RegistrationRequest).filter(RegistrationRequest.id == self.request_id).first()
            if not req or req.status != RegistrationStatus.PENDING:
                await interaction.followup.send("```diff\n- Request no longer pending\n```", ephemeral=True)
                return

            req.status = RegistrationStatus.REJECTED
            req.reviewed_at = datetime.now(timezone.utc)
            req.reviewed_by = str(interaction.user)
            db.commit()

            for item in self.children:
                item.disabled = True

            result_embed = discord.Embed(color=OFFLINE_GRAY)
            result_embed.description = (
                f"~~**Registration Request #{self.request_id}**~~\n\n"
                f"Discord: <@{self.discord_id}> (`{self.discord_username}`)\n"
                f"Twitch: `{self.twitch_username}`\n\n"
                f"```diff\n- Rejected by {interaction.user.display_name}\n```"
            )
            await interaction.edit_original_response(embed=result_embed, view=self)

            await _send_registration_dm(
                interaction.guild, self.discord_id,
                approved=False, twitch_name=self.twitch_username,
            )

        except Exception as e:
            await interaction.followup.send(f"```diff\n- Error: {e}\n```", ephemeral=True)
        finally:
            db.close()


async def _send_registration_dm(guild, discord_id: str, approved: bool, twitch_name: str):
    try:
        member = guild.get_member(int(discord_id))
        if not member:
            member = await guild.fetch_member(int(discord_id))
        if not member:
            return

        if approved:
            embed = discord.Embed(color=ACCENT)
            embed.description = (
                f"```diff\n+ Your registration has been approved!\n```\n"
                f"Your Twitch account **{twitch_name}** is now being tracked.\n"
                f"Happy streaming! 🎮"
            )
        else:
            embed = discord.Embed(color=OFFLINE_GRAY)
            embed.description = (
                f"```diff\n- Your registration has been rejected\n```\n"
                f"Your request to register **{twitch_name}** was not approved.\n"
                f"Contact an admin if you think this is a mistake."
            )

        await member.send(embed=embed)
    except discord.Forbidden:
        logger.warning("Kann User %s nicht per DM erreichen (DMs deaktiviert)", discord_id)
    except Exception as e:
        logger.exception("Registrierungs-DM konnte nicht gesendet werden: %s", e)
