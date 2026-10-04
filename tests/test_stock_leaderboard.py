"""Text stock-pick leaderboard grouping and loading."""

from helpers.affiliations import AFFILIATION_ATRIOC, AFFILIATION_DOUGDOUG
from helpers.stock_leaderboard import (
    PricedPick,
    build_stock_board,
    format_stock_board_field,
)


def _pick(ticker, percent, user_id, affiliation=None, company="Apple Inc"):
    return PricedPick(
        ticker=ticker,
        company_name=company,
        change_percent=percent,
        user_id=user_id,
        affiliation=affiliation,
    )


def test_nearby_percentages_share_one_row_and_drop_funds():
    board = build_stock_board(
        [
            _pick("AAPL", 9.61, 11, AFFILIATION_ATRIOC),
            _pick("AAPL", 10.59, 33, AFFILIATION_DOUGDOUG),
            _pick("AAPL", 10.1, 22, None),
        ]
    )

    assert len(board) == 1
    entry = board[0]
    assert entry.grouped
    assert entry.low_percent == 9.61
    assert entry.high_percent == 10.59
    assert [user_id for user_id, _fund in entry.holders] == [33, 22, 11]

    name, value = format_stock_board_field(entry, 1)
    assert name == "#1 AAPL • Apple Inc • +9.61% to +10.59%"
    assert value == "Picked by: <@33> <@22> <@11>"
    assert "Fund:" not in value


def test_gap_over_half_a_point_stays_separate_and_keeps_fund():
    board = build_stock_board(
        [
            _pick("AAPL", 9.61, 11, AFFILIATION_ATRIOC),
            _pick("AAPL", 10.12, 22, None),
        ]
    )

    assert len(board) == 2
    assert board[0].high_percent == 10.12
    assert not board[0].grouped
    _name, value = format_stock_board_field(board[1], 2)
    assert value == "Picked by: <@11> Fund: Atrioc"
    _top_name, top_value = format_stock_board_field(board[0], 1)
    assert top_value == "Picked by: <@22> Fund: Independent"


def test_exact_half_point_gap_stays_in_the_chain():
    board = build_stock_board(
        [
            _pick("AAPL", 10.0, 1),
            _pick("AAPL", 10.5, 2),
        ]
    )
    assert len(board) == 1
    assert board[0].grouped


def test_different_tickers_are_not_grouped():
    board = build_stock_board(
        [
            _pick("AAPL", 10.0, 1, company="Apple Inc"),
            _pick("MSFT", 10.2, 2, company="Microsoft"),
        ]
    )
    assert [entry.ticker for entry in board] == ["MSFT", "AAPL"]
    assert all(not entry.grouped for entry in board)


def test_list_priced_stock_picks_keeps_held_picks_only(be):
    owner_id = 301
    other_id = 302
    be.add_user(owner_id, "testing")
    be.add_user(other_id, "testing")
    be.add_game(
        user_id=owner_id,
        name="StockBoard",
        start_date="2025-01-01",
        starting_money=10_000,
        total_picks=4,
    )
    game = be.get_many_games(name="StockBoard", owner_id=owner_id)[0]
    be.update_game(game.id, status="active")
    be.add_participant(owner_id, game.id)
    be.add_participant(other_id, game.id)
    participants = {player.user_id: player for player in be.get_many_participants(game_id=game.id)}
    owner = participants[owner_id]
    other = participants[other_id]
    be.update_participant(owner.id, affiliation=AFFILIATION_ATRIOC)

    be.add_stock("AAPL", "NASDAQ", "Apple Inc")
    be.add_stock("SOLD", "NASDAQ", "Sold Co")
    be.add_stock("PEND", "NASDAQ", "Pending Co")
    apple = be.get_stock("AAPL")
    sold = be.get_stock("SOLD")
    pending = be.get_stock("PEND")

    be.add_stock_pick(owner.id, apple.id)
    be.add_stock_pick(other.id, apple.id)
    be.add_stock_pick(owner.id, sold.id)
    be.add_stock_pick(other.id, pending.id)

    owner_apple = be.get_many_stock_picks(participant_id=owner.id, stock_id=apple.id)[0]
    other_apple = be.get_many_stock_picks(participant_id=other.id, stock_id=apple.id)[0]
    sold_pick = be.get_many_stock_picks(participant_id=owner.id, stock_id=sold.id)[0]
    pending_pick = be.get_many_stock_picks(participant_id=other.id, stock_id=pending.id)[0]
    be.update_stock_pick(owner_apple.id, status="owned", change_percent=9.61, shares=1, start_value=100, current_value=109.61)
    be.update_stock_pick(other_apple.id, status="pending_sell", change_percent=10.1, shares=1, start_value=100, current_value=110.1)
    be.update_stock_pick(sold_pick.id, status="sold", change_percent=50, shares=1, start_value=100, current_value=150)
    be.update_stock_pick(pending_pick.id, status="pending_buy", change_percent=1, shares=1, start_value=100, current_value=101)

    picks = be.list_priced_stock_picks(game.id)
    assert {(pick.user_id, pick.ticker, pick.change_percent) for pick in picks} == {
        (owner_id, "AAPL", 9.61),
        (other_id, "AAPL", 10.1),
    }
    by_user = {pick.user_id: pick for pick in picks}
    assert by_user[owner_id].affiliation == AFFILIATION_ATRIOC
    assert by_user[owner_id].company_name == "Apple Inc"
    assert by_user[other_id].affiliation is None

    board = build_stock_board(picks)
    assert len(board) == 1
    assert board[0].grouped
