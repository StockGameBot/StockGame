# WRITTEN MOSTLY BY CLAUDE

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

import discord
from discord.app_commands import Choice
from discord.interactions import Interaction
from helpers.alpaca_client import to_db_ticker
from helpers.datatype_validation import Game, StockPick
from helpers.equity_meta import autocomplete_label
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stocks import Frontend

_fe: 'Frontend | None' = None
logger = logging.getLogger(__name__)

# Matches buy_stock's effective ticker length (DB form uses '-' for class shares).
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(?:[.\-][A-Z]{1,2})?$")


def init_autocomplete(fe_instance: 'Frontend') -> None:
    """Inject the Frontend instance shared with the main bot module."""
    global _fe
    _fe = fe_instance


def _normalize_typed_ticker(current: str) -> str | None:
    """Return a DB-form ticker if ``current`` looks like a symbol the user can buy."""
    raw = current.strip().upper().replace(" ", "")
    if not raw or not _TICKER_RE.match(raw):
        return None
    ticker = to_db_ticker(raw)
    if len(ticker) > 5:
        return None
    return ticker


def format_game_autocomplete_label(
    game: Game,
    *,
    is_owner: bool = False,
    status_emoji: str | None = None,
) -> str:
    """Uniform game label: status emoji, optional 🔁/🔒 prefixes, name, id."""
    emoji_prefix = f"{status_emoji} " if status_emoji else ""
    prefix = emoji_prefix + (f"{'🔁 ' if getattr(game, 'template_id', None) is not None else ''}")
    prefix += f"{'🔒 ' if getattr(game, 'private_game', False) else ''}"
    suffix = " [OWNER]" if is_owner else ""
    return f"{prefix}{game.name} (ID: {game.id}){suffix}"[:100]


def _matches_game_needle(game: Game, needle: str) -> bool:
    if not needle:
        return True
    haystack = f"{game.name} {game.id}".lower()
    return needle in haystack


def _choices_from_games(
    games: list[tuple[Game, bool]],
    needle: str,
) -> list[Choice[str]]:
    """Build up to 25 choices from ``(game, is_owner)`` pairs."""
    today = date.today()
    if _fe is not None and hasattr(_fe, "gl"):
        today = _fe.gl._today_et()
    choices: list[Choice[str]] = []
    for game, is_owner in games:
        if not _matches_game_needle(game, needle):
            continue
        status_emoji = (
            _fe.game_status_emoji(game, today)
            if _fe is not None and hasattr(_fe, "game_status_emoji")
            else None
        )
        choices.append(
            Choice(
                name=format_game_autocomplete_label(
                    game,
                    is_owner=is_owner,
                    status_emoji=status_emoji,
                ),
                value=str(game.id),
            )
        )
        if len(choices) >= 25:
            break
    return choices


@dataclass(frozen=True)
class GameAutocompleteSpec:
    """How to collect games for a slash-command autocomplete handler."""

    collector: Callable[[Interaction], list[tuple[Game, bool]]]


def _collect_participant_games(
    interaction: Interaction,
    *,
    owner_only: bool = False,
    private_owner_only: bool = False,
    include_ended: bool = False,
) -> list[tuple[Game, bool]]:
    if _fe is None:
        return []
    try:
        ranked = _fe.list_my_games_ranked(
            interaction.user.id,
            include_ended=include_ended,
        )
    except LookupError:
        return []

    rows: list[tuple[Game, bool]] = []
    for game, _count in ranked:
        is_owner = game.owner_id == interaction.user.id
        if owner_only and not is_owner:
            continue
        if private_owner_only and (not is_owner or not game.private_game):
            continue
        if private_owner_only and game.status == 'ended':
            continue
        rows.append((game, is_owner))
    return rows


