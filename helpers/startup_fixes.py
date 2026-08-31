"""One-time idempotent startup fixes."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stocks import Backend

logger = logging.getLogger("StartupFixes")

LWFN6_GAME_ID = "LWFN6"
LWFN6_END_DATE = "2026-08-29"
LWFN6_FIX_KEY = f"startup-lwfn6-end-{LWFN6_END_DATE}"


def _record_startup_fix(be: "Backend", key: str) -> None:
    stock_id = 1
    try:
        stocks = be.get_many_stocks()
        if stocks:
            stock_id = stocks[0].id
    except LookupError:
        pass
    if be.is_corporate_action_applied(key):
        return
    be.record_applied_corporate_action(
        key,
        "startup_fix",
        stock_id,
        date.today().isoformat(),
    )


def fix_lwfn6_end_date(be: "Backend") -> bool:
    """Set stress-test game LWFN6 end date for role assignment."""
    if be.is_corporate_action_applied(LWFN6_FIX_KEY):
        return False
    try:
        game = be.get_game(LWFN6_GAME_ID)
    except LookupError:
        logger.info("LWFN6 end-date fix: game not found, skipping")
        _record_startup_fix(be, LWFN6_FIX_KEY)
        return False

    target = date.fromisoformat(LWFN6_END_DATE)
    current = game.end_date
    if current == target:
        _record_startup_fix(be, LWFN6_FIX_KEY)
        return False

    be.update_game(game_id=LWFN6_GAME_ID, end_date=LWFN6_END_DATE)
    _record_startup_fix(be, LWFN6_FIX_KEY)
    logger.info(
        "LWFN6 end-date fix: game %s (%s) end_date set to %s (was %s)",
        LWFN6_GAME_ID,
        game.name,
        LWFN6_END_DATE,
        current,
    )
    return True


def run_startup_fixes(be: "Backend") -> None:
    """Run all idempotent startup fixes."""
    fix_lwfn6_end_date(be)
