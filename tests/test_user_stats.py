"""Tests for expanded /user-stats backend."""

from __future__ import annotations


def test_get_user_stats_recurring_podiums_and_picks(fe):
    fe.register(1)
    fe.register(2)
    fe.be.add_stock("WIN", "nasdaq", "Winner")
    fe.be.add_stock("LOSE", "nasdaq", "Loser")
    win = fe.be.get_stock("WIN")
    lose = fe.be.get_stock("LOSE")

    template_id = fe.be.add_game_template(
        user_id=10,
        name="Monthly",
        start_date="2026-01-01",
        starting_money=1000,
        total_picks=1,
    )
    game_id = fe.be.add_game(
        user_id=10,
        name="Jan Recurring",
        start_date="2026-01-01",
        starting_money=1000,
        total_picks=1,
        template_id=template_id,
    )
    fe.be.update_game(game_id, status="ended")
    fe.join_game(1, game_id)
    fe.join_game(2, game_id)
    p1 = fe.be.get_many_participants(user_id=1, game_id=game_id)[0]
    p2 = fe.be.get_many_participants(user_id=2, game_id=game_id)[0]
    fe.be.update_participant(p1.id, current_value=1200, change_dollars=200, change_percent=20)
    fe.be.update_participant(p2.id, current_value=900, change_dollars=-100, change_percent=-10)
    fe.be.add_stock_pick(p1.id, win.id)
    fe.be.add_stock_pick(p2.id, lose.id)
    pick1 = fe.be.get_many_stock_picks(participant_id=p1.id)[0]
    pick2 = fe.be.get_many_stock_picks(participant_id=p2.id)[0]
    fe.be.update_stock_pick(
        pick_id=pick1.id, status="owned", shares=10, current_value=1200,
        change_dollars=200, change_percent=25.0,
    )
    fe.be.update_stock_pick(
        pick_id=pick2.id, status="owned", shares=10, current_value=900,
        change_dollars=-100, change_percent=-15.0,
    )

    stats = fe.get_user_stats(1)
    assert stats.recurring_first == 1
    assert stats.recurring_second == 0
    assert stats.best_stock_ticker == "WIN"
    assert stats.best_stock_percent == 25.0
    assert stats.best_recurring_rank == 1
    assert stats.best_recurring_game_name == "Jan Recurring"

    stats2 = fe.get_user_stats(2)
    assert stats2.recurring_third == 0
    assert stats2.worst_stock_ticker == "LOSE"
    assert stats2.worst_stock_percent == -15.0
