#!/usr/bin/env python3
"""Apply NXGL and NIVF reverse splits missed on 2026-10-12 (staging found 0 picks).

Uses a broader pick search than daily CA staging (any active participant, any game
status). Skips only picks already applied (per-pick CA key) or without shares.

Idempotent:
  - Exits without changes if ``manual-repair:missed-splits-2026-10-12`` is already in
    ``applied_corporate_actions`` (use ``--force`` to bypass the script marker).
  - Skips each pick when ``{alpaca_ca_id}:pick:{pick_id}`` is already recorded.

Usage:
  python scripts/apply_missed_splits_2026_10_12.py              # dry-run
  python scripts/apply_missed_splits_2026_10_12.py --apply      # write DB
  python scripts/apply_missed_splits_2026_10_12.py --apply --refresh  # + update_all
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from helpers.alpaca_client import AlpacaMarketData, to_db_ticker
from helpers.corporate_actions import (
    format_split_badge,
    merge_event_label,
    summarize_corporate_actions_for_date,
)
from stocks import Backend, GameLogic

TRADE_DATE = date(2026, 10, 12)
SCRIPT_MARKER = "manual-repair:missed-splits-2026-10-12"
EXPECTED_CA_IDS: dict[str, str] = {
    "NXGL": "2270956c-b195-4d82-8563-57cde2a8bdb3",
    "NIVF": "d6fe3d50-97a8-4c44-9122-1e063d8514c5",
}
# DB tickers that may hold the split name (typo / legacy rows).
TICKER_LOOKUP: dict[str, tuple[str, ...]] = {
    "NXGL": ("NXGL", "NXG"),
    "NIVF": ("NIVF",),
}


@dataclass(frozen=True)
class SplitRepairPlan:
    pick_id: int
    ticker: str
    ca_id: str
    applied_key: str
    old_shares: float
    new_shares: float
    badge: str
    share_factor: float


@dataclass(frozen=True)
class PickDiagnostic:
    pick_id: int
    db_ticker: str
    status: str
    shares: float | None
    change_percent: float | None
    event_label: str | None
    game_id: str
    game_status: str
    user_id: int
    participant_status: str
    skip_reason: str | None


def _parse_ex_date(item: dict) -> date:
    raw = item.get("ex_date") or item.get("process_date")
    if raw is None:
        return TRADE_DATE
    return date.fromisoformat(str(raw)[:10])


def _reverse_splits_by_ticker(ca_data: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in ca_data.get("reverse_splits") or []:
        symbol = to_db_ticker(str(item.get("symbol") or ""))
        if symbol:
            out[symbol] = item
    return out


def _stocks_for_canonical_ticker(be: Backend, canonical: str) -> list[Any]:
    """Resolve stock row(s) for a split symbol (aliases + company-name fallback)."""
    import helpers.datatype_validation as dtv

    found: dict[int, Any] = {}
    for symbol in TICKER_LOOKUP.get(canonical, (canonical,)):
        try:
            stock = be.get_stock(symbol)
        except LookupError:
            continue
        found[int(stock.id)] = stock

    if canonical == "NXGL":
        query = """
        WHERE UPPER(ticker) LIKE 'NXG%' OR UPPER(company_name) LIKE '%NEXGEL%'
        """
        try:
            resp = be.sql.get(table="stocks", filters=(query, []))
            for row in be._many_get(typeadapter=dtv.Stocks, resp=resp):
                found[int(row.id)] = row
        except LookupError:
            pass

    return list(found.values())


def _held_picks_for_stock_repair(be: Backend, stock_id: int) -> list[Any]:
    """Owned / pending_sell picks for active participants (any game status)."""
    import helpers.datatype_validation as dtv

    query = """
    WHERE stock_id = ?
    AND status IN ("owned", "pending_sell")
    AND participation_id IN (
        SELECT participation_id FROM game_participants WHERE status = "active"
    )
    """
    try:
        resp = be.sql.get(table="stock_picks", filters=(query, [stock_id]))
        return list(be._many_get(typeadapter=dtv.StockPicks, resp=resp))
    except LookupError:
        return []


def _repair_skip_reason(
    be: Backend,
    pick: Any,
    *,
    applied_key: str,
    share_factor: float,
) -> str | None:
    if be.is_corporate_action_applied(applied_key):
        return "already_applied"
    shares = getattr(pick, "shares", None)
    if shares is None or float(shares) <= 0:
        return "no_shares"
    if share_factor >= 1:
        return "not_reverse_factor"
    return None


def _pick_context(be: Backend, pick: Any) -> tuple[str, str, str, int]:
    participant = be.get_participant(int(pick.participation_id))
    game = be.get_game(participant.game_id)
    stock = be.get_stock(int(pick.stock_id))
    return (
        str(stock.ticker),
        str(game.id),
        str(game.status),
        int(participant.user_id),
    )


def diagnose_split_picks(
    be: Backend,
    canonical: str,
    *,
    ca_id: str,
    share_factor: float,
) -> list[PickDiagnostic]:
    rows: list[PickDiagnostic] = []
    for stock in _stocks_for_canonical_ticker(be, canonical):
        for pick in _held_picks_for_stock_repair(be, stock.id):
            applied_key = f"{ca_id}:pick:{pick.id}"
            skip = _repair_skip_reason(
                be, pick, applied_key=applied_key, share_factor=share_factor,
            )
            db_ticker, game_id, game_status, user_id = _pick_context(be, pick)
            participant = be.get_participant(int(pick.participation_id))
            rows.append(
                PickDiagnostic(
                    pick_id=int(pick.id),
                    db_ticker=db_ticker,
                    status=str(pick.status),
                    shares=float(pick.shares) if pick.shares is not None else None,
                    change_percent=float(pick.change_percent)
                    if pick.change_percent is not None
                    else None,
                    event_label=getattr(pick, "event_label", None),
                    game_id=game_id,
                    game_status=game_status,
                    user_id=user_id,
                    participant_status=str(participant.status),
                    skip_reason=skip,
                )
            )
    return rows


def _print_diagnostics(rows: list[PickDiagnostic], canonical: str) -> None:
    if not rows:
        print(f"  [{canonical}] no owned/pending_sell picks for active participants.")
        return
    print(f"  [{canonical}] {len(rows)} held pick(s) in DB:")
    for row in rows:
        print(
            f"    pick_id={row.pick_id} ticker={row.db_ticker} user_id={row.user_id} "
            f"game={row.game_id}({row.game_status}) status={row.status} "
            f"shares={row.shares} chg%={row.change_percent} label={row.event_label!r} "
            f"-> {row.skip_reason or 'WILL REPAIR'}"
        )


def plan_missed_split_repairs(
    be: Backend,
    ca_data: dict,
) -> tuple[list[SplitRepairPlan], list[str]]:
    """Build repair plans; return (plans, warnings)."""
    warnings: list[str] = []
    plans: list[SplitRepairPlan] = []
    by_ticker = _reverse_splits_by_ticker(ca_data)

    for canonical, expected_ca_id in EXPECTED_CA_IDS.items():
        item = by_ticker.get(canonical)
        if item is None:
            warnings.append(f"{canonical}: no reverse split on Alpaca for {TRADE_DATE.isoformat()}.")
            continue
        ca_id = str(item.get("id") or "")
        if ca_id != expected_ca_id:
            warnings.append(
                f"{canonical}: Alpaca ca_id {ca_id!r} != logged {expected_ca_id!r}; using Alpaca id.",
            )
        old_rate = float(item.get("old_rate") or 0)
        new_rate = float(item.get("new_rate") or 0)
        if old_rate <= 0 or new_rate <= 0:
            warnings.append(f"{canonical}: invalid old_rate/new_rate in Alpaca payload.")
            continue
        share_factor = new_rate / old_rate
        badge = format_split_badge(old_rate, new_rate, "reverse_split")

        stocks = _stocks_for_canonical_ticker(be, canonical)
        if not stocks:
            warnings.append(f"{canonical}: no matching rows in stocks table.")
            continue

        seen_pick_ids: set[int] = set()
        for stock in stocks:
            for pick in _held_picks_for_stock_repair(be, stock.id):
                if pick.id in seen_pick_ids:
                    continue
                seen_pick_ids.add(int(pick.id))
                applied_key = f"{ca_id}:pick:{pick.id}"
                if _repair_skip_reason(
                    be, pick, applied_key=applied_key, share_factor=share_factor,
                ):
                    continue
                old_shares = float(pick.shares)
                plans.append(
                    SplitRepairPlan(
                        pick_id=int(pick.id),
                        ticker=canonical,
                        ca_id=ca_id,
                        applied_key=applied_key,
                        old_shares=old_shares,
                        new_shares=old_shares * share_factor,
                        badge=badge,
                        share_factor=share_factor,
                    )
                )

    return plans, warnings


def apply_plans(be: Backend, plans: list[SplitRepairPlan]) -> int:
    """Apply plans; return count of picks updated."""
    if not plans:
        return 0
    stock_ids: list[int] = []
    applied = 0
    for plan in plans:
        if be.is_corporate_action_applied(plan.applied_key):
            continue
        pick = be.get_stock_pick(plan.pick_id)
        stock = be.get_stock(int(pick.stock_id))
        stock_ids.append(int(stock.id))
        be.update_stock_pick(
            pick_id=plan.pick_id,
            shares=plan.new_shares,
            event_label=merge_event_label(getattr(pick, "event_label", None), plan.badge),
        )
        be.record_applied_corporate_action(
            plan.applied_key,
            "reverse_split",
            stock.id,
            TRADE_DATE.isoformat(),
        )
        applied += 1
    if applied and not be.is_corporate_action_applied(SCRIPT_MARKER):
        be.record_applied_corporate_action(
            SCRIPT_MARKER,
            "manual_repair",
            stock_ids[0],
            TRADE_DATE.isoformat(),
        )
    return applied


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist share adjustments (default is dry-run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if the script marker is already in applied_corporate_actions",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="After --apply, run GameLogic.update_all(force=True)",
    )
    args = parser.parse_args()

    db_name = os.getenv("DB_NAME")
    if not db_name:
        print("DB_NAME not set in environment.", file=sys.stderr)
        return 1

    be = Backend(db_name)

    if be.is_corporate_action_applied(SCRIPT_MARKER) and not args.force:
        print(
            f"Already applied (marker {SCRIPT_MARKER!r} in applied_corporate_actions). "
            "Use --force to run again; per-pick keys still prevent double splits.",
        )
        return 0

    alpaca = AlpacaMarketData()
    if not alpaca.configured:
        print("Alpaca credentials missing; cannot verify split rates.", file=sys.stderr)
        return 1

    ca_data = summarize_corporate_actions_for_date(alpaca, TRADE_DATE)
    if not ca_data:
        print(f"No corporate actions from Alpaca for {TRADE_DATE.isoformat()}.", file=sys.stderr)
        return 1

    plans, warnings = plan_missed_split_repairs(be, ca_data)
    for line in warnings:
        print(f"WARNING: {line}", file=sys.stderr)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode}: {len(plans)} pick(s) to adjust for {TRADE_DATE.isoformat()}")
    for plan in plans:
        print(
            f"  pick_id={plan.pick_id} {plan.ticker} shares "
            f"{plan.old_shares:.6f} -> {plan.new_shares:.6f} "
            f"factor={plan.share_factor:.6f} badge={plan.badge!r}"
        )

    if not plans:
        print("Nothing to do. Diagnostics:")
        by_ticker = _reverse_splits_by_ticker(ca_data)
        for canonical in EXPECTED_CA_IDS:
            item = by_ticker.get(canonical)
            if not item:
                print(f"  [{canonical}] Alpaca split not found for this date.")
                continue
            ca_id = str(item.get("id") or "")
            old_rate = float(item.get("old_rate") or 0)
            new_rate = float(item.get("new_rate") or 0)
            if old_rate <= 0 or new_rate <= 0:
                continue
            share_factor = new_rate / old_rate
            rows = diagnose_split_picks(
                be, canonical, ca_id=ca_id, share_factor=share_factor,
            )
            _print_diagnostics(rows, canonical)
        return 0

    if args.apply:
        count = apply_plans(be, plans)
        print(f"Updated {count} pick(s).")
        if args.refresh:
            GameLogic(db_name).update_all(force=True)
            print("Ran update_all(force=True) to refresh prices and portfolio values.")
    else:
        print("No changes written. Re-run with --apply to persist.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
