"""Discord UI for recurring hedge-fund affiliation selection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

import discord

from helpers.affiliations import (
    AFFILIATION_AIDEN,
    AFFILIATION_ATRIOC,
    AFFILIATION_DOUGDOUG,
    AFFILIATION_DISPLAY,
    AFFILIATION_KEYS,
    AFFILIATION_WORKING_CLASS,
    AFFILIATION_WARNING,
    is_affiliations_enabled,
    normalize_affiliation,
)

SelectCallback = Callable[[discord.Interaction, str | None], Awaitable[None]]


def _affiliation_label(key: str | None) -> str:
    if key is None:
        return "Independent (solo fund)"
    return AFFILIATION_DISPLAY.get(key, key)


class AffiliationSelect(discord.ui.Select):
    """Dropdown for picking a hedge-fund team."""

    def __init__(self, *, on_chosen: Optional[SelectCallback] = None) -> None:
        options = [
            discord.SelectOption(
                label="Independent (solo fund)",
                value="__none__",
                description="No fund — play on your own",
            ),
            discord.SelectOption(
                label=AFFILIATION_DISPLAY[AFFILIATION_ATRIOC],
                value=AFFILIATION_ATRIOC,
            ),
            discord.SelectOption(
                label=AFFILIATION_DISPLAY[AFFILIATION_DOUGDOUG],
                value=AFFILIATION_DOUGDOUG,
            ),
            discord.SelectOption(
                label=AFFILIATION_DISPLAY[AFFILIATION_AIDEN],
                value=AFFILIATION_AIDEN,
            ),
            discord.SelectOption(
                label=AFFILIATION_DISPLAY[AFFILIATION_WORKING_CLASS],
                value=AFFILIATION_WORKING_CLASS,
            ),
        ]
        super().__init__(
            placeholder="Choose your fund…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._on_chosen = on_chosen

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AffiliationSelectView = self.view  # type: ignore[assignment]
        if interaction.user.id != view.user_id:
            await interaction.response.send_message(
                "Only you can choose a fund here.",
                ephemeral=True,
            )
            return
        raw = self.values[0]
        affiliation = None if raw == "__none__" else raw
        try:
            participant = view.fe.set_participant_affiliation(
                view.user_id,
                view.game_id,
                affiliation,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except LookupError:
            await interaction.response.send_message(
                "You are not an active player in this game.",
                ephemeral=True,
            )
            return

        label = _affiliation_label(getattr(participant, "affiliation", None))
        self.disabled = True
        await interaction.response.edit_message(
            content=(
                f"✅ Fund set to **{label}** for game **#{view.game_id}**.\n"
                f"{AFFILIATION_WARNING}"
            ),
            view=view,
        )
        if self._on_chosen is not None:
            await self._on_chosen(interaction, getattr(participant, "affiliation", None))


class AffiliationSelectView(discord.ui.View):
    """Ephemeral affiliation picker after join or from a command button."""

    def __init__(
        self,
        fe,
        *,
        user_id: int,
        game_id: str,
        on_chosen: Optional[SelectCallback] = None,
        timeout: float = 600,
    ) -> None:
        super().__init__(timeout=timeout)
        self.fe = fe
        self.user_id = user_id
        self.game_id = game_id
        self.add_item(AffiliationSelect(on_chosen=on_chosen))


def should_prompt_affiliation(fe, game, user_id: int, *, participant_status: str) -> bool:
    """True when the user should see the affiliation picker."""
    if participant_status != "active":
        return False
    if not is_affiliations_enabled(fe.be, game):
        return False
    try:
        participant = fe.be.get_many_participants(game_id=game.id, user_id=user_id)[0]
    except LookupError:
        return False
    return getattr(participant, "affiliation", None) is None


async def maybe_send_affiliation_prompt(
    interaction: discord.Interaction,
    fe,
    game,
    *,
    participant_status: str,
    ephemeral: bool = True,
) -> None:
    """Send ephemeral affiliation dropdown after a successful join."""
    if not should_prompt_affiliation(
        fe,
        game,
        interaction.user.id,
        participant_status=participant_status,
    ):
        return
    view = AffiliationSelectView(
        fe,
        user_id=interaction.user.id,
        game_id=str(game.id),
    )
    await interaction.followup.send(
        content=(
            f"Pick your fund for **{game.name}** (#{game.id}):\n\n"
            f"{AFFILIATION_WARNING}"
        ),
        view=view,
        ephemeral=ephemeral,
    )


def show_affiliation_button(fe, game, participant) -> bool:
    """True when the Choose Fund button should appear."""
    if participant is None:
        return False
    if getattr(participant, "affiliation", None) is not None:
        return False
    return is_affiliations_enabled(fe.be, game)
