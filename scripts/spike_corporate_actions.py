#!/usr/bin/env python3
"""Manual Alpaca corporate-actions probe (date-only, single symbol, batch)."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date

from dotenv import load_dotenv

from helpers.alpaca_client import AlpacaMarketData, CRITICAL_CA_TYPES


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Probe Alpaca corporate actions API")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--symbol", default=None, help="Optional single symbol")
    args = parser.parse_args()

    alpaca = AlpacaMarketData()
    if not alpaca.configured:
        print("Alpaca credentials not configured")
        return

    trade_date = date.fromisoformat(args.date)
    if args.symbol:
        page = alpaca.fetch_corporate_actions_page(
            start=trade_date.isoformat(),
            end=trade_date.isoformat(),
            types=CRITICAL_CA_TYPES,
        )
        print(json.dumps(page, indent=2)[:4000])
        return

    merged = alpaca.fetch_corporate_actions_for_date(trade_date)
    counts = {k: len(v) for k, v in merged.items() if v}
    print("counts:", counts)
    for key in ("reverse_splits", "forward_splits", "name_changes", "cash_mergers"):
        items = merged.get(key) or []
        if items:
            print(key, json.dumps(items[0], indent=2))


if __name__ == "__main__":
    main()
