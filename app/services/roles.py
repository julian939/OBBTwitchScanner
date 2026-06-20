"""Discord role management — live roles and leaderboard roles."""
from __future__ import annotations

import asyncio
import logging
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import get_settings
from app.database.models import Streamer, PointTransaction

settings = get_settings()
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  Live Role (sync helper — fire-and-forget from sync code)
# ═══════════════════════════════════════════════════════════

def _get_bot():
    """Lazy import to avoid circular imports."""
    from app.integrations.discord_bot import bot
    return bot


def _run_coro(coro):
    """Schedule a coroutine on the bot's event loop (non-blocking)."""
    scheduled = False
    try:
        bot = _get_bot()
        loop = getattr(bot, "dispatch_loop", None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
            scheduled = True
            return
    except Exception:
        logger.exception("Konnte Coroutine nicht auf Discord-Loop schedulen")
    # Coroutine was never scheduled — close it to suppress RuntimeWarning
    if not scheduled:
        coro.close()


def _get_guild():
    """Get the configured guild object."""
    bot = _get_bot()
    return bot.get_guild(settings.discord_guild_id)


async def _get_member(guild, discord_id: str):
    """Get a guild member, fetching if needed."""
    member = guild.get_member(int(discord_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(discord_id))
        except Exception:
            logger.warning("Konnte Discord-Mitglied %s nicht fetchen", discord_id, exc_info=True)
            return None
    return member


# ── Live role (called from reconciliation/stream_tracker) ──

def assign_live_role(discord_id: str):
    """Give the live role to a member."""
    role_id = settings.discord_live_role_id
    if not role_id or not discord_id:
        return

    async def _do():
        guild = _get_guild()
        if not guild:
            return
        role = guild.get_role(role_id)
        if not role:
            return
        member = await _get_member(guild, discord_id)
        if member and role not in member.roles:
            await member.add_roles(role, reason="Stream went live (tracked)")

    _run_coro(_do())


def remove_live_role(discord_id: str):
    """Remove the live role from a member."""
    role_id = settings.discord_live_role_id
    if not role_id or not discord_id:
        return

    async def _do():
        guild = _get_guild()
        if not guild:
            return
        role = guild.get_role(role_id)
        if not role:
            return
        member = await _get_member(guild, discord_id)
        if member and role in member.roles:
            await member.remove_roles(role, reason="Stream ended or untracked")

    _run_coro(_do())


# ═══════════════════════════════════════════════════════════
#  Leaderboard Role Sync (idempotent)
# ═══════════════════════════════════════════════════════════

async def sync_leaderboard_roles(db: Session):
    """Sync top-1 and top-5 Discord roles based on current points.

    Idempotent: computes desired state and applies diff.
    Safe to call frequently.
    """
    top1_role_id = settings.discord_top1_role_id
    top5_role_id = settings.discord_top5_role_id

    # Nothing to do if no roles configured
    if not top1_role_id and not top5_role_id:
        return

    guild = _get_guild()
    if not guild:
        return

    top1_role = guild.get_role(top1_role_id) if top1_role_id else None
    top5_role = guild.get_role(top5_role_id) if top5_role_id else None

    if not top1_role and not top5_role:
        return

    # ── Query top 5 by total points ──
    top_rows = (
        db.query(
            Streamer.discord_id,
            func.coalesce(func.sum(PointTransaction.points), 0).label("pts"),
        )
        .outerjoin(PointTransaction, PointTransaction.streamer_id == Streamer.id)
        .filter(Streamer.discord_id.isnot(None), Streamer.is_locked == False)
        .group_by(Streamer.id)
        .order_by(func.coalesce(func.sum(PointTransaction.points), 0).desc())
        .limit(5)
        .all()
    )

    # Filter out zero-point entries
    top = [(row.discord_id, row.pts) for row in top_rows if row.pts > 0]

    # ── Desired state ──
    should_have_top1: set[str] = set()
    should_have_top5: set[str] = set()

    if top:
        should_have_top1.add(top[0][0])  # #1 gets top1 role
        for discord_id, pts in top[1:5]:  # #2-#5 get top5 role
            should_have_top5.add(discord_id)

    # ── Current state ──
    has_top1: set[str] = set()
    has_top5: set[str] = set()

    if top1_role:
        has_top1 = {str(m.id) for m in top1_role.members}
    if top5_role:
        has_top5 = {str(m.id) for m in top5_role.members}

    # ── Apply diff ──
    # Remove top1 from people who shouldn't have it
    if top1_role:
        for discord_id in has_top1 - should_have_top1:
            member = await _get_member(guild, discord_id)
            if member:
                await member.remove_roles(top1_role, reason="No longer #1 on leaderboard")

        # Add top1 to people who should have it
        for discord_id in should_have_top1 - has_top1:
            member = await _get_member(guild, discord_id)
            if member:
                await member.add_roles(top1_role, reason="Reached #1 on leaderboard")

    # Remove top5 from people who shouldn't have it
    if top5_role:
        for discord_id in has_top5 - should_have_top5:
            member = await _get_member(guild, discord_id)
            if member:
                await member.remove_roles(top5_role, reason="No longer top 2-5 on leaderboard")

        # Add top5 to people who should have it
        for discord_id in should_have_top5 - has_top5:
            member = await _get_member(guild, discord_id)
            if member:
                await member.add_roles(top5_role, reason="Reached top 2-5 on leaderboard")

    # ── Ensure no one has BOTH roles ──
    # #1 should not have top5 role
    if top5_role:
        for discord_id in should_have_top1:
            if discord_id in has_top5:
                member = await _get_member(guild, discord_id)
                if member and top5_role in member.roles:
                    await member.remove_roles(top5_role, reason="#1 doesn't need top 2-5 role")

    # #2-5 should not have top1 role
    if top1_role:
        for discord_id in should_have_top5:
            if discord_id in has_top1:
                member = await _get_member(guild, discord_id)
                if member and top1_role in member.roles:
                    await member.remove_roles(top1_role, reason="Top 2-5 doesn't need #1 role")


def schedule_leaderboard_role_sync():
    """Fire-and-forget: schedule a leaderboard role sync on the bot loop.

    Safe to call from synchronous code (webhook handlers, etc.).
    Creates its own DB session.
    """
    async def _do():
        try:
            from app.database.database import SessionLocal
            db = SessionLocal()
            try:
                await sync_leaderboard_roles(db)
            finally:
                db.close()
        except Exception as e:
            logger.exception("Leaderboard-Rollensync fehlgeschlagen: %s", e)

    _run_coro(_do())
