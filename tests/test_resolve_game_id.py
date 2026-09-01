"""Tests for optional game_id resolution."""

from __future__ import annotations

import pytest


def test_resolve_game_id_single_portfolio_game(fe):
    fe.register(1)
    game_id = fe.new_game(user_id=1, name="Solo", start_date="2026-01-01", total_picks=3)
    fe.be.update_game(game_id, status="active")

    resolved = fe.resolve_game_id(1, None, purpose="portfolio")
    assert resolved == str(game_id)


def test_resolve_game_id_multiple_games_raises(fe):
    fe.register(1)
    g1 = fe.new_game(user_id=1, name="A", start_date="2026-01-01", total_picks=3)
    g2 = fe.new_game(user_id=1, name="B", start_date="2026-01-01", total_picks=3)
    fe.be.update_game(g1, status="active")
    fe.be.update_game(g2, status="active")

    with pytest.raises(LookupError, match="multiple games"):
        fe.resolve_game_id(1, None, purpose="portfolio")


def test_resolve_game_id_buy_requires_active_and_capacity(fe):
    fe.register(1)
    fe.be.add_stock("MSFT", "nasdaq", "Microsoft")
    fe.be.add_stock("AAPL", "nasdaq", "Apple")
    msft = fe.be.get_stock("MSFT")
    aapl = fe.be.get_stock("AAPL")
    game_id = fe.new_game(user_id=1, name="Buy", start_date="2026-01-01", total_picks=2)
    fe.be.update_game(game_id, status="active")
    participant = fe.be.get_many_participants(user_id=1, game_id=game_id)[0]

    resolved = fe.resolve_game_id(1, None, purpose="buy")
    assert resolved == str(game_id)

    fe.be.add_stock_pick(participant.id, msft.id)
    fe.be.add_stock_pick(participant.id, aapl.id)

    with pytest.raises(LookupError, match="No matching game"):
        fe.resolve_game_id(1, None, purpose="buy")


def test_resolve_game_id_remove_pick_at_capacity(fe):
    fe.register(1)
    fe.be.add_stock("MSFT", "nasdaq", "Microsoft")
    fe.be.add_stock("AAPL", "nasdaq", "Apple")
    fe.be.add_stock("GOVT", "nasdaq", "Gov")
    msft = fe.be.get_stock("MSFT")
    aapl = fe.be.get_stock("AAPL")
    govt = fe.be.get_stock("GOVT")
    game_id = fe.new_game(user_id=1, name="Full", start_date="2026-01-01", total_picks=3)
    fe.be.update_game(game_id, status="active")
    participant = fe.be.get_many_participants(user_id=1, game_id=game_id)[0]

    fe.be.add_stock_pick(participant.id, msft.id)
    fe.be.add_stock_pick(participant.id, aapl.id)
    fe.be.add_stock_pick(participant.id, govt.id)

    with pytest.raises(LookupError, match="No matching game"):
        fe.resolve_game_id(1, None, purpose="buy")

    resolved = fe.resolve_game_id(1, None, purpose="remove_pick")
    assert resolved == str(game_id)
    assert fe.resolve_game_id(1, game_id, purpose="remove_pick") == str(game_id)


def test_resolve_game_id_remove_pick_requires_pending(fe):
    fe.register(1)
    fe.be.add_stock("MSFT", "nasdaq", "Microsoft")
    msft = fe.be.get_stock("MSFT")
    game_id = fe.new_game(user_id=1, name="Owned", start_date="2026-01-01", total_picks=1)
    fe.be.update_game(game_id, status="active")
    participant = fe.be.get_many_participants(user_id=1, game_id=game_id)[0]
    fe.be.add_stock_pick(participant.id, msft.id)
    pick = fe.be.get_many_stock_picks(participant_id=participant.id)[0]
    fe.be.update_stock_pick(pick.id, status="owned")

    with pytest.raises(LookupError, match="No matching game"):
        fe.resolve_game_id(1, None, purpose="remove_pick")
    with pytest.raises(LookupError, match="not eligible"):
        fe.resolve_game_id(1, game_id, purpose="remove_pick")


def test_resolve_game_id_no_games(fe):
    fe.register(1)
    with pytest.raises(LookupError, match="No matching game"):
        fe.resolve_game_id(1, None, purpose="leave")
