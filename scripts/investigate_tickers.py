#!/usr/bin/env python3
"""Probe Alpaca for ticker pricing / metadata issues."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from helpers.alpaca_client import (
    AlpacaMarketData,
    price_from_post_open_trade,
    price_from_snapshot,
    to_alpaca_symbol,
    to_db_ticker,
    trade_timestamp_from_snapshot,
)
from helpers.equity_meta import lookup_company_name

TICKERS = ["ZEUS", "BAD", "KOOL"]


def fmt_ts(dt: datetime | None) -> str:
    if dt is None:
        return "None"
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S %Z")


def main() -> None:
    load_dotenv()
    alpaca = AlpacaMarketData()
    if not alpaca.configured:
        print("Alpaca not configured")
        sys.exit(1)

    trade_date = date.today()
    print(f"Investigation for {TICKERS} on {trade_date}\n")

    for ticker in TICKERS:
        db = to_db_ticker(ticker)
        sym = to_alpaca_symbol(db)
        print("=" * 60)
        print(f"TICKER: {ticker} (db={db}, alpaca={sym})")
        print("=" * 60)

        # Asset metadata
        try:
            asset = alpaca.get_us_equity(db)
            print("ASSET:")
            for k in (
                "name",
                "symbol",
                "class",
                "exchange",
                "status",
                "tradable",
                "marginable",
                "shortable",
                "easy_to_borrow",
            ):
                print(f"  {k}: {asset.get(k)}")
        except Exception as exc:
            print(f"ASSET ERROR: {type(exc).__name__}: {exc}")

        name = lookup_company_name(db, alpaca=alpaca)
        print(f"lookup_company_name: {name!r}")

        # Snapshot (IEX feed - same as game)
        try:
            snaps = alpaca.fetch_snapshots([sym])
            snap = snaps.get(sym)
            if not snap:
                print("SNAPSHOT: missing from response")
            else:
                lt = (snap.get("latestTrade") or {})
                lq = (snap.get("latestQuote") or {})
                db_bar = snap.get("dailyBar") or {}
                prev = snap.get("prevDailyBar") or {}
                print("SNAPSHOT (IEX):")
                print(f"  latestTrade.p: {lt.get('p')}")
                print(f"  latestTrade.s: {lt.get('s')}")
                print(f"  latestTrade.t: {lt.get('t')} -> {fmt_ts(trade_timestamp_from_snapshot(snap))}")
                print(f"  latestQuote.ap/bp: {lq.get('ap')} / {lq.get('bp')}")
                print(f"  dailyBar.c: {db_bar.get('c')} v={db_bar.get('v')}")
                print(f"  prevDailyBar.c: {prev.get('c')} v={prev.get('v')}")
                print(f"  price_from_snapshot: {price_from_snapshot(snap)}")
                print(
                    f"  price_from_post_open_trade: "
                    f"{price_from_post_open_trade(snap, trade_date)}"
                )
        except Exception as exc:
            print(f"SNAPSHOT ERROR: {type(exc).__name__}: {exc}")

        # get_latest_prices path
        try:
            prices = alpaca.get_latest_prices([db])
            print(f"get_latest_prices: {prices}")
        except Exception as exc:
            print(f"get_latest_prices ERROR: {type(exc).__name__}: {exc}")

        # lookup_equity_price
        try:
            price, status = alpaca.lookup_equity_price(db)
            print(f"lookup_equity_price: price={price} status={status}")
        except Exception as exc:
            print(f"lookup_equity_price ERROR: {type(exc).__name__}: {exc}")

        print()


if __name__ == "__main__":
    main()