def _collect_joinable_games(interaction: Interaction) -> list[tuple[Game, bool]]:
    if _fe is None:
        return []
    try:
        ranked = _fe.list_games_ranked(include_open=True, include_active=True)
    except LookupError:
        ranked = []
    try:
        joined_ids = {
            str(participant.game_id)
            for participant in _fe.be.get_many_participants(user_id=interaction.user.id)
        }
    except LookupError:
        joined_ids = set()

    rows: list[tuple[Game, bool]] = []
    for game, _count in ranked:
        if str(game.id) in joined_ids:
            continue
        rows.append((game, getattr(game, "owner_id", None) == interaction.user.id))

    existing_ids = {str(game.id) for game, _ in rows}
    try:
        pending_invites = _fe.list_pending_game_invites(interaction.user.id)
    except Exception:
        pending_invites = ()
    for invite in pending_invites:
        game_id = str(invite.game_id)
        if game_id in joined_ids or game_id in existing_ids:
            continue
        try:
            game = _fe.be.get_game(game_id=game_id)
        except LookupError:
            continue
        rows.append((game, False))
        existing_ids.add(game_id)
    return rows


def _collect_leaderboard_games(interaction: Interaction) -> list[tuple[Game, bool]]:
    if _fe is None:
        return []

    try:
        mine = _fe.list_my_games_ranked(interaction.user.id, include_ended=True)
    except LookupError:
        mine = []
    try:
        public = _fe.list_games_ranked(
            include_public=True,
            include_private=False,
            include_open=True,
            include_active=True,
            include_ended=True,
        )
    except LookupError:
        public = []

    mine_ids = {str(game.id) for game, _count in mine}
    ordered: list[tuple[Game, bool]] = []
    seen: set[str] = set()

    def _append(group: list, *, is_owner: bool) -> None:
        for game, _count in group:
            game_id = str(game.id)
            if game_id in seen:
                continue
            seen.add(game_id)
            ordered.append((game, is_owner))

    my_recurring = [item for item in mine if item[0].template_id is not None]
    my_other = [item for item in mine if item[0].template_id is None]
    public_not_mine = [item for item in public if str(item[0].id) not in mine_ids]
    other_recurring = [item for item in public_not_mine if item[0].template_id is not None]
    other_public = [item for item in public_not_mine if item[0].template_id is None]

    _append(my_recurring, is_owner=True)
    _append(my_other, is_owner=True)
    _append(other_recurring, is_owner=False)
    _append(other_public, is_owner=False)
    return ordered


def _collect_game_info_games(interaction: Interaction) -> list[tuple[Game, bool]]:
    if _fe is None:
        return []

    try:
        mine = _fe.list_my_games_ranked(interaction.user.id, include_ended=True)
    except LookupError:
        mine = []
    try:
        public = _fe.list_games_ranked(
            include_public=True,
            include_private=False,
            include_open=True,
            include_active=True,
            include_ended=True,
        )
    except LookupError:
        public = []

    accessible: list[tuple[Game, bool]] = []
    seen: set[str] = set()
    for game, _count in mine:
        if game.private_game:
            try:
                participants = _fe.be.get_many_participants(
                    game_id=game.id, user_id=interaction.user.id
                )
            except LookupError:
                continue
            if game.owner_id != interaction.user.id and not any(
                p.status == 'active' for p in participants
            ):
                continue
        gid = str(game.id)
        if gid not in seen:
            seen.add(gid)
            accessible.append((game, game.owner_id == interaction.user.id))
    for game, _count in public:
        gid = str(game.id)
        if gid not in seen:
            seen.add(gid)
            accessible.append((game, game.owner_id == interaction.user.id))
    return accessible


_GAME_SPECS: dict[str, GameAutocompleteSpec] = {
    "participant": GameAutocompleteSpec(
        collector=lambda i: _collect_participant_games(i, include_ended=False),
    ),
    "owner": GameAutocompleteSpec(
        collector=lambda i: _collect_participant_games(i, owner_only=True, include_ended=False),
    ),
    "private_owner": GameAutocompleteSpec(
        collector=lambda i: _collect_participant_games(
            i, private_owner_only=True, include_ended=False
        ),
    ),
    "join": GameAutocompleteSpec(collector=_collect_joinable_games),
    "leaderboard": GameAutocompleteSpec(collector=_collect_leaderboard_games),
    "game_info": GameAutocompleteSpec(collector=_collect_game_info_games),
}


async def game_id_autocomplete(
    interaction: Interaction,
    current: str,
    spec_key: str = "participant",
) -> list[Choice[str]]:
    """Shared game-id autocomplete; ``spec_key`` selects the collector in ``_GAME_SPECS``."""
    try:
        spec = _GAME_SPECS.get(spec_key)
        if spec is None or _fe is None:
            return []
        needle = current.strip().lower()
        games = spec.collector(interaction)
        return _choices_from_games(games, needle)
    except Exception:
        logger.debug('Game autocomplete failed (%s).', spec_key, exc_info=True)
        return []


