"""Tests for one-off 2026-10-12 NXGL/NIVF split repair script."""

from __future__ import annotations

from datetime import date

from scripts.apply_missed_splits_2026_10_12 import (
    EXPECTED_CA_IDS,
    SCRIPT_MARKER,
    TRADE_DATE,
    apply_plans,
    plan_missed_split_repairs,
)


def _ca_payload(ticker: str, *, old_rate: float, new_rate: float) -> dict:
    return {
        "reverse_splits": [
            {
                "id": EXPECTED_CA_IDS[ticker],
                "symbol": ticker,
                "old_rate": old_rate,
                "new_rate": new_rate,
                "ex_date": TRADE_DATE.isoformat(),
                "process_date": TRADE_DATE.isoformat(),
            }
        ]
    }


def test_plan_and_apply_records_marker_once(be):
    owner_id = 501
    be.add_user(owner_id, "testing")
    be.add_game(
        user_id=owner_id,
        name="SplitRepair",
        start_date="2026-10-01",
        starting_money=10_000,
        total_picks=2,
    )
    game = be.get_many_games(name="SplitRepair", owner_id=owner_id)[0]
    be.update_game(game.id, status="active")
    be.add_participant(owner_id, game.id)
    participant = be.get_many_participants(user_id=owner_id, game_id=game.id)[0]

    be.add_stock("NXGL", "NASDAQ", "Nexgel")
    stock = be.get_stock("NXGL")
    be.add_stock_pick(participant.id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    be.update_stock_pick(
        pick_id=pick.id,
        shares=100.0,
        start_value=1000.0,
        current_value=1000.0,
        status="owned",
    )

    ca_data = _ca_payload("NXGL", old_rate=10, new_rate=1)
    ca_data["reverse_splits"].append(
        {
            "id": EXPECTED_CA_IDS["NIVF"],
            "symbol": "NIVF",
            "old_rate": 20,
            "new_rate": 1,
            "ex_date": TRADE_DATE.isoformat(),
        }
    )
    plans, warnings = plan_missed_split_repairs(be, ca_data)
    assert len(plans) == 1
    assert not any("NXGL" in w for w in warnings)
    assert plans[0].old_shares == 100.0
    assert plans[0].new_shares == 10.0

    assert apply_plans(be, plans) == 1
    assert be.is_corporate_action_applied(SCRIPT_MARKER)
    assert be.is_corporate_action_applied(plans[0].applied_key)

    updated = be.get_stock_pick(pick.id)
    assert updated.shares == 10.0
    assert updated.event_label == "1:10 Reverse Split"

    # Second apply is a no-op (per-pick key + marker).
    assert apply_plans(be, plans) == 0


def test_plan_uses_alpaca_ca_id_when_log_id_differs(be):
    owner_id = 502
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="SplitRepair2",
        start_date="2026-10-01",
        starting_money=10_000,
        total_picks=2,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(user_id=owner_id, game_id=game_id)[0]
    be.add_stock("NXGL", "NASDAQ", "Nexgel")
    stock = be.get_stock("NXGL")
    be.add_stock_pick(participant.id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    be.update_stock_pick(
        pick_id=pick.id,
        shares=50.0,
        start_value=1000.0,
        current_value=1000.0,
        status="owned",
    )
    bad = _ca_payload("NXGL", old_rate=10, new_rate=1)
    bad["reverse_splits"][0]["id"] = "wrong-id"
    bad["reverse_splits"].append(
        {
            "id": EXPECTED_CA_IDS["NIVF"],
            "symbol": "NIVF",
            "old_rate": 20,
            "new_rate": 1,
            "ex_date": TRADE_DATE.isoformat(),
        }
    )
    plans, warnings = plan_missed_split_repairs(be, bad)
    assert len(plans) == 1
    assert plans[0].ca_id == "wrong-id"
    assert any("ca_id" in w for w in warnings)
