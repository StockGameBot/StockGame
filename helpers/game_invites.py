"""DM game invite views and message helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from stocks import Frontend

logger = logging.getLogger(__name__)

# Discord message components stop responding after ~15 minutes; match that window.
INVITE_VIEW_TIMEOUT = 900.0


def build_invite_embed(game_name: str, game_id: str, inviter_name: str) -> discord.Embed:
    return discord.Embed(
        title="Game Invite",
        description=(
            f"You have been invited to **{game_name}** (#{game_id}) by {inviter_name}."
        ),
        color=discord.Color.green(),
    )


def build_expired_invite_embed(game_name: str, game_id: str) -> discord.Embed:
    return discord.Embed(
        title="Invite Buttons Expired",
        description=(
            f"The invite buttons for **{game_name}** (#{game_id}) have expired.\n\n"
            f"You can still join with `/join-game` and enter game ID `{game_id}`, "
            "or ask the game owner to send you a new invite."
        ),
        color=discord.Color.orange(),
    )


def build_join_result_embed(
    *,
    game_name: str,
    game_id: str,
    participant_status: str,
    failed: bool = False,
    error_message: str | None = None,
) -> discord.Embed:
    if failed:
        return discord.Embed(
            title="Game Join Failed",
            description=error_message or "Unable to join the game.",
            color=discord.Color.red(),
        )
    if participant_status == "pending":
        return discord.Embed(
            title="Join Request Submitted",
            description=(
                "Your request is pending owner approval before you can play."
            ),
            color=discord.Color.green(),
        )
    return discord.Embed(
        title="Game Joined",
        description=f"You joined **{game_name}** (#{game_id}).",
        color=discord.Color.green(),
    )


def build_declined_invite_embed(game_id: str) -> discord.Embed:
    return discord.Embed(
        title="Invite Declined",
        description=f"You have declined the invite to game #{game_id}.",
        color=discord.Color.red(),
    )


async def edit_invite_dm(
    client: discord.Client,
    *,
    dm_channel_id: int | None,
    dm_message_id: int | None,
    embed: discord.Embed,
) -> None:
    if dm_channel_id is None or dm_message_id is None:
        return
    try:
        channel = await client.fetch_channel(dm_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        message = await channel.fetch_message(dm_message_id)
        await message.edit(embed=embed, view=None)
    except discord.HTTPException:
        logger.debug("Could not update invite DM message.", exc_info=True)


async def expire_superseded_invite_dm(
    client: discord.Client,
    fe: Frontend,
    invite,
) -> None:
    if invite is None or invite.dm_channel_id is None or invite.dm_message_id is None:
        return
    try:
        game = fe.be.get_game(game_id=invite.game_id)
        game_name = game.name
    except LookupError:
        game_name = invite.game_id
    await edit_invite_dm(
        client,
        dm_channel_id=invite.dm_channel_id,
        dm_message_id=invite.dm_message_id,
        embed=build_expired_invite_embed(game_name, str(invite.game_id)),
    )


def perform_invited_join(
    fe: Frontend,
    *,
    user_id: int,
    username: str | None,
    game_id: str,
    private_game: bool,
    had_pending_invite: bool,
) -> tuple[discord.Embed, bool]:
    """Join a game from an invite flow. Returns ``(embed, success)``."""
    force_active = had_pending_invite and private_game
    try:
        fe.register(user_id=user_id, username=username)
        fe.join_game(user_id=user_id, game_id=game_id, force_active=force_active)
        if had_pending_invite:
            fe.finalize_game_invite(game_id=game_id, user_id=user_id, status="accepted")
        participant = fe.be.get_many_participants(user_id=user_id, game_id=game_id)[0]
        game_name = fe._get_game_name(game_id)
        return (
            build_join_result_embed(
                game_name=game_name,
                game_id=game_id,
                participant_status=participant.status,
            ),
            True,
        )
    except ValueError as exc:
        message = str(exc)
        if "already in game" in message.lower():
            message = "You are already participating in this game."
            if had_pending_invite:
                fe.finalize_game_invite(game_id=game_id, user_id=user_id, status="accepted")
        elif "pick_date" in message.lower():
            message = "The pick deadline for this game has passed."
        return (
            build_join_result_embed(
                game_name=fe._get_game_name(game_id),
                game_id=game_id,
                participant_status="active",
                failed=True,
                error_message=message,
            ),
            False,
        )
    except LookupError:
        return (
            build_join_result_embed(
                game_name=game_id,
                game_id=game_id,
                participant_status="active",
                failed=True,
                error_message="This game is no longer available.",
            ),
            False,
        )
    except Exception:
        logger.exception("Invite join failed for user %s game %s.", user_id, game_id)
        return (
            discord.Embed(
                title="Game Join Failed",
                description="Unable to join the game. Please try again or contact a moderator.",
                color=discord.Color.red(),
            ),
            False,
        )


class GameInviteView(discord.ui.View):
    def __init__(
        self,
        *,
        fe: Frontend,
        game_id: str,
        invitee_id: int,
        game_name: str,
        inviter_name: str,
        private_game: bool,
    ):
        super().__init__(timeout=INVITE_VIEW_TIMEOUT)
        self.fe = fe
        self.game_id = game_id
        self.invitee_id = invitee_id
        self.game_name = game_name
        self.inviter_name = inviter_name
        self.private_game = private_game
        self.message: discord.Message | None = None

        accept_button = discord.ui.Button(
            label="Accept Invite",
            style=discord.ButtonStyle.success,
            custom_id="accept_invite",
            emoji="✅",
        )
        decline_button = discord.ui.Button(
            label="Decline Invite",
            style=discord.ButtonStyle.danger,
            custom_id="decline_invite",
            emoji="❌",
        )
        accept_button.callback = self._accept_callback  # type: ignore[assignment]
        decline_button.callback = self._decline_callback  # type: ignore[assignment]
        self.add_item(accept_button)
        self.add_item(decline_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.invitee_id:
            return True
        await interaction.response.send_message(
            "This invite was not meant for you.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            embed = build_expired_invite_embed(self.game_name, self.game_id)
            await self.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            logger.debug("Could not expire invite buttons on DM message.", exc_info=True)

    async def _accept_callback(self, interaction: discord.Interaction) -> None:
        embed, success = perform_invited_join(
            self.fe,
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            game_id=self.game_id,
            private_game=self.private_game,
            had_pending_invite=True,
        )
        await interaction.response.edit_message(embed=embed, view=None)
        if success:
            try:
                game = self.fe.be.get_game(self.game_id)
                participant = self.fe.be.get_many_participants(
                    user_id=interaction.user.id,
                    game_id=self.game_id,
                )[0]
                from helpers import affiliation_views as av

                await av.maybe_send_affiliation_prompt(
                    interaction,
                    self.fe,
                    game,
                    participant_status=participant.status,
                    ephemeral=True,
                )
            except LookupError:
                pass

    async def _decline_callback(self, interaction: discord.Interaction) -> None:
        self.fe.finalize_game_invite(
            game_id=self.game_id,
            user_id=interaction.user.id,
            status="declined",
        )
        embed = build_declined_invite_embed(self.game_id)
        await interaction.response.edit_message(embed=embed, view=None)
