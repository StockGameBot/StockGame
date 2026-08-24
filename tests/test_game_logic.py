import pytest

from stocks import GameLogic


def test_update_all_forwards_target_and_force(be, mocker):
    logic = GameLogic(be.sql.db)
    game_id = "ABCDE"
    update_statuses = mocker.patch.object(logic, "update_game_statuses")
    update_prices = mocker.patch.object(logic, "update_stock_prices")
    update_picks = mocker.patch.object(logic, "update_stock_picks")
    update_totals = mocker.patch.object(logic, "update_participants_and_games")

    logic.update_all(game_id=game_id, force=True)

    update_statuses.assert_called_once_with(game_id=game_id)
    update_prices.assert_called_once_with(game_id=game_id, force=True)
    update_picks.assert_called_once_with(game_id=game_id, force=True)
    update_totals.assert_called_once_with(game_id=game_id)


def test_update_all_runs_recurring_when_no_game_id(be, mocker):
    logic = GameLogic(be.sql.db)
    recurring = mocker.patch.object(logic, "recurring_games")
    mocker.patch.object(logic, "update_game_statuses")
    mocker.patch.object(logic, "update_stock_prices")
    mocker.patch.object(logic, "update_stock_picks")
    mocker.patch.object(logic, "update_participants_and_games")

    logic.update_all(game_id=None, force=False)
    recurring.assert_called_once_with()


def test_participant_and_game_totals_include_uninvested_cash(be):
    owner_id = 101
    other_user_id = 102
    be.add_user(owner_id, "testing")
    be.add_user(other_user_id, "testing")
    be.add_game(
        user_id=owner_id,
        name="CashAccounting",
        start_date="2025-01-01",
        starting_money=10_000,
        total_picks=2,
    )
    game = be.get_many_games(name="CashAccounting", owner_id=owner_id)[0]
    be.update_game(game.id, status="active")
    be.add_participant(owner_id, game.id)
    be.add_participant(other_user_id, game.id)
    participants = be.get_many_participants(game_id=game.id)

    be.add_stock("HALF", "NASDAQ", "Half Invested")
    stock = be.get_stock("HALF")
    be.add_stock_pick(participants[0].id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participants[0].id)[0]
    be.update_stock_pick(
        pick_id=pick.id,
        current_value=5_000,
        shares=50,
        start_value=5_000,
        status="owned",
    )

    GameLogic(be.sql.db).update_participants_and_games(game.id)

    first = be.get_participant(participants[0].id)
    second = be.get_participant(participants[1].id)
    updated_game = be.get_game(game.id)
    assert first.current_value == 10_000
    assert first.change_dollars == 0
    assert second.current_value == 10_000
    assert second.change_dollars == 0
    assert updated_game.current_value == 20_000
    assert updated_game.change_dollars == 0


def test_update_stock_prices_writes_alpaca_snapshots(be, mocker):
    from datetime import datetime

    be.add_stock("AAA", "NASDAQ", "Alpha")
    be.add_stock("BBB", "NASDAQ", "Beta")
    logic = GameLogic(be.sql.db)
    mocker.patch.object(
        logic.alpaca,
        "get_latest_prices",
        return_value={"AAA": 12.5, "BBB": 20.0},
    )

    logic.update_stock_prices(force=True)

    # GameLogic stamps prices with datetime.now() (not the mocked _iso8601 helper).
    day = datetime.now().strftime("%Y-%m-%d")
    aaa = be.get_many_stock_prices(stock_id=be.get_stock("AAA").id, datetime=day)
    bbb = be.get_many_stock_prices(stock_id=be.get_stock("BBB").id, datetime=day)
    assert aaa[0].price == 12.5
    assert bbb[0].price == 20.0


def test_update_stock_prices_swallows_alpaca_errors(be, mocker):
    be.add_stock("ERR", "NASDAQ", "Error Co")
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic.alpaca, "get_latest_prices", side_effect=RuntimeError("boom"))
    logic.update_stock_prices()  # should not raise


def test_update_stock_picks_settles_pending_buy_and_marks_values(be, mocker):
    owner_id = 201
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="SettleBuys",
        start_date="2025-01-01",
        starting_money=10_000,
        total_picks=2,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(game_id=game_id)[0]
    be.add_stock("BUY1", "NASDAQ", "Buy One")
    stock = be.get_stock("BUY1")
    be.add_stock_pick(participant.id, stock.id)
    be.add_stock_price(stock.id, price=50.0, datetime="2025-05-21 10:00:00")

    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_is_market_hours", return_value=False)
    logic.update_stock_picks(game_id=game_id, force=True)

    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    assert pick.status == "owned"
    assert pick.shares == pytest.approx(100.0)  # 5000 / 50
    assert pick.start_value == pytest.approx(5000.0)
    assert pick.current_value == pytest.approx(5000.0)