async def all_games_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    return await game_id_autocomplete(interaction, current, spec_key="participant")


async def owner_games_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    return await game_id_autocomplete(interaction, current, spec_key="owner")


async def private_owner_games_autocomplete(
    interaction: Interaction,
    current: str,
) -> list[Choice[str]]:
    return await game_id_autocomplete(interaction, current, spec_key="private_owner")


async def join_games_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    return await game_id_autocomplete(interaction, current, spec_key="join")


async def leaderboard_games_autocomplete(
    interaction: Interaction,
    current: str,
) -> list[Choice[str]]:
    return await game_id_autocomplete(interaction, current, spec_key="leaderboard")


async def game_info_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    return await game_id_autocomplete(interaction, current, spec_key="game_info")


async def sell_ticker_autocomplete(
    interaction: Interaction,
    current: str,
) -> list[Choice[str]]:
    """Autocomplete function to show user's stocks for the selected game"""
    try:
        game_id: str | None = None
        if interaction.data and 'options' in interaction.data:
            for option in interaction.data.get('options', []):
                if option['name'] == 'game_id':
                    value = option.get('value')
                    game_id = value if isinstance(value, str) else None
                    break

        if not isinstance(game_id, str) or not game_id:
            return []

        if _fe is None:
            return []

        user_stocks: tuple[StockPick] = _fe.my_stocks(
            user_id=interaction.user.id,
            game_id=game_id,
            show_pending=True,
            show_sold=False
        )

        choices = []
        seen_tickers = set()

        for stock in user_stocks:
            ticker: str | None = stock.stock_ticker

            if not isinstance(ticker, str):
                continue

            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)

            status_indicator = ""
            if hasattr(stock, 'status'):
                if stock.status == 'pending_buy':
                    status_indicator = " [PENDING BUY]"

            display_name: str = ticker + status_indicator

            search_text = ticker.lower()
            if current.lower() in search_text:
                choices.append(Choice(
                    name=display_name[:100],
                    value=ticker
                ))

        return choices[:25]

    except (LookupError, AttributeError):
        return []
    except Exception:
        logger.debug('Stock-pick autocomplete failed.', exc_info=True)
        return []


async def buy_ticker_autocomplete(
    interaction: Interaction,
    current: str,
) -> list[Choice[str]]:
    """Suggest local tickers, and always offer the typed symbol if valid."""
    try:
        if _fe is None:
            return []

        choices: list[Choice[str]] = []
        seen: set[str] = set()

        typed = _normalize_typed_ticker(current)
        if typed:
            typed_label = typed
            try:
                existing = _fe.be.get_stock(typed)
                typed_label = autocomplete_label(str(existing.ticker), existing.company)
                if typed_label == typed and getattr(_fe, "gl", None) is not None:
                    await asyncio.to_thread(_fe.gl._ensure_company_name, existing)
                    existing = _fe.be.get_stock(typed)
                    typed_label = autocomplete_label(str(existing.ticker), existing.company)
            except LookupError:
                pass
            except Exception:
                logger.debug('Typed-ticker name refresh failed for %s', typed, exc_info=True)
            choices.append(Choice(name=typed_label[:100], value=typed))
            seen.add(typed)

        needle = current.strip().lower()
        try:
            stocks = _fe.be.get_many_stocks()
        except LookupError:
            stocks = ()

        for stock in stocks:
            ticker = str(stock.ticker)
            company_name = str(getattr(stock, "company", "") or "")
            label = autocomplete_label(ticker, company_name)
            searchable = f"{ticker} {company_name}".lower()
            if needle and needle not in searchable:
                continue
            if ticker in seen:
                choices = [
                    Choice(name=label[:100], value=ticker) if c.value == ticker else c
                    for c in choices
                ]
                continue
            seen.add(ticker)
            choices.append(Choice(name=label[:100], value=ticker))
            if len(choices) >= 25:
                break

        return choices[:25]
    except Exception:
        logger.debug('Buy-ticker autocomplete failed.', exc_info=True)
        typed = _normalize_typed_ticker(current)
        if typed:
            return [Choice(name=typed, value=typed)]
        return []
