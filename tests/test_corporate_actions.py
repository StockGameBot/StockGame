"""Tests for corporate action helpers, badges, and market schedule."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from helpers import corporate_actions as ca
from helpers.market_schedule import (
    market_update_times,
    should_apply_corporate_actions,
    update_kind_for_time,
)
from helpers.alpaca_client import is_post_open_trade, to_db_ticker


def test_format_split_badge_imcc_reverse():
    label = ca.format_split_badge(30, 1, "reverse_split")
    assert label == "1:30 Reverse Split"


def test_format_split_badge_forward_two_for_one():
    label = ca.format_split_badge(1, 2, "forward_split")
    assert label == "2:1 Split"


def test_format_cash_merger_badge():
    assert ca.format_cash_merger_badge("FRBA") == "Bought by FRBA"


def test_format_stock_merger_badge():
    assert ca.format_stock_merger_badge("LEG", "SGI") == "LEG & SGI Merger"


def test_market_update_times_includes_pre_and_post():
    times = market_update_times()
    assert times[0].hour == 9 and times[0].minute == 15
    assert times[1].hour == 9 and times[1].minute == 30
    assert times[-1].hour == 16 and times[-1].minute == 15
    assert len(times) >= 27


def test_update_kind_for_open_and_pre():
    ny = ZoneInfo("America/New_York")
    assert update_kind_for_time(datetime(2026, 8, 27, 9, 15, tzinfo=ny)) == "pre_open"
    assert update_kind_for_time(datetime(2026, 8, 27, 9, 30, tzinfo=ny)) == "market"
    assert update_kind_for_time(datetime(2026, 8, 27, 16, 15, tzinfo=ny)) == "post_close"
    assert should_apply_corporate_actions(datetime(2026, 8, 27, 9, 30, tzinfo=ny))


def test_is_post_open_trade():
    trade_date = date(2026, 8, 27)
    open_ms = int(datetime(2026, 8, 27, 9, 31, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
    pre_ms = int(datetime(2026, 8, 27, 9, 0, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
    assert is_post_open_trade({"latestTrade": {"t": open_ms, "p": 3.39}}, trade_date)
    assert not is_post_open_trade({"latestTrade": {"t": pre_ms, "p": 0.11}}, trade_date)


def test_apply_reverse_split_updates_shares(be, mocker):
    from stocks import GameLogic

    logic = GameLogic(be.sql.db)
    be.add_stock("IMCC", "nasdaq", "IMCC Inc")
    stock = be.get_stock("IMCC")
    be.add_user(1, "discord", "player")
    game_id = be.add_game(1, "Split Game", "2026-01-01", starting_money=1000, total_picks=1)
    be.add_participant(1, game_id, force_active=True)
    participant = be.get_many_participants(user_id=1, game_id=game_id)[0]
    be.add_stock_pick(participant.id, stock.id)
    picks = be.get_many_stock_picks(participant_id=participant.id)
    be.update_stock_pick(
        pick_id=picks[0].id,
        shares=9000.0,
        start_value=1000.0,
        current_value=1000.0,
        status="owned",
    )
    trade_date = date(2026, 8, 27)
    payload = ca.format_split_badge(30, 1, "reverse_split")
    be.insert_staged_corporate_action(
        alpaca_ca_id="ca-imcc-test",
        action_type="reverse_split",
        stock_id=stock.id,
        pick_id=picks[0].id,
        share_factor=1 / 30,
        payload='{"badge": "' + payload + '"}',
        trade_date=trade_date.isoformat(),
        datetime_staged="2026-08-27 09:00:00",
    )
    report = ca.apply_staged_corporate_actions(
        be, logic, trade_date, {}, phase="pre_price"
    )
    assert report.applied == 1
    updated = be.get_stock_pick(picks[0].id)
    assert updated.shares == pytest.approx(300.0)
    assert updated.event_label == "1:30 Reverse Split"


def test_buy_stock_rejects_delisted(fe, mocker):
    be = fe.be
    be.add_stock("DEAD", "nasdaq", "Dead Co")
    stock = be.get_stock("DEAD")
    be.set_stock_trade_status(stock.id, "delisted")
    owner_id = 10
    game_id = be.add_game(owner_id, "G", "2026-01-01", starting_money=1000, total_picks=1)
    be.add_participant(owner_id, game_id, force_active=True)
    mocker.patch.object(fe.gl, "find_stock", return_value="DEAD")
    with pytest.raises(ValueError, match="delisted"):
        fe.buy_stock(user_id=owner_id, game_id=game_id, ticker="DEAD")


def test_buy_stock_rejects_untradeable_on_alpaca(fe, mocker):
    be = fe.be
    be.add_stock("ZEUS", "nasdaq", "ZEUS")
    owner_id = 10
    game_id = be.add_game(owner_id, "G", "2026-01-01", starting_money=1000, total_picks=1)
    be.add_participant(owner_id, game_id, force_active=True)
    mocker.patch.object(fe.gl, "find_stock", return_value="ZEUS")
    mocker.patch.object(fe.gl.alpaca, "get_us_equity", side_effect=ValueError("Stock is not tradeable"))
    with pytest.raises(ValueError, match="not tradeable"):
        fe.buy_stock(user_id=owner_id, game_id=game_id, ticker="ZEUS")


def test_repair_untradeable_equity_removes_picks(be, mocker):
    from helpers.repairs_0_2_8 import repair_untradeable_equity_picks

    be.add_stock("ZEUS", "nasdaq", "ZEUS")
    be.add_stock("MSFT", "nasdaq", "Microsoft")
    zeus = be.get_stock("ZEUS")
    msft = be.get_stock("MSFT")
    be.add_user(1, "discord", "player")
    game_id = be.add_game(1, "G", "2026-01-01", starting_money=1000, total_picks=3)
    be.add_participant(1, game_id, force_active=True)
    participant = be.get_many_participants(user_id=1, game_id=game_id)[0]
    be.add_stock_pick(participant.id, zeus.id)
    be.add_stock_pick(participant.id, msft.id)
    zeus_picks = be.get_many_stock_picks(participant_id=participant.id, stock_id=zeus.id)
    msft_picks = be.get_many_stock_picks(participant_id=participant.id, stock_id=msft.id)
    be.update_stock_pick(pick_id=zeus_picks[0].id, status="owned", shares=10.0, current_value=500.0)
    be.update_stock_pick(pick_id=msft_picks[0].id, status="owned", shares=5.0, current_value=500.0)

    class FakeAlpaca:
        configured = True

        def fetch_buyable_db_tickers(self):
            return {"MSFT"}

    report = repair_untradeable_equity_picks(be, FakeAlpaca(), force=True)
    assert report.picks_removed == 1
    assert "ZEUS" in report.tickers
    remaining = be.get_many_stock_picks(participant_id=participant.id)
    assert len(remaining) == 1
    assert remaining[0].stock_id == msft.id
    assert be.get_stock("ZEUS").trade_status == "delisted"


def test_repair_stress_test_end_date(be):
    from helpers.repairs_0_2_8 import repair_stress_test_end_date

    be.add_user(1, "discord", "owner")
    game_id = be.add_game(1, "Official Stress Test Jul 2026", "2026-07-01", total_picks=1)
    assert be.get_game(game_id).end_date is None

    assert repair_stress_test_end_date(be, force=True) is True
    assert str(be.get_game(game_id).end_date) == "2026-08-31"
    assert repair_stress_test_end_date(be, force=True) is False


def test_portfolio_event_badge_render():
    from helpers.views import StockPortfolioImageGenerator
    from PIL import Image, ImageDraw

    gen = StockPortfolioImageGenerator()
    img = Image.new("RGB", (800, 120), gen.colors["bg"])
    draw = ImageDraw.Draw(img)
    gen._draw_stock_title(
        draw,
        40,
        {
            "stock_ticker": "IMCC",
            "company_name": "IMCC Inc",
            "event_label": "1:30 Reverse Split",
        },
    )


def test_split_price_retry_waits_for_post_open_trade(mocker):
    from helpers.alpaca_client import AlpacaMarketData

    trade_date = date(2026, 8, 27)
    ny = ZoneInfo("America/New_York")
    pre_ms = int(datetime(2026, 8, 27, 9, 0, tzinfo=ny).timestamp() * 1000)
    open_ms = int(datetime(2026, 8, 27, 9, 31, tzinfo=ny).timestamp() * 1000)
    alpaca = AlpacaMarketData(api_key="k", secret_key="s")
    calls = {"n": 0}

    def fake_fetch(symbols, *, attempts=3):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"IMCC": {"latestTrade": {"t": pre_ms, "p": 0.11}}}
        return {"IMCC": {"latestTrade": {"t": open_ms, "p": 3.39}}}

    mocker.patch.object(alpaca, "_fetch_snapshots_with_retries", side_effect=fake_fetch)
    mocker.patch("helpers.alpaca_client.time.sleep")
    prices = alpaca.get_latest_prices(
        ["IMCC"],
        split_tickers={"IMCC"},
        trade_date=trade_date,
    )
    assert prices["IMCC"] == pytest.approx(3.39)
    assert calls["n"] >= 2


def test_delisted_pick_value_unchanged_on_update(be, mocker):
    from stocks import GameLogic

    logic = GameLogic(be.sql.db)
    be.add_stock("DEAD", "nasdaq", "Dead Co")
    stock = be.get_stock("DEAD")
    be.set_stock_trade_status(stock.id, "delisted")
    be.add_user(1, "discord", "player")
    game_id = be.add_game(1, "G", "2026-01-01", starting_money=1000, total_picks=1)
    be.add_participant(1, game_id, force_active=True)
    participant = be.get_many_participants(user_id=1, game_id=game_id)[0]
    be.add_stock_pick(participant.id, stock.id)
    picks = be.get_many_stock_picks(participant_id=participant.id)
    be.update_stock_pick(
        pick_id=picks[0].id,
        shares=100.0,
        start_value=500.0,
        current_value=500.0,
        change_dollars=0.0,
        change_percent=0.0,
        status="owned",
        event_label="Delisted",
    )
    logic._latest_prices = {"DEAD": 999.0}
    mocker.patch.object(logic, "_is_market_hours", return_value=True)
    logic.update_stock_picks(game_id=game_id, force=True)
    updated = be.get_stock_pick(picks[0].id)
    assert updated.current_value == pytest.approx(500.0)


def test_apply_name_change_renames_ticker_and_sets_badge(be, mocker):
    from stocks import GameLogic

    logic = GameLogic(be.sql.db)
    be.add_stock("AIHS", "nasdaq", "Old Name")
    stock = be.get_stock("AIHS")
    be.add_user(1, "discord", "player")
    game_id = be.add_game(1, "G", "2026-01-01", starting_money=1000, total_picks=1)
    be.add_participant(1, game_id, force_active=True)
    participant = be.get_many_participants(user_id=1, game_id=game_id)[0]
    be.add_stock_pick(participant.id, stock.id)
    picks = be.get_many_stock_picks(participant_id=participant.id)
    be.update_stock_pick(pick_id=picks[0].id, status="owned", shares=10.0)
    trade_date = date(2026, 8, 27)
    payload = '{"old_symbol": "AIHS", "new_symbol": "NEWS", "badge": "Renamed from AIHS"}'
    be.insert_staged_corporate_action(
        alpaca_ca_id="ca-rename",
        action_type="name_change",
        stock_id=stock.id,
        pick_id=None,
        share_factor=None,
        payload=payload,
        trade_date=trade_date.isoformat(),
        datetime_staged="2026-08-27 09:00:00",
    )
    be.insert_staged_corporate_action(
        alpaca_ca_id="ca-rename:pick:1",
        action_type="name_change_pick",
        stock_id=stock.id,
        pick_id=picks[0].id,
        share_factor=None,
        payload=payload,
        trade_date=trade_date.isoformat(),
        datetime_staged="2026-08-27 09:00:00",
    )
    ca.apply_staged_corporate_actions(be, logic, trade_date, {}, phase="all")
    renamed = be.get_stock(stock.id)
    assert renamed.ticker == "NEWS"
    updated = be.get_stock_pick(picks[0].id)
    assert updated.event_label == "Renamed from AIHS"


def test_apply_cash_merger_freezes_value(be, mocker):
    from stocks import GameLogic

    logic = GameLogic(be.sql.db)
    be.add_stock("LEG", "nyse", "Leggett")
    stock = be.get_stock("LEG")
    be.add_user(1, "discord", "player")
    game_id = be.add_game(1, "G", "2026-01-01", starting_money=1000, total_picks=1)
    be.add_participant(1, game_id, force_active=True)
    participant = be.get_many_participants(user_id=1, game_id=game_id)[0]
    be.add_stock_pick(participant.id, stock.id)
    picks = be.get_many_stock_picks(participant_id=participant.id)
    be.update_stock_pick(
        pick_id=picks[0].id,
        shares=10.0,
        start_value=100.0,
        current_value=100.0,
        status="owned",
    )
    trade_date = date(2026, 8, 27)
    payload = (
        '{"rate": 12.5, "acquirer_symbol": "FRBA", '
        '"badge": "Bought by FRBA", "action_type": "cash_merger"}'
    )
    be.insert_staged_corporate_action(
        alpaca_ca_id="ca-cash",
        action_type="cash_merger",
        stock_id=stock.id,
        pick_id=picks[0].id,
        share_factor=None,
        payload=payload,
        trade_date=trade_date.isoformat(),
        datetime_staged="2026-08-27 09:30:00",
    )
    report = ca.apply_staged_corporate_actions(
        be, logic, trade_date, {}, phase="post_price"
    )
    assert report.applied == 1
    updated = be.get_stock_pick(picks[0].id)
    assert updated.current_value == pytest.approx(125.0)
    assert updated.event_label == "Bought by FRBA"
    assert be.get_stock(stock.id).trade_status == "merged"


def test_stage_corporate_actions_skips_pending_buy(be, mocker):
    be.add_stock("SPLT", "nasdaq", "Split Co")
    stock = be.get_stock("SPLT")
    be.add_user(1, "discord", "player")
    game_id = be.add_game(1, "G", "2026-01-01", starting_money=1000, total_picks=2)
    be.add_participant(1, game_id, force_active=True)
    participant = be.get_many_participants(user_id=1, game_id=game_id)[0]
    be.add_stock_pick(participant.id, stock.id)
    picks = be.get_many_stock_picks(participant_id=participant.id)
    be.update_stock_pick(pick_id=picks[0].id, status="pending_buy")
    trade_date = date(2026, 8, 27)

    class FakeAlpaca:
        configured = True

        def fetch_corporate_actions_for_date(self, _date):
            return {
                "reverse_splits": [
                    {
                        "id": "ca-splt",
                        "symbol": "SPLT",
                        "old_rate": 10,
                        "new_rate": 1,
                    }
                ]
            }

    report = ca.stage_corporate_actions(be, FakeAlpaca(), trade_date, force_if_empty=True)
    assert report.splits == 1
    assert report.picks_staged == 0
    assert be.count_staged_corporate_actions(trade_date.isoformat()) == 0


def test_buy_stock_outcome_delisted_message(fe, mocker):
    import discord_bot as db

    be = fe.be
    be.add_stock("DEAD", "nasdaq", "Dead Co")
    stock = be.get_stock("DEAD")
    be.set_stock_trade_status(stock.id, "delisted")
    owner_id = 10
    game_id = be.add_game(owner_id, "G", "2026-01-01", starting_money=1000, total_picks=1)
    be.add_participant(owner_id, game_id, force_active=True)
    mocker.patch.object(fe, "buy_stock", side_effect=ValueError(
        "Stock was delisted and can no longer be purchased."
    ))
    mocker.patch.object(db, "fe", fe)
    status, _title, description = db._buy_stock_outcome(owner_id, game_id, "DEAD")
    assert status == "failed"
    assert "delisted" in description.lower()