def test_update_stock_picks_revalues_owned_shares(be, mocker):
    owner_id = 202
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="RevalueOwned",
        start_date="2025-01-01",
        starting_money=10_000,
        total_picks=1,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(game_id=game_id)[0]
    be.add_stock("OWN1", "NASDAQ", "Owned One")
    stock = be.get_stock("OWN1")
    be.add_stock_pick(participant.id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    be.update_stock_pick(
        pick.id,
        status="owned",
        shares=10.0,
        start_value=1000.0,
        current_value=1000.0,
    )
    be.add_stock_price(stock.id, price=120.0, datetime="2025-05-21 10:00:00")

    logic = GameLogic(be.sql.db)
    logic.update_stock_picks(game_id=game_id, force=True)

    updated = be.get_stock_pick(pick.id)
    assert updated.current_value == pytest.approx(1200.0)
    assert updated.change_dollars == pytest.approx(200.0)
    assert updated.change_percent == pytest.approx(20.0)


def test_update_stock_picks_low_start_money_keeps_nonzero_start_value(be, mocker):
    """$1 games with many picks must not store a zero start_value after settlement."""
    owner_id = 203
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="PennySlots",
        start_date="2025-01-01",
        starting_money=1.0,
        total_picks=500,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(game_id=game_id)[0]
    be.add_stock("PENN", "NASDAQ", "Penny")
    stock = be.get_stock("PENN")
    be.add_stock_pick(participant.id, stock.id)
    be.add_stock_price(stock.id, price=10.0, datetime="2025-05-21 10:00:00")

    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_is_market_hours", return_value=False)
    logic.update_stock_picks(game_id=game_id, force=True)

    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    assert pick.status == "owned"
    assert pick.start_value == pytest.approx(0.002)
    assert pick.start_value > 0


def test_update_stock_picks_zero_start_value_does_not_raise(be, mocker):
    """Legacy picks with start_value=0 should revalue without crashing scheduled updates."""
    owner_id = 204
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="LegacyZero",
        start_date="2025-01-01",
        starting_money=10_000,
        total_picks=1,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(game_id=game_id)[0]
    be.add_stock("ZERO", "NASDAQ", "Zero Start")
    stock = be.get_stock("ZERO")
    be.add_stock_pick(participant.id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    be.update_stock_pick(
        pick.id,
        status="owned",
        shares=10.0,
        start_value=0.0,
        current_value=0.0,
    )
    be.add_stock_price(stock.id, price=50.0, datetime="2025-05-21 10:00:00")

    logic = GameLogic(be.sql.db)
    logic.update_stock_picks(game_id=game_id, force=True)

    updated = be.get_stock_pick(pick.id)
    assert updated.current_value == pytest.approx(500.0)
    assert updated.change_dollars == pytest.approx(500.0)
    assert updated.change_percent == pytest.approx(0.0)


def test_find_stock_returns_existing_and_adds_from_market_data(be, mocker):
    be.add_stock("EXIST", "NASDAQ", "Exists")
    logic = GameLogic(be.sql.db)
    assert logic.find_stock("EXIST") == "EXIST"

    mocker.patch.object(logic.alpaca, "get_latest_prices", return_value={"NEWCO": 12.5})
    mocker.patch("helpers.equity_meta.lookup_company_name", return_value="New Company Inc.")
    mocker.patch.object(logic.alpaca, "get_us_equity", side_effect=RuntimeError("401"))
    assert logic.find_stock("NEWCO") == "NEWCO"
    assert be.get_stock("NEWCO").company == "New Company Inc."


def test_find_stock_backfills_placeholder_company_name(be, mocker):
    be.add_stock("RACE", "UNKNOWN", "RACE")
    logic = GameLogic(be.sql.db)
    mocker.patch("helpers.equity_meta.lookup_company_name", return_value="Ferrari N.V.")
    assert logic.find_stock("RACE") == "RACE"
    assert be.get_stock("RACE").company == "Ferrari N.V."


def test_find_stock_raises_when_market_data_misses(be, mocker):
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic.alpaca, "get_latest_prices", return_value={})
    with pytest.raises(ValueError, match="Unable to find stock"):
        logic.find_stock("NOSUCH")


def test_find_stock_raises_when_price_fetch_errors(be, mocker):
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic.alpaca, "get_latest_prices", side_effect=RuntimeError("boom"))
    with pytest.raises(ValueError, match="Unable to find stock"):
        logic.find_stock("RACE")
