#!/usr/bin/env python3
"""Apply NXGL and NIVF reverse splits missed on 2026-10-12 (staging found 0 picks).

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from helpers.alpaca_client import AlpacaMarketData, to_db_ticker
from helpers.corporate_actions import (
    _owned_picks_for_stock,
    _pick_should_receive_split,
    format_split_badge,
    summarize_corporate_actions_for_date,
)
from stocks import Backend, GameLogic

TRADE_DATE = date(2026, 10, 12)
SCRIPT_MARKER = "manual-repair:missed-splits-2026-10-12"
EXPECTED_CA_IDS: dict[str, str] = {
    "NXGL": "2270956c-b195-4d82-8563-57cde2a8bdb3",
    "NIVF": "d6fe3d50-97a8-4c44-9122-1e063d8514c5",
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


def plan_missed_split_repairs(
    be: Backend,
    ca_data: dict,
) -> tuple[list[SplitRepairPlan], list[str]]:
    """Build repair plans; return (plans, warnings)."""
    warnings: list[str] = []
    plans: list[SplitRepairPlan] = []
    by_ticker = _reverse_splits_by_ticker(ca_data)

    for ticker, expected_ca_id in EXPECTED_CA_IDS.items():
        item = by_ticker.get(ticker)
        if item is None:
            warnings.append(f"{ticker}: no reverse split on Alpaca for {TRADE_DATE.isoformat()}.")
            continue
        ca_id = str(item.get("id") or "")
        if ca_id != expected_ca_id:
            warnings.append(
                f"{ticker}: Alpaca ca_id {ca_id!r} != expected {expected_ca_id!r}; skipping.",
            )
            continue
        old_rate = float(item.get("old_rate") or 0)
        new_rate = float(item.get("new_rate") or 0)
        if old_rate <= 0 or new_rate <= 0:
            warnings.append(f"{ticker}: invalid old_rate/new_rate in Alpaca payload.")
            continue
        share_factor = new_rate / old_rate
        badge = format_split_badge(old_rate, new_rate, "reverse_split")
        ex_date = _parse_ex_date(item)

        try:
            stock = be.get_stock(ticker)
        except LookupError:
            warnings.append(f"{ticker}: not in stocks table.")
            continue

        picks = _owned_picks_for_stock(be, stock.id, trade_date=TRADE_DATE)
        for pick in picks:
            applied_key = f"{ca_id}:pick:{pick.id}"
            if be.is_corporate_action_applied(applied_key):
                continue
            if not _pick_should_receive_split(
                pick, TRADE_DATE, share_factor, ex_date=ex_date,
            ):
                continue
            if pick.shares is None:
                warnings.append(f"pick_id={pick.id} ({ticker}): no shares; skipped.")
                continue
            old_shares = float(pick.shares)
            plans.append(
                SplitRepairPlan(
                    pick_id=int(pick.id),
                    ticker=ticker,
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
        stock = be.get_stock(plan.ticker)
        stock_ids.append(stock.id)
        be.update_stock_pick(
            pick_id=plan.pick_id,
            shares=plan.new_shares,
            event_label=plan.badge,
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
        print("Nothing to do.")
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
