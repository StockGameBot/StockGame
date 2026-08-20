from datetime import date, datetime

import pytest

import helpers.exceptions as bexc


def test_game_template_round_trip_allows_no_pick_deadline(be):
    owner_id = 501
    be.add_user(owner_id, "testing")

    be.add_game_template(
        user_id=owner_id,
        name="Monthly Game",
        start_date="2099-08-01",
        recurring_period=2,
        pick_date=None,
    )

    templates = be.get_many_game_templates(status="enabled")
    assert len(templates) == 1
    template = be.get_game_template(templates[0].id)
    assert template.name == "Monthly Game"
    assert template.owner_id == owner_id
    assert template.recurring_period == 2
    assert template.pick_date is None


def test_next_recurring_start_uses_anchor_and_clamps_february(be):
    from stocks import GameLogic

    logic = GameLogic(be.sql.db)
    anchor = date(2026, 7, 30)

    assert logic._next_recurring_start(anchor, 1) == date(2026, 7, 30)
    assert logic._next_recurring_start(anchor, 1, after=date(2026, 7, 30)) == date(2026, 8, 30)
    assert logic._next_recurring_start(anchor, 1, after=date(2027, 1, 30)) == date(2027, 2, 28)
    assert logic._next_recurring_start(anchor, 1, after=date(2027, 2, 28)) == date(2027, 3, 30)

    leap_anchor = date(2024, 1, 31)
    assert logic._next_recurring_start(leap_anchor, 1, after=date(2024, 1, 31)) == date(2024, 2, 29)
    assert logic._next_recurring_start(leap_anchor, 1, after=date(2024, 2, 29)) == date(2024, 3, 31)


def test_recurring_games_create_due_template_once(be, mocker):
    from stocks import GameLogic

    owner_id = 502
    be.add_user(owner_id, "testing")
    be.add_game_template(
        user_id=owner_id,
        name="BiMonthly",
        start_date="2025-03-01",
        create_days_in_advance=7,
        recurring_period=2,
        pick_date=None,
    )
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=date(2025, 2, 25))

    logic.recurring_games()
    logic.recurring_games()

    games = be.get_many_games(owner_id=owner_id, include_private=True)
    assert len(games) == 1
    assert games[0].template_id is not None
    assert games[0].start_date == date(2025, 3, 1)
    assert games[0].name == "BiMonthly Mar 2025"


def test_recurring_games_first_start_is_template_start_date(be, mocker):
    from stocks import GameLogic

    owner_id = 503
    be.add_user(owner_id, "testing")
    be.add_game_template(
        user_id=owner_id,
        name="monthly1",
        start_date="2026-07-31",
        create_days_in_advance=1,
        recurring_period=1,
        pick_date=None,
    )
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 7, 30))

    logic.recurring_games()

    games = be.get_many_games(owner_id=owner_id, include_private=True)
    assert len(games) == 1
    assert games[0].start_date == date(2026, 7, 31)
    assert games[0].name == "monthly1 Jul 2026"


def test_add_game_template_rejects_duplicate_name(be):
    owner_id = 504
    be.add_user(owner_id, "testing")
    be.add_game_template(
        user_id=owner_id,
        name="duplicate-name",
        start_date="2026-08-01",
    )
    with pytest.raises(bexc.AlreadyExistsError):
        be.add_game_template(
            user_id=owner_id,
            name="duplicate-name",
            start_date="2026-09-01",
        )


def test_add_game_template_rejects_exclusive_without_pick_date(be):
    owner_id = 506
    be.add_user(owner_id, "testing")
    with pytest.raises(ValueError, match="pick_date"):
        be.add_game_template(
            user_id=owner_id,
            name="draft-bad",
            start_date="2026-08-01",
            exclusive_picks=True,
            pick_date=None,
        )


def test_add_game_template_rejects_length_gt_period(be):
    owner_id = 507
    be.add_user(owner_id, "testing")
    with pytest.raises(ValueError, match="game_length"):
        be.add_game_template(
            user_id=owner_id,
            name="overlap-bad",
            start_date="2026-08-01",
            recurring_period=1,
            game_length=2,
        )


def test_recurring_games_uniquifies_month_year_name(be, mocker):
    from stocks import GameLogic

    owner_id = 505
    be.add_user(owner_id, "testing")
    be.add_game(
        user_id=owner_id,
        name="Monthly Series Jul 2026",
        start_date="2026-07-01",
    )
    be.add_game_template(
        user_id=owner_id,
        name="Monthly Series",
        start_date="2026-07-31",
        create_days_in_advance=1,
        recurring_period=1,
        pick_date=None,
    )
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 7, 30))

    logic.recurring_games()
    games = be.get_many_games(owner_id=owner_id, include_private=True)
    names = sorted(game.name for game in games)
    assert names == ["Monthly Series Jul 2026", "Monthly Series Jul 2026 #2"]
    spawned = [game for game in games if game.template_id is not None]
    assert len(spawned) == 1
    assert spawned[0].name == "Monthly Series Jul 2026 #2"


def test_recurring_games_skips_disabled_templates(be, mocker):
    from stocks import GameLogic

    owner_id = 508
    be.add_user(owner_id, "testing")
    be.add_game_template(
        user_id=owner_id,
        name="stopped-template",
        start_date="2026-07-31",
        create_days_in_advance=1,
        recurring_period=1,
    )
    template = be.get_many_game_templates(status="enabled")[0]
    be.update_game_template(template_id=template.id, status="disabled")

    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 7, 30))
    logic.recurring_games()

    with pytest.raises(LookupError):
        be.get_many_games(owner_id=owner_id, include_private=True)


def test_remove_game_template_clears_links(be, mocker):
    from stocks import GameLogic

    owner_id = 509
    be.add_user(owner_id, "testing")
    be.add_game_template(
        user_id=owner_id,
        name="to-delete",
        start_date="2026-07-31",
        create_days_in_advance=1,
        recurring_period=1,
    )
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 7, 30))
    logic.recurring_games()

    template = be.get_many_game_templates(status="enabled")[0]
    game = be.get_many_games(owner_id=owner_id, include_private=True)[0]
    assert game.template_id == template.id

    be.remove_game_template(template.id)
    with pytest.raises(LookupError):
        be.get_game_template(template.id)
    refreshed = be.get_game(game.id)
    assert refreshed.template_id is None


def test_recurring_games_catches_up_multiple_due(be, mocker):
    from stocks import GameLogic

    owner_id = 510
    be.add_user(owner_id, "testing")
    be.add_game_template(
        user_id=owner_id,
        name="catchup",
        start_date="2026-01-31",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 3, 31))
    logic.recurring_games()

    games = be.get_many_games(owner_id=owner_id, include_private=True, include_ended=True)
    starts = sorted(game.start_date for game in games)
    assert starts == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    names = sorted(game.name for game in games)
    assert names == ["catchup Feb 2026", "catchup Jan 2026", "catchup Mar 2026"]
