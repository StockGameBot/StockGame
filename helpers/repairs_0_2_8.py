"""One-time 0.2.8 startup repairs (untradeable picks + stress-test end date)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from helpers.alpaca_client import AlpacaMarketData
    from stocks import Backend

logger = logging.getLogger("Repairs028")

VERSION = "0.2.8"
REPAIR_DATE = "2026-08-27"

UNTRADEABLE_REPAIR_KEY = f"untradeable-pick-repair-{REPAIR_DATE}"
STRESS_TEST_GAME_ID = "LWFN6"
STRESS_TEST_END_DATE = "2026-08-31"
STRESS_TEST_END_REPAIR_KEY = f"stress-test-end-date-{STRESS_TEST_END_DATE}"


@dataclass
class UntradeableRepairReport:
    stocks_marked: int = 0
    picks_removed: int = 0
    tickers: list[str] = field(default_factory=list)
    runtime_sec: float = 0.0


@dataclass
class Repairs028Report:
    untradeable: UntradeableRepairReport = field(default_factory=UntradeableRepairReport)
    stress_test_end_date_set: bool = False
    stress_test_game_id: Optional[str] = None
    runtime_sec: float = 0.0


def _marker_stock_id(be: "Backend") -> Optional[int]:
    try:
        return be.get_many_stocks()[0].id
    except LookupError:
        return None


def _record_repair(
    be: "Backend",
    key: str,
    action_type: str,
    *,
    force: bool = False,
) -> None:
    if not force and be.is_corporate_action_applied(key):
        return
    stock_id = _marker_stock_id(be)
    if stock_id is None:
        logger.warning("Repair %s applied but not recorded (no stocks in DB)", key)
        return
    be.record_applied_corporate_action(key, action_type, stock_id, REPAIR_DATE)


def _find_stress_test_game(be: "Backend"):
    try:
        return be.get_game(STRESS_TEST_GAME_ID)
    except LookupError:
        pass
    try:
        games = be.get_many_games(
            include_public=True,
            include_private=True,
            include_open=True,
            include_active=True,
            include_ended=True,
        )
    except LookupError:
        return None
    for game in games:
        if "stress test" in str(game.name).lower():
            return game
    return None


def repair_untradeable_equity_picks(
    be: "Backend",
    alpaca: "AlpacaMarketData",
    *,
    game_id: Optional[str | int] = None,
    force: bool = False,
) -> UntradeableRepairReport:
    """Mark untradeable DB stocks delisted and drop their active-game picks."""
    start = time.perf_counter()
    report = UntradeableRepairReport()

    if not force and be.is_corporate_action_applied(UNTRADEABLE_REPAIR_KEY):
        report.runtime_sec = time.perf_counter() - start
        return report

    if not alpaca.configured:
        logger.warning("Skipping untradeable equity repair: Alpaca not configured")
        report.runtime_sec = time.perf_counter() - start
        return report

    buyable = alpaca.fetch_buyable_db_tickers()
    try:
        stocks = be.get_many_stocks()
    except LookupError:
        stocks = ()

    for stock in stocks:
        ticker = str(stock.ticker).upper()
        trade_status = getattr(stock, "trade_status", "active")
        is_buyable = ticker in buyable

        if is_buyable and trade_status == "active":
            continue

        if not is_buyable and trade_status != "delisted":
            be.set_stock_trade_status(stock.id, "delisted")
            report.stocks_marked += 1
            report.tickers.append(ticker)

        picks = be.get_active_picks_for_stock(stock.id, game_id=game_id)
        for pick in picks:
            be.remove_stock_pick(pick.id)
            report.picks_removed += 1
            logger.warning(
                "Removed pick %s (%s) from participant %s — ticker not buyable on Alpaca",
                pick.id,
                ticker,
                pick.participation_id,
            )

    if report.picks_removed or report.stocks_marked:
        _record_repair(be, UNTRADEABLE_REPAIR_KEY, "untradeable_repair", force=force)
        logger.warning(
            "Untradeable equity repair: marked=%s picks_removed=%s tickers=%s",
            report.stocks_marked,
            report.picks_removed,
            ", ".join(report.tickers[:20])
            + ("..." if len(report.tickers) > 20 else ""),
        )

    report.runtime_sec = time.perf_counter() - start
    return report


def repair_stress_test_end_date(
    be: "Backend",
    *,
    end_date: str = STRESS_TEST_END_DATE,
    force: bool = False,
) -> bool:
    """Set Official Stress Test game end date to ``end_date`` (default 2026-08-31)."""
    if not force and be.is_corporate_action_applied(STRESS_TEST_END_REPAIR_KEY):
        return False

    game = _find_stress_test_game(be)
    if game is None:
        logger.info("Stress test end-date repair: game not found, skipping")
        return False

    current = game.end_date
    if current is not None and str(current) == end_date:
        _record_repair(be, STRESS_TEST_END_REPAIR_KEY, "stress_test_end_date", force=force)
        return False

    be.update_game(game_id=game.id, end_date=end_date)
    _record_repair(be, STRESS_TEST_END_REPAIR_KEY, "stress_test_end_date", force=force)
    logger.warning(
        "Stress test end-date repair: game %s (%s) end_date set to %s (was %s)",
        game.id,
        game.name,
        end_date,
        current,
    )
    return True


def run_repairs_0_2_8(
    be: "Backend",
    alpaca: "AlpacaMarketData",
    *,
    force: bool = False,
) -> Repairs028Report:
    """Run all one-time 0.2.8 repairs."""
    start = time.perf_counter()
    stress_game = _find_stress_test_game(be)
    report = Repairs028Report(
        untradeable=repair_untradeable_equity_picks(be, alpaca, force=force),
        stress_test_end_date_set=repair_stress_test_end_date(be, force=force),
        stress_test_game_id=str(stress_game.id) if stress_game else None,
    )
    report.runtime_sec = time.perf_counter() - start
    logger.info(
        "0.2.8 repairs complete in %.2fs: picks_removed=%s stress_test_end=%s",
        report.runtime_sec,
        report.untradeable.picks_removed,
        report.stress_test_end_date_set,
    )
    return report
