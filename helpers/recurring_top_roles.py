"""Assign 1st/2nd/3rd Discord roles when recurring games end."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord

if TYPE_CHECKING:
    from discord.ext import commands
    from stocks import Frontend

logger = logging.getLogger("RecurringTopRoles")

# Same guild as discord_bot.HOME_GUILD_ID
HOME_GUILD_ID = 1358170062762283119

TOP_ROLE_IDS: dict[int, int] = {
    1: 1539020215860338688,
    2: 1539020212828110938,
    3: 1539020204229529690,
}


def _role_objects(guild: discord.Guild) -> dict[int, discord.Role]:
    roles: dict[int, discord.Role] = {}
    for rank, role_id in TOP_ROLE_IDS.items():
        role = guild.get_role(role_id)
        if role is not None:
            roles[rank] = role
    return roles


def _bot_can_manage_roles(guild: discord.Guild, me: discord.Member, roles: dict[int, discord.Role]) -> bool:
    if not me.guild_permissions.manage_roles:
        logger.warning("Bot lacks Manage Roles in guild %s; skipping auto top roles.", guild.id)
        return False
    bot_top = me.top_role
    for rank, role in roles.items():
        if role >= bot_top:
            logger.warning(
                "Bot role hierarchy too low for rank %s role %s in guild %s.",
                rank,
                role.id,
                guild.id,
            )
            return False
    return True


async def _resolve_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        logger.warning("User %s left guild %s before role update.", user_id, guild.id)
        return None
    except discord.HTTPException as exc:
        logger.warning("Could not fetch member %s in guild %s: %s", user_id, guild.id, exc)
        return None


async def _remove_role(member: discord.Member, role: discord.Role) -> None:
    if role not in member.roles:
        logger.info("User %s does not have role %s; skip remove.", member.id, role.id)
        return
    try:
        await member.remove_roles(role, reason="Recurring game top-role rotation")
    except discord.HTTPException as exc:
        logger.warning("Failed to remove role %s from user %s: %s", role.id, member.id, exc)


async def _add_role(member: discord.Member, role: discord.Role) -> None:
    if role in member.roles:
        return
    try:
        await member.add_roles(role, reason="Recurring game top-3 finish")
    except discord.HTTPException as exc:
        logger.warning("Failed to add role %s to user %s: %s", role.id, member.id, exc)


async def _strip_holders(
    guild: discord.Guild,
    holders: tuple,
    roles: dict[int, discord.Role],
) -> None:
    for holder in holders:
        role = roles.get(holder.rank)
        if role is None:
            continue
        member = await _resolve_member(guild, int(holder.user_id))
        if member is None:
            continue
        await _remove_role(member, role)


async def _apply_ranked_users(
    guild: discord.Guild,
    ranked_user_ids: list[int],
    roles: dict[int, discord.Role],
) -> None:
    for rank, user_id in enumerate(ranked_user_ids[:3], start=1):
        role = roles.get(rank)
        if role is None:
            continue
        member = await _resolve_member(guild, user_id)
        if member is None:
            continue
        await _add_role(member, role)


def _ranked_user_ids(fe: Frontend, game_id: str) -> list[int]:
    try:
        participants = fe.be.get_many_participants(
            game_id=game_id,
            status='active',
            sort_by_value=True,
        )
    except LookupError:
        return []
    ranked: list[int] = []
    seen: set[int] = set()
    for participant in participants:
        uid = int(participant.user_id)
        if uid in seen:
            continue
        seen.add(uid)
        ranked.append(uid)
        if len(ranked) >= 3:
            break
    return ranked


async def strip_template_top_roles(
    bot: commands.Bot,
    fe: Frontend,
    template_id: int,
) -> None:
    """Remove Discord roles from tracked holders and clear DB rows."""
    guild = bot.get_guild(HOME_GUILD_ID)
    if guild is None:
        logger.warning("Home guild %s not available; cannot strip top roles.", HOME_GUILD_ID)
        return
    me = guild.me
    if me is None:
        return
    roles = _role_objects(guild)
    if not roles:
        logger.warning("Top role IDs not found in guild %s.", guild.id)
        return
    holders = fe.be.get_template_role_holders(template_id)
    await _strip_holders(guild, holders, roles)
    fe.be.clear_template_role_holders(template_id)


async def sync_recurring_top_roles(bot: commands.Bot, fe: Frontend) -> None:
    """Process ended recurring games with auto_top_roles enabled."""
    guild = bot.get_guild(HOME_GUILD_ID)
    if guild is None:
        logger.warning("Home guild %s not available; skipping auto top roles.", HOME_GUILD_ID)
        return
    me = guild.me
    if me is None:
        return
    roles = _role_objects(guild)
    if not roles:
        logger.warning("Top role IDs not found in guild %s; skipping auto top roles.", guild.id)
        return
    if not _bot_can_manage_roles(guild, me, roles):
        return

    pending = fe.be.get_games_pending_top_roles()
    for game in pending:
        if game.template_id is None:
            fe.be.update_game(game_id=game.id, top_roles_applied=True)
            continue
        try:
            template = fe.be.get_game_template(int(game.template_id))
        except LookupError:
            fe.be.update_game(game_id=game.id, top_roles_applied=True)
            continue
        if not template.auto_top_roles:
            fe.be.update_game(game_id=game.id, top_roles_applied=True)
            continue

        template_id = int(template.id)
        prior_holders = fe.be.get_template_role_holders(template_id)
        await _strip_holders(guild, prior_holders, roles)

        ranked = _ranked_user_ids(fe, str(game.id))
        await _apply_ranked_users(guild, ranked, roles)

        fe.be.replace_template_role_holders(
            template_id,
            game_id=str(game.id),
            ranked_user_ids=ranked,
        )
        fe.be.update_game(game_id=game.id, top_roles_applied=True)
        logger.info(
            "Auto top roles applied for game %s template %s: %s",
            game.id,
            template_id,
            ranked,
        )
