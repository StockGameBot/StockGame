"""Tests for /game-list ranking and bot-owned recurring games."""

from datetime import date

import pytest


def test_list_games_ranked_recurring_first_then_prominence_then_players(fe, mocker):
    owner = 10
    other = 20
    fe.register(other)

    # Far-future non-recurring with many players (should rank below prominent)
    far_id = fe.new_game(user_id=other, name="FarCrowd", start_date="2099-01-01", total_picks=5)
    for uid in range(30, 35):
        fe.register(uid)
        fe.join_game(uid, far_id)

    # Prominent non-recurring (start soon, buy anytime) with fewer players
    near_id = fe.new_game(user_id=owner, name="NearQuiet", start_date="2026-08-15", total_picks=5)
    fe.register(40)
    fe.join_game(40, near_id)

    # Recurring (template_id set) with one player - must still beat non-recurring
    fe.be.add_game_template(
        user_id=owner,
        name="RecurringTop",
        start_date="2026-08-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    from stocks import GameLogic

    logic = GameLogic(fe.be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 8, 1))
    # Use Frontend's GameLogic path via fe.gl for ranking today, but spawn via local logic
    fe.gl.recurring_game_owner_id = None
    mocker.patch.object(fe.gl, "_today_et", return_value=date(2026, 8, 1))
    # Spawn with template owner so games appear under existing owners in list_games
    logic.recurring_games()
    recurring_games = [
        g for g in fe.be.get_many_games(include_private=True) if g.template_id is not None
    ]
    assert recurring_games

    ranked = fe.list_games_ranked(today=date(2026, 8, 1))
    names = [g.name for g, _ in ranked]
    assert names[0] == "RecurringTop Aug 2026"
    # Near (prominent) before far (not prominent), even with fewer players
    assert names.index("NearQuiet") < names.index("FarCrowd")


def test_list_games_owner_filter(fe):
    fe.register(20)
    fe.new_game(user_id=10, name="OwnerTen", start_date="2099-01-01")
    fe.new_game(user_id=20, name="OwnerTwenty", start_date="2099-02-01")
    only_ten = fe.list_games(owner_id=10)
    assert {g.name for g in only_ten} == {"OwnerTen"}


def test_game_is_time_prominent_pick_rules():
    from stocks import Frontend
    from helpers.datatype_validation import Game
    from datetime import datetime

    today = date(2026, 8, 1)

    def _game(**kwargs):
        base = dict(
            game_id="AAAAA",
            name="G",
            owner_user_id=1,
            start_money=10000,
            pick_count=10,
            draft_mode=False,
            private_game=False,
            allow_selling=False,
            update_frequency="alpaca",
            start_date=today,
            status="open",
            datetime_created=datetime(2026, 1, 1),
        )
        base.update(kwargs)
        return Game.model_validate(base)

    assert Frontend.game_is_time_prominent(_game(pick_date=None), today) is True
    assert Frontend.game_is_time_prominent(_game(pick_date=date(2026, 8, 20)), today) is True
    # Past pick date is not prominent even if start is nearby
    assert Frontend.game_is_time_prominent(_game(pick_date=date(2026, 7, 1)), today) is False
    # Start too far out
    assert Frontend.game_is_time_prominent(
        _game(start_date=date(2026, 10, 1), pick_date=None), today
    ) is False


def test_game_status_emoji():
    from stocks import Frontend
    from helpers.datatype_validation import Game
    from datetime import datetime

    today = date(2026, 8, 1)

    def _game(**kwargs):
        base = dict(
            game_id="AAAAA",
            name="G",
            owner_user_id=1,
            start_money=10000,
            pick_count=10,
            draft_mode=False,
            private_game=False,
            allow_selling=False,
            update_frequency="alpaca",
            start_date=today,
            status="active",
            datetime_created=datetime(2026, 1, 1),
        )
        base.update(kwargs)
        return Game.model_validate(base)

    assert Frontend.game_status_emoji(_game(pick_date=None), today) == "💸"
    assert Frontend.game_status_emoji(_game(pick_date=date(2026, 8, 1)), today) == "💸"
    assert Frontend.game_status_emoji(_game(pick_date=date(2026, 7, 1)), today) == "🏃🏻‍➡️"
    assert Frontend.game_status_emoji(
        _game(status="ended", end_date=date(2026, 7, 1)), today
    ) == "🛑"
    assert Frontend.game_status_emoji(
        _game(status="active", end_date=date(2026, 7, 31), pick_date=None), today
    ) == "🛑"


def test_list_my_games_ranked_filters_to_participant(fe):
    fe.register(20)
    mine = fe.new_game(user_id=10, name="Mine", start_date="2099-01-01")
    fe.new_game(user_id=20, name="Theirs", start_date="2099-02-01")
    ranked = fe.list_my_games_ranked(10, today=date(2026, 8, 1))
    assert [g.id for g, _ in ranked] == [mine]
    assert all(count >= 1 for _, count in ranked)


def test_recurring_games_use_configured_bot_owner(be, mocker):
    from stocks import GameLogic

    human = 800
    bot_id = 900
    be.add_user(human, "testing")
    be.add_user(bot_id, "discord", display_name="StockBot")
    be.add_game_template(
        user_id=human,
        name="BotOwnedSeries",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    logic = GameLogic(be.sql.db)
    logic.recurring_game_owner_id = bot_id
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 1, 1))
    logic.recurring_games()

    games = be.get_many_games(owner_id=bot_id, include_private=True)
    assert len(games) == 1
    assert games[0].template_id is not None
    with pytest.raises(LookupError):
        be.get_many_games(owner_id=human, include_private=True)
