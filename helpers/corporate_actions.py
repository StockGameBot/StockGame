"""Corporate action staging, application, and portfolio badge labels."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Optional

from helpers.alpaca_client import (
    AlpacaMarketData,
    to_db_ticker,
)
from helpers.sqlhelper import _iso8601

if TYPE_CHECKING:
    from stocks import Backend, GameLogic

logger = logging.getLogger("CorporateActions")

SPLIT_TYPES = ("forward_split", "reverse_split")
MERGER_TYPES = ("cash_merger", "stock_merger", "stock_and_cash_merger")


@dataclass
class StageReport:
    api_calls: int = 0
    splits: int = 0
    name_changes: int = 0
    mergers: int = 0
    delistings: int = 0
    picks_staged: int = 0
    runtime_sec: float = 0.0


@dataclass
class ApplyReport:
    applied: int = 0
    skipped_already_applied: int = 0
    skipped_no_picks: int = 0
    runtime_sec: float = 0.0
    split_tickers: set[str] = field(default_factory=set)


def _gcd_int(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a)


def _reduce_ratio(n: float, d: float) -> tuple[int, int]:
    """Reduce a ratio to lowest integer terms."""
    if d == 0:
        return int(n), 1
    scale = 1
    while (
        abs(n * scale - round(n * scale)) > 1e-6
        or abs(d * scale - round(d * scale)) > 1e-6
    ) and scale < 1_000_000:
        scale *= 10
    ni, di = int(round(n * scale)), int(round(d * scale))
    if di == 0:
        return ni, 1
    g = _gcd_int(ni, di)
    return ni // g, di // g


def format_split_badge(old_rate: float, new_rate: float, split_type: str) -> str:
    """Build market-style split label from Alpaca rates."""
    num, den = _reduce_ratio(new_rate, old_rate)
    if split_type == "reverse_split":
        return f"{num}:{den} Reverse Split"
    return f"{num}:{den} Split"


def format_rename_badge(old_symbol: str) -> str:
    return f"Renamed from {to_db_ticker(old_symbol)}"


def format_cash_merger_badge(acquirer: str) -> str:
    return f"Bought by {to_db_ticker(acquirer)}"


def format_stock_merger_badge(acquiree: str, acquirer: str) -> str:
    return f"{to_db_ticker(acquiree)} & {to_db_ticker(acquirer)} Merger"


def format_delist_badge() -> str:
    return "Delisted"


def _ca_list(ca_data: dict[str, list[dict[str, Any]]], *keys: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in keys:
        items = ca_data.get(key)
        if isinstance(items, list):
            out.extend(items)
    return out


def _stock_by_ticker(be: "Backend", symbol: str):
    from stocks import Backend as _B  # noqa: F401

    try:
        return be.get_stock(to_db_ticker(symbol))
    except LookupError:
        return None


def _owned_picks_for_stock(be: "Backend", stock_id: int) -> list[Any]:
    import helpers.datatype_validation as dtv

    query = """
    WHERE status IN ("owned", "pending_sell")
    AND stock_id = ?
    AND participation_id IN (
        SELECT participation_id FROM game_participants
        WHERE status = "active"
        AND game_id IN (SELECT game_id FROM games WHERE status = "active")
    )
    """
    try:
        resp = be.sql.get(table="stock_picks", filters=(query, [stock_id]))
        return list(be._many_get(typeadapter=dtv.StockPicks, resp=resp))
    except LookupError:
        return []


def stage_corporate_actions(
    be: "Backend",
    alpaca: AlpacaMarketData,
    trade_date: Optional[date] = None,
    *,
    force_if_empty: bool = False,
) -> StageReport:
    """Poll Alpaca CA for ``trade_date`` and stage rows for owned picks."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    start = time.perf_counter()
    report = StageReport()
    if trade_date is None:
        trade_date = datetime.now(ZoneInfo("America/New_York")).date()

    existing = be.count_staged_corporate_actions(trade_date.isoformat())
    if existing and not force_if_empty:
        report.runtime_sec = time.perf_counter() - start
        return report

    if existing and force_if_empty:
        be.clear_staged_corporate_actions(trade_date.isoformat())

    if not alpaca.configured:
        logger.warning("Skipping CA staging: Alpaca not configured")
        report.runtime_sec = time.perf_counter() - start
        return report

    ca_data = alpaca.fetch_corporate_actions_for_date(trade_date)
    report.api_calls = 1

    now = _iso8601()
    date_str = trade_date.isoformat()

    for item in _ca_list(ca_data, "forward_splits"):
        symbol = item.get("symbol")
        ca_id = item.get("id")
        if not symbol or not ca_id:
            continue
        stock = _stock_by_ticker(be, str(symbol))
        if stock is None:
            continue
        old_rate = float(item.get("old_rate", 0))
        new_rate = float(item.get("new_rate", 0))
        if old_rate <= 0 or new_rate <= 0:
            continue
        split_type = "forward_split"
        share_factor = new_rate / old_rate
        payload = json.dumps(
            {
                "old_rate": old_rate,
                "new_rate": new_rate,
                "split_type": split_type,
                "badge": format_split_badge(old_rate, new_rate, split_type),
            }
        )
        picks = _owned_picks_for_stock(be, stock.id)
        for pick in picks:
            be.insert_staged_corporate_action(
                alpaca_ca_id=str(ca_id),
                action_type=split_type,
                stock_id=stock.id,
                pick_id=pick.id,
                share_factor=share_factor,
                payload=payload,
                trade_date=date_str,
                datetime_staged=now,
            )
            report.picks_staged += 1
        report.splits += 1

    for item in _ca_list(ca_data, "reverse_splits"):
        symbol = item.get("symbol")
        ca_id = item.get("id")
        if not symbol or not ca_id:
            continue
        stock = _stock_by_ticker(be, str(symbol))
        if stock is None:
            continue
        old_rate = float(item.get("old_rate", 0))
        new_rate = float(item.get("new_rate", 0))
        if old_rate <= 0 or new_rate <= 0:
            continue
        split_type = "reverse_split"
        share_factor = new_rate / old_rate
        payload = json.dumps(
            {
                "old_rate": old_rate,
                "new_rate": new_rate,
                "split_type": split_type,
                "badge": format_split_badge(old_rate, new_rate, split_type),
            }
        )
        picks = _owned_picks_for_stock(be, stock.id)
        for pick in picks:
            be.insert_staged_corporate_action(
                alpaca_ca_id=str(ca_id),
                action_type=split_type,
                stock_id=stock.id,
                pick_id=pick.id,
                share_factor=share_factor,
                payload=payload,
                trade_date=date_str,
                datetime_staged=now,
            )
            report.picks_staged += 1
        report.splits += 1

    for item in ca_data.get("name_changes") or []:
        old_sym = item.get("old_symbol")
        new_sym = item.get("new_symbol")
        ca_id = item.get("id")
        if not old_sym or not new_sym or not ca_id:
            continue
        stock = _stock_by_ticker(be, str(old_sym))
        if stock is None:
            continue
        payload = json.dumps(
            {
                "old_symbol": to_db_ticker(str(old_sym)),
                "new_symbol": to_db_ticker(str(new_sym)),
                "badge": format_rename_badge(str(old_sym)),
            }
        )
        be.insert_staged_corporate_action(
            alpaca_ca_id=str(ca_id),
            action_type="name_change",
            stock_id=stock.id,
            pick_id=None,
            share_factor=None,
            payload=payload,
            trade_date=date_str,
            datetime_staged=now,
        )
        for pick in _owned_picks_for_stock(be, stock.id):
            be.insert_staged_corporate_action(
                alpaca_ca_id=f"{ca_id}:pick:{pick.id}",
                action_type="name_change_pick",
                stock_id=stock.id,
                pick_id=pick.id,
                share_factor=None,
                payload=payload,
                trade_date=date_str,
                datetime_staged=now,
            )
            report.picks_staged += 1
        report.name_changes += 1

    for item in ca_data.get("worthless_removals") or []:
        symbol = item.get("symbol")
        ca_id = item.get("id")
        if not symbol or not ca_id:
            continue
        stock = _stock_by_ticker(be, str(symbol))
        if stock is None:
            continue
        payload = json.dumps({"badge": format_delist_badge()})
        picks = _owned_picks_for_stock(be, stock.id)
        for pick in picks:
            be.insert_staged_corporate_action(
                alpaca_ca_id=str(ca_id),
                action_type="worthless_removal",
                stock_id=stock.id,
                pick_id=pick.id,
                share_factor=None,
                payload=payload,
                trade_date=date_str,
                datetime_staged=now,
            )
            report.picks_staged += 1
        report.delistings += 1

    for key, action_type in (
        ("cash_mergers", "cash_merger"),
        ("stock_mergers", "stock_merger"),
        ("stock_and_cash_mergers", "stock_and_cash_merger"),
    ):
        for item in ca_data.get(key) or []:
            acquiree = item.get("acquiree_symbol")
            ca_id = item.get("id")
            if not acquiree or not ca_id:
                continue
            stock = _stock_by_ticker(be, str(acquiree))
            if stock is None:
                continue
            acquirer = item.get("acquirer_symbol", "")
            if action_type == "cash_merger":
                badge = format_cash_merger_badge(str(acquirer))
            else:
                badge = format_stock_merger_badge(str(acquiree), str(acquirer))
            payload = json.dumps({**item, "badge": badge, "action_type": action_type})
            picks = _owned_picks_for_stock(be, stock.id)
            for pick in picks:
                be.insert_staged_corporate_action(
                    alpaca_ca_id=str(ca_id),
                    action_type=action_type,
                    stock_id=stock.id,
                    pick_id=pick.id,
                    share_factor=None,
                    payload=payload,
                    trade_date=date_str,
                    datetime_staged=now,
                )
                report.picks_staged += 1
            report.mergers += 1

    report.runtime_sec = time.perf_counter() - start
    logger.info(
        "CA staging complete: splits=%s name_changes=%s mergers=%s delistings=%s "
        "picks_staged=%s runtime=%.2fs",
        report.splits,
        report.name_changes,
        report.mergers,
        report.delistings,
        report.picks_staged,
        report.runtime_sec,
    )
    return report


