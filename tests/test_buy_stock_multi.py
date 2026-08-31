from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord_bot as db


def test_collect_buy_tickers_dedupes_and_normalizes():
    assert db._collect_buy_tickers("aapl", " msft ", None, "AAPL") == ["AAPL", "MSFT"]


def test_buy_stock_outcome_success(fe, mocker):
    mocker.patch.object(db, "fe", fe)
    game_id = fe.new_game(user_id=10, name="MultiBuy", start_date="2099-01-01")
    participant_id = fe._participant_id(user_id=10, game_id=game_id)
    fe.be.update_participant(participant_id=participant_id, status="active")
    fe.be.add_stock("ONE", "NASDAQ", "One Inc")
    mocker.patch.object(fe.gl, "find_stock", return_value="ONE")

    status, title, description = db._buy_stock_outcome(10, game_id, "ONE")
    assert status == "success"
    assert title == "Stock Purchased"
    assert "ONE" in description


def test_buy_stock_outcome_reports_transient_lookup_failure(fe, mocker):
    mocker.patch.object(db, "fe", fe)
    game_id = fe.new_game(user_id=10, name="LookupFail", start_date="2099-01-01")
    participant_id = fe._participant_id(user_id=10, game_id=game_id)
    fe.be.update_participant(participant_id=participant_id, status="active")
    mocker.patch.object(
        fe.gl,
        "find_stock",
        side_effect=RuntimeError("Stock lookup temporarily unavailable"),
    )

    status, _title, description = db._buy_stock_outcome(10, game_id, "RACE")
    assert status == "failed"
    assert "temporarily unavailable" in description.lower()


def test_invalid_ticker_cache_expires_and_renews(be):
    be.record_invalid_ticker("BAD1")
    assert be.is_ticker_invalid("BAD1")
    be.sql.update(
        "invalid_stocks",
        {"expires_at": "2000-01-01 00:00:00"},
        filters={"ticker": "BAD1"},
    )
    assert not be.is_ticker_invalid("BAD1")
    be.record_invalid_ticker("BAD1")
    assert be.is_ticker_invalid("BAD1")
    be.clear_invalid_ticker("BAD1")
    assert not be.is_ticker_invalid("BAD1")


def test_buy_stock_command_batch_summary(mocker):

    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=10)
    interaction.response = SimpleNamespace(defer=AsyncMock())
    interaction.followup = SimpleNamespace(send=AsyncMock())
    mocker.patch.object(db, "ephemeral_test", True)
    mocker.patch.object(
        db,
        "_buy_stock_outcome",
        side_effect=[
            ("success", "Stock Purchased", "Added AAA"),
            ("failed", "Stock Purchase Failed", "Not found"),
        ],
    )
    mocker.patch.object(db.fe, "pick_capacity", return_value=(1, 3))
    mocker.patch.object(
        db,
        "_resolve_game_id_for_command",
        AsyncMock(return_value="GAME1"),
    )

    async def run():
        command = db.bot.tree.get_command("buy-stock")
        await command.callback(
            interaction,
            "GAME1",
            "AAA",
            ticker_2="BBB",
        )

    asyncio.run(run())
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Stock Purchases"
    assert "AAA" in embed.description
    assert "BBB" in embed.description
