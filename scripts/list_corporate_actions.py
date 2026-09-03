#!/usr/bin/env python3
"""Print Alpaca corporate actions for a calendar date (default: today ET)."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from helpers.alpaca_client import AlpacaMarketData, to_db_ticker
from helpers.corporate_actions import format_split_badge, summarize_corporate_actions_for_date


def _today_et() -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo("America/New_York")).date()


def _print_splits(label: str, items: list[dict]) -> None:
    if not items:
        return
    print(f"\n{label} ({len(items)})")
    for item in items:
        symbol = to_db_ticker(str(item.get("symbol") or "?"))
        old_rate = item.get("old_rate")
        new_rate = item.get("new_rate")
        badge = ""
        if old_rate and new_rate:
            kind = "reverse_split" if label.lower().startswith("reverse") else "forward_split"
            badge = format_split_badge(float(old_rate), float(new_rate), kind)
        print(
            f"  - {symbol}: {badge or f'{old_rate}:{new_rate}'} "
            f"(ex_date={item.get('ex_date')}, process_date={item.get('process_date')}, "
            f"id={item.get('id')})"
        )


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="Trade date YYYY-MM-DD (default: today in America/New_York)",
    )
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.date) if args.date else _today_et()

    alpaca = AlpacaMarketData()
    if not alpaca.configured:
        print("Alpaca credentials missing (ALPACA_API_KEY / ALPACA_SECRET_KEY).", file=sys.stderr)
        return 1

    ca_data = summarize_corporate_actions_for_date(alpaca, trade_date)
    if not ca_data:
        print(f"No corporate actions returned for {trade_date.isoformat()}.")
        return 0

    print(f"Corporate actions for {trade_date.isoformat()} (Alpaca process_date window):")
    _print_splits("Forward splits", ca_data.get("forward_splits") or [])
    _print_splits("Reverse splits", ca_data.get("reverse_splits") or [])

    for key, label in (
        ("name_changes", "Name changes"),
        ("cash_mergers", "Cash mergers"),
        ("stock_mergers", "Stock mergers"),
        ("stock_and_cash_mergers", "Stock+cash mergers"),
        ("worthless_removals", "Worthless removals / delistings"),
    ):
        items = ca_data.get(key) or []
        if not items:
            continue
        print(f"\n{label} ({len(items)})")
        for item in items:
            sym = item.get("symbol") or item.get("acquiree_symbol") or item.get("old_symbol")
            print(f"  - {to_db_ticker(str(sym or '?'))}: id={item.get('id')}")

    total = sum(len(v) for v in ca_data.values() if isinstance(v, list))
    if total == 0:
        print("  (empty lists in response)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