def split_tickers_for_date(be: "Backend", trade_date: date) -> set[str]:
    """DB tickers with staged/applied splits for ``trade_date``."""
    tickers: set[str] = set()
    for row in be.get_staged_corporate_actions(trade_date.isoformat()):
        if row.get("action_type") in SPLIT_TYPES:
            try:
                stock = be.get_stock(int(row["stock_id"]))
                tickers.add(stock.ticker.upper())
            except (LookupError, TypeError, ValueError):
                pass
    return tickers


def apply_staged_corporate_actions(
    be: "Backend",
    gl: "GameLogic",
    trade_date: date,
    prices: dict[str, float],
    *,
    phase: str = "all",
) -> ApplyReport:
    """Apply staged corporate actions (``pre_price`` | ``post_price`` | ``all``)."""
    start = time.perf_counter()
    report = ApplyReport()
    staged = be.get_staged_corporate_actions(trade_date.isoformat())
    if not staged:
        report.runtime_sec = time.perf_counter() - start
        return report

    applied_ca_ids: set[str] = set()

    if phase in ("all", "pre_price"):
        for row in staged:
            if row.get("action_type") != "name_change":
                continue
            ca_id = str(row["alpaca_ca_id"])
            if be.is_corporate_action_applied(ca_id):
                report.skipped_already_applied += 1
                continue
            payload = json.loads(row.get("payload") or "{}")
            new_sym = payload.get("new_symbol")
            if not new_sym:
                continue
            stock = be.get_stock(int(row["stock_id"]))
            be.rename_stock_ticker(stock.id, new_sym)
            be.record_applied_corporate_action(
                ca_id, "name_change", stock.id, trade_date.isoformat()
            )
            applied_ca_ids.add(ca_id)
            report.applied += 1
            logger.warning(
                "Applied name_change %s -> %s (ca_id=%s)",
                payload.get("old_symbol"),
                new_sym,
                ca_id,
            )

        for row in staged:
            action_type = row.get("action_type")
            if action_type not in SPLIT_TYPES:
                continue
            ca_id = str(row["alpaca_ca_id"])
            pick_id = row.get("pick_id")
            if pick_id is None:
                continue
            applied_key = f"{ca_id}:pick:{pick_id}"
            if be.is_corporate_action_applied(applied_key):
                report.skipped_already_applied += 1
                continue
            share_factor = row.get("share_factor")
            if share_factor is None:
                continue
            pick = be.get_stock_pick(int(pick_id))
            if pick.shares is None:
                continue
            payload = json.loads(row.get("payload") or "{}")
            badge = payload.get("badge", "")
            new_shares = pick.shares * float(share_factor)
            be.update_stock_pick(
                pick_id=pick.id,
                shares=new_shares,
                event_label=badge,
            )
            stock = be.get_stock(int(row["stock_id"]))
            report.split_tickers.add(stock.ticker.upper())
            be.record_applied_corporate_action(
                applied_key, action_type, stock.id, trade_date.isoformat()
            )
            report.applied += 1
            logger.warning(
                "Applied %s %s (ca_id=%s, pick_id=%s, factor=%s)",
                action_type,
                stock.ticker,
                ca_id,
                pick.id,
                share_factor,
            )

    if phase in ("all", "post_price"):
        for row in staged:
            action_type = row.get("action_type")
            if action_type not in MERGER_TYPES + ("worthless_removal",):
                continue
            ca_id = str(row["alpaca_ca_id"])
            pick_id = row.get("pick_id")
            if pick_id is None:
                continue
            applied_key = f"{ca_id}:pick:{pick_id}"
            if be.is_corporate_action_applied(applied_key):
                report.skipped_already_applied += 1
                continue
            pick = be.get_stock_pick(int(pick_id))
            payload = json.loads(row.get("payload") or "{}")
            badge = payload.get("badge", format_delist_badge())
            stock = be.get_stock(int(row["stock_id"]))

            if action_type == "worthless_removal":
                be.set_stock_trade_status(stock.id, "delisted")
                be.update_stock_pick(pick_id=pick.id, event_label=badge)
            elif action_type == "cash_merger":
                rate = float(payload.get("rate", 0))
                if pick.shares is not None and rate > 0:
                    final_value = round(pick.shares * rate, 2)
                    start_val = pick.start_value or 0
                    be.update_stock_pick(
                        pick_id=pick.id,
                        current_value=final_value,
                        change_dollars=final_value - start_val,
                        change_percent=(
                            ((final_value - start_val) / start_val * 100) if start_val else 0.0
                        ),
                        event_label=badge,
                    )
                be.set_stock_trade_status(stock.id, "merged")
            elif action_type in ("stock_merger", "stock_and_cash_merger"):
                acquirer_sym = payload.get("acquirer_symbol")
                acquiree_rate = float(payload.get("acquiree_rate", 1) or 1)
                acquirer_rate = float(payload.get("acquirer_rate", 0) or 0)
                cash_rate = float(payload.get("cash_rate", 0) or 0)
                if pick.shares is not None:
                    if action_type == "stock_and_cash_merger" and cash_rate:
                        final_value = round(pick.shares * cash_rate, 2)
                        be.update_stock_pick(
                            pick_id=pick.id,
                            current_value=final_value,
                            event_label=badge,
                        )
                    elif acquirer_sym and acquirer_rate and acquiree_rate:
                        new_shares = pick.shares * (acquirer_rate / acquiree_rate)
                        acquirer = _ensure_stock(gl, str(acquirer_sym))
                        acquirer_price = prices.get(acquirer.ticker)
                        current_value = None
                        if acquirer_price is not None:
                            current_value = round(new_shares * acquirer_price, 2)
                        be.update_stock_pick(
                            pick_id=pick.id,
                            shares=new_shares,
                            stock_id=acquirer.id,
                            current_value=current_value,
                            event_label=badge,
                        )
                be.set_stock_trade_status(stock.id, "merged")

            if ca_id not in applied_ca_ids:
                be.record_applied_corporate_action(
                    applied_key, action_type, stock.id, trade_date.isoformat()
                )
                applied_ca_ids.add(ca_id)
                report.applied += 1
            logger.warning(
                "Applied %s for %s pick_id=%s (ca_id=%s)",
                action_type,
                stock.ticker,
                pick.id,
                ca_id,
            )

        for row in staged:
            if row.get("action_type") != "name_change_pick":
                continue
            pick_id = row.get("pick_id")
            if pick_id is None:
                continue
            payload = json.loads(row.get("payload") or "{}")
            badge = payload.get("badge")
            if badge:
                be.update_stock_pick(pick_id=int(pick_id), event_label=badge)

        be.clear_staged_corporate_actions(trade_date.isoformat())

    report.runtime_sec = time.perf_counter() - start
    logger.info(
        "CA apply complete: applied=%s skipped_already=%s split_tickers=%s runtime=%.2fs",
        report.applied,
        report.skipped_already_applied,
        len(report.split_tickers),
        report.runtime_sec,
    )
    return report


def _ensure_stock(gl: "GameLogic", ticker: str):
    resolved = gl.find_stock(ticker)
    return gl.be.get_stock(resolved)


def repair_imcc_2026_08_27(be: "Backend", alpaca: AlpacaMarketData) -> int:
    """One-time idempotent IMCC 1:30 reverse split repair."""
    from datetime import date as date_cls

    trade_date = date_cls(2026, 8, 27)
    ca_id = "d05a02a4-a1af-4c06-997f-2d124966852c"
    if be.is_corporate_action_applied(ca_id):
        return 0
    try:
        stock = be.get_stock("IMCC")
    except LookupError:
        return 0
    factor = 1.0 / 30.0
    badge = format_split_badge(30, 1, "reverse_split")
    fixed = 0
    for pick in _owned_picks_for_stock(be, stock.id):
        if pick.shares is None or pick.shares < 100:
            continue
        be.update_stock_pick(
            pick_id=pick.id,
            shares=pick.shares * factor,
            event_label=badge,
        )
        fixed += 1
    if fixed:
        be.record_applied_corporate_action(
            ca_id, "reverse_split", stock.id, trade_date.isoformat()
        )
        logger.warning("IMCC repair: adjusted %s pick(s)", fixed)
    return fixed
