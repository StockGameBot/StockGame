"""Alpaca client: batch retries and no silent ticker drops."""

import pytest

from helpers.alpaca_client import AlpacaMarketData, BATCH_SIZE


@pytest.fixture
def alpaca(mocker):
    client = AlpacaMarketData(api_key="test-key", secret_key="test-secret")
    mocker.patch.object(client, "_require_configured")
    mocker.patch("helpers.alpaca_client.time.sleep")  # keep tests fast
    return client


def _snap(price: float) -> dict:
    return {"latestTrade": {"p": price}}


def test_get_latest_prices_retries_failed_batch_then_succeeds(alpaca, mocker):
    fetch = mocker.patch.object(
        alpaca,
        "fetch_snapshots",
        side_effect=[
            RuntimeError("boom"),
            RuntimeError("boom"),
            {"AAA": _snap(10.0), "BBB": _snap(20.0)},
        ],
    )
    prices = alpaca.get_latest_prices(["AAA", "BBB"])
    assert prices == {"AAA": 10.0, "BBB": 20.0}
    assert fetch.call_count == 3


def test_get_latest_prices_retries_missing_symbols_individually(alpaca, mocker):
    def _side_effect(symbols):
        # Batch returns only AAA; BBB must be recovered via single-symbol retry.
        if symbols == ["AAA", "BBB"]:
            return {"AAA": _snap(11.5)}
        if symbols == ["BBB"]:
            return {"BBB": _snap(22.5)}
        raise AssertionError(f"unexpected symbols: {symbols}")

    mocker.patch.object(alpaca, "fetch_snapshots", side_effect=_side_effect)
    prices = alpaca.get_latest_prices(["AAA", "BBB"])
    assert prices == {"AAA": 11.5, "BBB": 22.5}


def test_lookup_equity_price_distinguishes_missing_and_unavailable(alpaca, mocker):
    fetch = mocker.patch.object(
        alpaca,
        "_fetch_snapshots_with_retries",
        side_effect=[None, {"BBB": _snap(1.0)}, {}],
    )
    price, status = alpaca.lookup_equity_price("AAA")
    assert price is None and status == "unavailable"

    price, status = alpaca.lookup_equity_price("BBB")
    assert price == 1.0 and status == "found"

    price, status = alpaca.lookup_equity_price("ZZZ")
    assert price is None and status == "not_found"
    assert fetch.call_count == 3


def test_get_latest_prices_does_not_skip_rest_of_universe_after_batch_failure(alpaca, mocker):
    # Force small batches by temporarily using a tiny universe across two batches.
    # With BATCH_SIZE=100, build 101 symbols so we get two batches.
    tickers = [f"T{i:03d}" for i in range(BATCH_SIZE + 1)]

    def _side_effect(symbols):
        if len(symbols) == BATCH_SIZE:
            raise RuntimeError("first batch failed")
        # Individual retries for the failed batch, or the final single-ticker batch
        out = {}
        for sym in symbols:
            out[sym] = _snap(1.0 + (int(sym[1:]) / 1000))
        return out

    mocker.patch.object(alpaca, "fetch_snapshots", side_effect=_side_effect)
    prices = alpaca.get_latest_prices(tickers)
    assert len(prices) == len(tickers)
    assert set(prices) == set(tickers)


def test_get_latest_prices_logs_when_ticker_truly_unavailable(alpaca, mocker, caplog):
    mocker.patch.object(
        alpaca,
        "fetch_snapshots",
        side_effect=[
            {"GOOD": _snap(5.0)},  # batch: BAD missing
            {},  # individual retry for BAD — still empty
            {},
            {},
        ],
    )
    import logging

    with caplog.at_level(logging.ERROR, logger="AlpacaMarketData"):
        prices = alpaca.get_latest_prices(["GOOD", "BAD"])
    assert prices == {"GOOD": 5.0}
    assert "incomplete after retries" in caplog.text
    assert "BAD" in caplog.text


def test_update_stock_prices_reports_missing_tickers(be, mocker, caplog):
    from stocks import GameLogic
    import logging

    be.add_stock("KEEP", "NASDAQ", "Keep")
    be.add_stock("DROP", "NASDAQ", "Drop")
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic.alpaca, "get_latest_prices", return_value={"KEEP": 9.0})

    with caplog.at_level(logging.ERROR, logger="StockGameLogic"):
        logic.update_stock_prices(force=True)

    assert "dropped" in caplog.text.lower() or "missing" in caplog.text.lower()
    assert "DROP" in caplog.text
    day = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    assert be.get_many_stock_prices(stock_id=be.get_stock("KEEP").id, datetime=day)
