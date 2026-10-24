"""Tests for one-off 2026-10-23 NIVF split repair script."""

from __future__ import annotations

from datetime import date

from helpers import corporate_actions as ca
from scripts.apply_missed_split_nivf_2026_10_23 import (
    SCRIPT_MARKER,
    TRADE_DATE,
    apply_plans,
    plan_missed_split_repairs,
)


def _ca_payload(*, ca_id: str, old_rate: float = 8, new_rate: float = 1) -> dict:
    return {
        "reverse_splits": [
            {
                "id": ca_id,
                "symbol": "NIVF",
                "old_rate": old_rate,
                "new_rate": new_rate,
                "ex_date": TRADE_DATE.isoformat(),
                "process_date": TRADE_DATE.isoformat(),
            }
        ]
    }


def test_plan_applies_oct23_split_on_pick_already_badged_oct12(be):
    owner_id = 601
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="NIVF2Split",
        start_date="2026-10-01",
        starting_money=10_000,
        total_picks=2,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(user_id=owner_id, game_id=game_id)[0]
    be.add_stock("NIVF", "NASDAQ", "NewGenIvf")
    stock = be.get_stock("NIVF")
    be.add_stock_pick(participant.id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    badge1 = ca.format_split_badge(4, 1, "reverse_split")
    be.update_stock_pick(
        pick_id=pick.id,
        shares=3526.0,
        start_value=1000.0,
        current_value=5000.0,
        status="owned",
        event_label=badge1,
    )

    ca_id = "ca-nivf-2026-10-23-test"
    plans, warnings = plan_missed_split_repairs(be, _ca_payload(ca_id=ca_id))
    assert not warnings
    assert len(plans) == 1
    assert plans[0].old_shares == 3526.0
    assert plans[0].new_shares == 3526.0 / 8

    assert apply_plans(be, plans) == 1
    assert be.is_corporate_action_applied(SCRIPT_MARKER)
    assert be.is_corporate_action_applied(f"{ca_id}:pick:{pick.id}")

    updated = be.get_stock_pick(pick.id)
    assert updated.shares == 3526.0 / 8
    assert badge1 in (updated.event_label or "")
    assert "1:8 Reverse Split" in (updated.event_label or "")

    assert apply_plans(be, plans) == 0
