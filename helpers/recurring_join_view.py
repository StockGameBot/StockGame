"""Persistent Join Game button for recurring leaderboard channel messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord

if TYPE_CHECKING:
    from stocks import Frontend

logger = logging.getLogger("RecurringJoin")

JOIN_CUSTOM_ID_PREFIX = "recurring_join:"


def recurring_join_view_for_game(game) -> Optional["RecurringJoinView"]:
    """Return a persistent join view for public active recurring games."""
    if getattr(game, "private_game", False):
        return None
    if getattr(game, "status", None) == "ended":
        return None
    if not getattr(game, "template_id", None):
        return None
    return RecurringJoinView(str(game.id))


async def perform_recurring_join(interaction: discord.Interaction, game_id: str) -> None:
    """Join a game from a leaderboard button (mirrors ``/join-game``)."""
    import discord_bot as db
    from helpers import affiliation_views as av
    from helpers import game_invites as gi

    fe: Frontend = db.fe
    ephemeral = db.ephemeral_test

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)

    status = "failed"
    title = "Game Join Failed"
    description = "Unable to join this game."
    participant_status = "active"

    try:
        fe.register(user_id=interaction.user.id, username=interaction.user.display_name)
        pending_invite = fe.get_pending_game_invite(interaction.user.id, game_id)
        try:
            game = fe.be.get_game(game_id)
            force_active = pending_invite is not None and game.private_game
        except LookupError:
            force_active = False
        fe.join_game(
            user_id=interaction.user.id,
            game_id=game_id,
            force_active=force_active,
        )
        game_name = fe._get_game_name(game_id)
        title = "Game Joined Successfully"
        description = f"You have joined **{game_name}** (#{game_id})."
        status = "success"
        try:
            participants = fe.be.get_many_participants(
                user_id=interaction.user.id, game_id=game_id,
            )
            if participants:
                participant_status = participants[0].status
            if participants and participants[0].status == "pending":
                title = "Join Request Submitted"
                description = (
                    f"Your request to join **{game_name}** (#{game_id}) is pending. "
                    "The owner must approve it before you can play."
                )
        except LookupError:
            logger.warning(
                "Could not verify join status for user %s in game %s.",
                interaction.user.id,
                game_id,
            )

        if pending_invite:
            fe.finalize_game_invite(
                game_id=game_id,
                user_id=interaction.user.id,
                status="accepted",
            )
            await gi.edit_invite_dm(
                interaction.client,
                dm_channel_id=pending_invite.dm_channel_id,
                dm_message_id=pending_invite.dm_message_id,
                embed=gi.build_join_result_embed(
                    game_name=game_name,
                    game_id=game_id,
                    participant_status=participant_status,
                ),
            )
    except LookupError:
        description = f"No game with the ID {game_id}."
    except ValueError as exc:
        msg = str(exc).lower()
        if "already in game" in msg:
            description = f"You are already in game **{game_id}**."
        elif "pick_date" in msg:
            description = "The pick date for this game has passed."
        else:
            description = "Unable to join this game. Please check its settings and try again."
    except Exception:
        logger.exception(
            "User %s failed to join game %s from recurring button.",
            interaction.user.id,
            game_id,
        )
        description = (
            f"An unexpected error occurred when joining game {game_id}. "
            "Please try again or use `/join-game`."
        )
    else:
        if status == "success" and participant_status == "active":
            try:
                joined_game = fe.be.get_game(game_id)
                await av.maybe_send_affiliation_prompt(
                    interaction,
                    fe,
                    joined_game,
                    participant_status=participant_status,
                    ephemeral=ephemeral,
                )
            except LookupError:
                pass

    if status == "failed":
        title = "Game Join Failed"

    await interaction.followup.send(
        embed=db.simple_embed(status=status, title=title, desc=description),
        ephemeral=ephemeral,
    )


class RecurringJoinView(discord.ui.View):
    """Persistent view attached to recurring leaderboard push messages."""

    def __init__(self, game_id: str):
        super().__init__(timeout=None)
        self.game_id = str(game_id)
        btn = discord.ui.Button(
            label="Join Game",
            style=discord.ButtonStyle.success,
            custom_id=f"{JOIN_CUSTOM_ID_PREFIX}{self.game_id}",
        )
        btn.callback = self._on_join  # type: ignore[method-assign]
        self.add_item(btn)

    async def _on_join(self, interaction: discord.Interaction) -> None:
        await perform_recurring_join(interaction, self.game_id)


def register_recurring_join_views(bot: discord.Client, fe: Frontend) -> None:
    """Register persistent join views for active public recurring games."""
    try:
        games = fe.be.get_many_games(status=["open", "active"])
    except LookupError:
        return
    seen: set[str] = set()
    for game in games:
        view = recurring_join_view_for_game(game)
        if view is None:
            continue
        key = str(game.id)
        if key in seen:
            continue
        seen.add(key)
        bot.add_view(view)
