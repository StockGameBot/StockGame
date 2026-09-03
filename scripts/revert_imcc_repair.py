#!/usr/bin/env python3
"""Revert the manual IMCC 1:30 reverse-split repair (multiply shares by 30, clear badge).

The bot used to run a one-time IMCC repair on startup that divided share counts by 30
for picks with >=100 shares. That was incorrect for post-split purchases in new games.

Usage:
  python scripts/revert_imcc_repair.py              # dry-run (default)
  python scripts/revert_imcc_repair.py --apply      # write changes
  python scripts/revert_imcc_repair.py --apply --refresh  # revert + force price update
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from stocks import Backend, GameLogic

IMCC_CA_ID = "d05a02a4-a1af-4c06-997f-2d124966852c"
IMCC_REVERT_FACTOR = 30.0
IMCC_SPLIT_BADGE = "1:30 Reverse Split"


def _find_imcc_picks(be: Backend) -> list:
    try:
        stock = be.get_stock("IMCC")
    except LookupError:
        print("IMCC not in database.", file=sys.stderr)
        return []

    import helpers.datatype_validation as dtv

    query = """
    WHERE stock_id = ?
    AND status IN ("owned", "pending_sell", "pending_buy")
    """
    try:
        resp = be.sql.get(table="stock_picks", filters=(query, [stock.id]))
        return list(be._many_get(typeadapter=dtv.StockPicks, resp=resp))
    except LookupError:
        return []


def _pick_needs_revert(pick) -> bool:
    label = getattr(pick, "event_label", None)
    if label == IMCC_SPLIT_BADGE:
        return True
    shares = getattr(pick, "shares", None)
    if shares is None:
        return False
    # Heuristic: wrongly repaired picks are small post-split counts with ~$1k basis
    start = getattr(pick, "start_value", None)
    if start and float(start) >= 500 and float(shares) < 50:
        return True
    return False


def _delete_applied_ca_records(be: Backend) -> int:
    resp = be.sql.get(table="applied_corporate_actions", filters={})
    if resp.status != "success" or not isinstance(resp.result, tuple):
        return 0
    removed = 0
    prefix = IMCC_CA_ID
    for row in resp.result:
        if not isinstance(row, dict):
            continue
        ca_id = str(row.get("alpaca_ca_id") or "")
        if ca_id == prefix or ca_id.startswith(f"{prefix}:pick:"):
            be.sql.delete(
                table="applied_corporate_actions",
                filters={"alpaca_ca_id": ca_id},
            )
            removed += 1
    return removed


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist reversions (default is dry-run)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="After --apply, run GameLogic.update_all(force=True) to reprice picks",
    )
    args = parser.parse_args()

    db_name = os.getenv("DB_NAME")
    if not db_name:
        print("DB_NAME not set in environment.", file=sys.stderr)
        return 1

    be = Backend(db_name)
    picks = [p for p in _find_imcc_picks(be) if _pick_needs_revert(p)]
    if not picks:
        print("No IMCC picks matched revert criteria.")
        return 0

    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: {len(picks)} IMCC pick(s) to revert")
    for pick in picks:
        old_shares = float(pick.shares or 0)
        new_shares = old_shares * IMCC_REVERT_FACTOR
        print(
            f"  pick_id={pick.id} shares {old_shares:.4f} -> {new_shares:.4f} "
            f"start_value={pick.start_value} event_label={pick.event_label!r}"
        )
        if args.apply:
            be.update_stock_pick(pick_id=int(pick.id), shares=new_shares)
            be.sql.update(
                table="stock_picks",
                filters={"pick_id": pick.id},
                items={"event_label": None},
            )

    if args.apply:
        removed = _delete_applied_ca_records(be)
        print(f"Cleared {removed} applied_corporate_actions row(s) for IMCC repair.")
        if args.refresh:
            gl = GameLogic(db_name)
            gl.update_all(force=True)
            print("Ran update_all(force=True) to refresh prices and portfolio values.")
    else:
        print("No changes written. Re-run with --apply to revert.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
