"""Deep coverage of recurring template creation, scheduling, stop/resume, and errors."""

from datetime import date

import pytest

import helpers.exceptions as bexc


def _template(be, owner_id: int, **kwargs):
    defaults = dict(
        user_id=owner_id,
        name="default-template",
        start_date="2026-01-15",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
        pick_date=None,
        exclusive_picks=False,
    )
    defaults.update(kwargs)
    be.add_game_template(**defaults)
    return be.get_many_game_templates(status="enabled")[-1]


def _logic(be, mocker, today: date):
    from stocks import GameLogic

    logic = GameLogic(be.sql.db)
    mocker.patch.object(logic, "_today_et", return_value=today)
    return logic


def _spawned(be, owner_id: int, *, include_ended: bool = True):
    games = be.get_many_games(
        owner_id=owner_id, include_private=True, include_ended=include_ended
    )
    return [g for g in games if g.template_id is not None]


# ---------------------------------------------------------------------------
# A) First game starts on the template start_date
# ---------------------------------------------------------------------------


def test_first_game_opens_on_template_start_date_then_activates(be, mocker):
    """Game is created for the anchor start date and flips open→active that day."""
    owner_id = 701
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="anchor-start",
        start_date="2026-06-01",
        create_days_in_advance=3,
        recurring_period=1,
    )

    # Not due yet (due = Jun 1 - 3 = May 29)
    logic = _logic(be, mocker, date(2026, 5, 28))
    logic.recurring_games()
    with pytest.raises(LookupError):
        be.get_many_games(owner_id=owner_id, include_private=True)

    # Due day: create game still starting Jun 1
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 5, 29))
    logic.recurring_games()
    games = _spawned(be, owner_id)
    assert len(games) == 1
    assert games[0].start_date == date(2026, 6, 1)
    assert games[0].status == "open"
    assert games[0].template_id == tpl.id

    # Day before start: still open
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 5, 31))
    logic.update_game_statuses()
    assert be.get_game(games[0].id).status == "open"

    # Start date: becomes active
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 6, 1))
    logic.update_game_statuses()
    assert be.get_game(games[0].id).status == "active"


# ---------------------------------------------------------------------------
# B) Keeps spawning each period until stopped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period,anchor,checkpoints,expected_starts",
    [
        (
            1,
            "2026-01-15",
            [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)],
            [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)],
        ),
        (
            2,
            "2026-01-01",
            [date(2026, 1, 1), date(2026, 3, 1), date(2026, 5, 1)],
            [date(2026, 1, 1), date(2026, 3, 1), date(2026, 5, 1)],
        ),
        (
            3,
            "2026-01-31",
            [date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31)],
            [date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31)],
        ),
        (
            6,
            "2025-07-01",
            [date(2025, 7, 1), date(2026, 1, 1), date(2026, 7, 1)],
            [date(2025, 7, 1), date(2026, 1, 1), date(2026, 7, 1)],
        ),
        (
            12,
            "2024-03-01",
            [date(2024, 3, 1), date(2025, 3, 1), date(2026, 3, 1)],
            [date(2024, 3, 1), date(2025, 3, 1), date(2026, 3, 1)],
        ),
    ],
)
def test_recurring_keeps_starting_each_period(
    be, mocker, period, anchor, checkpoints, expected_starts
):
    owner_id = 710 + period
    be.add_user(owner_id, "testing")
    _template(
        be,
        owner_id,
        name=f"period-{period}",
        start_date=anchor,
        create_days_in_advance=0,
        recurring_period=period,
        game_length=min(period, 1) if period == 1 else 1,
    )

    logic = _logic(be, mocker, checkpoints[0])
    for today in checkpoints:
        mocker.patch.object(logic, "_today_et", return_value=today)
        logic.recurring_games()
        starts = sorted(g.start_date for g in _spawned(be, owner_id))
        due_count = sum(1 for s in expected_starts if s <= today)
        assert starts == expected_starts[:due_count]

    assert sorted(g.start_date for g in _spawned(be, owner_id)) == expected_starts


def test_stop_prevents_new_games_existing_continue_and_resume_works(be, mocker):
    owner_id = 720
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="stoppable",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )

    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    first = _spawned(be, owner_id)
    assert len(first) == 1
    assert first[0].status == "open"

    # Stop template before next period
    be.update_game_template(template_id=tpl.id, status="disabled")
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 2, 1))
    logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 1

    # Existing game still activates / ends via status updates
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 1, 1))
    logic.update_game_statuses()
    assert be.get_game(first[0].id).status == "active"

    mocker.patch.object(logic, "_today_et", return_value=date(2026, 2, 1))
    logic.update_game_statuses()
    assert be.get_game(first[0].id).status == "ended"

    # Resume: next due occurrence is created
    be.update_game_template(template_id=tpl.id, status="enabled")
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 2, 1))
    logic.recurring_games()
    starts = sorted(g.start_date for g in _spawned(be, owner_id, include_ended=True))
    assert starts == [date(2026, 1, 1), date(2026, 2, 1)]


def test_idempotent_recurring_call_does_not_duplicate(be, mocker):
    owner_id = 721
    be.add_user(owner_id, "testing")
    _template(
        be,
        owner_id,
        name="idempotent",
        start_date="2026-04-10",
        create_days_in_advance=0,
        recurring_period=1,
    )
    logic = _logic(be, mocker, date(2026, 4, 10))
    for _ in range(5):
        logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 1


def test_catchup_creates_all_overdue_then_stops_at_future(be, mocker):
    owner_id = 722
    be.add_user(owner_id, "testing")
    _template(
        be,
        owner_id,
        name="catchup-deep",
        start_date="2025-10-15",
        create_days_in_advance=0,
        recurring_period=2,
        game_length=1,
    )
    logic = _logic(be, mocker, date(2026, 4, 15))
    logic.recurring_games()
    starts = sorted(g.start_date for g in _spawned(be, owner_id))
    assert starts == [
        date(2025, 10, 15),
        date(2025, 12, 15),
        date(2026, 2, 15),
        date(2026, 4, 15),
    ]

    # Next period (Jun 15) is not due yet
    logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 4


def test_create_days_in_advance_gates_creation(be, mocker):
    owner_id = 723
    be.add_user(owner_id, "testing")
    _template(
        be,
        owner_id,
        name="advance-gate",
        start_date="2026-09-10",
        create_days_in_advance=5,
        recurring_period=1,
    )
    logic = _logic(be, mocker, date(2026, 9, 4))
    logic.recurring_games()
    with pytest.raises(LookupError):
        be.get_many_games(owner_id=owner_id, include_private=True)

    mocker.patch.object(logic, "_today_et", return_value=date(2026, 9, 5))
    logic.recurring_games()
    games = _spawned(be, owner_id)
    assert len(games) == 1
    assert games[0].start_date == date(2026, 9, 10)


def test_pick_date_offset_applied_per_occurrence(be, mocker):
    owner_id = 724
    be.add_user(owner_id, "testing")
    _template(
        be,
        owner_id,
        name="pick-offset",
        start_date="2026-01-20",
        create_days_in_advance=0,
        recurring_period=1,
        pick_date=5,  # 5 days before start
        exclusive_picks=True,
        game_length=1,
    )
    logic = _logic(be, mocker, date(2026, 2, 20))
    logic.recurring_games()
    games = sorted(_spawned(be, owner_id), key=lambda g: g.start_date)
    assert [g.start_date for g in games] == [date(2026, 1, 20), date(2026, 2, 20)]
    assert games[0].pick_date == date(2026, 1, 15)
    assert games[1].pick_date == date(2026, 2, 15)


def test_game_length_sets_end_date_and_status_ends(be, mocker):
    owner_id = 725
    be.add_user(owner_id, "testing")
    _template(
        be,
        owner_id,
        name="length-end",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=2,
        game_length=1,
    )
    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    game = _spawned(be, owner_id)[0]
    assert game.end_date == date(2026, 1, 31)

    mocker.patch.object(logic, "_today_et", return_value=date(2026, 1, 1))
    logic.update_game_statuses()
    assert be.get_game(game.id).status == "active"

    mocker.patch.object(logic, "_today_et", return_value=date(2026, 2, 1))
    logic.update_game_statuses()
    assert be.get_game(game.id).status == "ended"


def test_remove_template_stops_future_spawns(be, mocker):
    owner_id = 726
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="remove-me",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
    )
    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    game_id = _spawned(be, owner_id)[0].id
    be.remove_game_template(tpl.id)

    mocker.patch.object(logic, "_today_et", return_value=date(2026, 3, 1))
    logic.recurring_games()
    games = be.get_many_games(owner_id=owner_id, include_private=True, include_ended=True)
    assert len(games) == 1
    assert games[0].id == game_id
    assert games[0].template_id is None


# ---------------------------------------------------------------------------
# Validation / error surface
# ---------------------------------------------------------------------------


def test_duplicate_template_names_rejected(be):
    owner_id = 730
    be.add_user(owner_id, "testing")
    be.add_game_template(user_id=owner_id, name="same-name", start_date="2026-01-01")
    with pytest.raises(bexc.AlreadyExistsError, match="same name"):
        be.add_game_template(user_id=owner_id, name="same-name", start_date="2026-02-01")


def test_exclusive_picks_requires_pick_date(be):
    owner_id = 731
    be.add_user(owner_id, "testing")
    with pytest.raises(ValueError, match="pick_date.*required"):
        be.add_game_template(
            user_id=owner_id,
            name="exclusive-no-pick",
            start_date="2026-01-01",
            exclusive_picks=True,
            pick_date=None,
        )


def test_exclusive_picks_rejects_negative_pick_date(be):
    owner_id = 732
    be.add_user(owner_id, "testing")
    with pytest.raises(ValueError, match="cannot be after the game start"):
        be.add_game_template(
            user_id=owner_id,
            name="exclusive-late-picks",
            start_date="2026-01-01",
            exclusive_picks=True,
            pick_date=-1,
        )


def test_invalid_start_date_rejected(be):
    owner_id = 733
    be.add_user(owner_id, "testing")
    with pytest.raises(bexc.InvalidDateFormatError):
        be.add_game_template(
            user_id=owner_id,
            name="bad-start",
            start_date="01-15-2026",
        )
    with pytest.raises(bexc.InvalidDateFormatError):
        be.add_game_template(
            user_id=owner_id,
            name="bad-start-2",
            start_date="not-a-date",
        )


def test_empty_start_date_rejected(be):
    """Templates require a concrete YYYY-MM-DD start_date."""
    owner_id = 734
    be.add_user(owner_id, "testing")
    with pytest.raises(bexc.InvalidDateFormatError):
        be.add_game_template(
            user_id=owner_id,
            name="no-start",
            start_date="",
            exclusive_picks=True,
            pick_date=3,
        )
    with pytest.raises(bexc.InvalidDateFormatError):
        be.add_game_template(
            user_id=owner_id,
            name="none-start",
            start_date=None,  # type: ignore[arg-type]
        )


def test_recurring_period_and_advance_and_length_errors(be):
    owner_id = 735
    be.add_user(owner_id, "testing")

    with pytest.raises(ValueError, match="recurring_period"):
        be.add_game_template(
            user_id=owner_id, name="bad-period", start_date="2026-01-01", recurring_period=0
        )
    with pytest.raises(ValueError, match="create_days_in_advance"):
        be.add_game_template(
            user_id=owner_id,
            name="bad-advance",
            start_date="2026-01-01",
            create_days_in_advance=-1,
        )
    with pytest.raises(ValueError, match="game_length"):
        be.add_game_template(
            user_id=owner_id, name="bad-length-neg", start_date="2026-01-01", game_length=-1
        )
    with pytest.raises(ValueError, match="game_length.*recurring_period"):
        be.add_game_template(
            user_id=owner_id,
            name="bad-overlap",
            start_date="2026-01-01",
            recurring_period=1,
            game_length=2,
        )


def test_pick_date_out_of_range(be):
    owner_id = 736
    be.add_user(owner_id, "testing")
    with pytest.raises(ValueError, match="between -30 and 30"):
        be.add_game_template(
            user_id=owner_id, name="pick-hi", start_date="2026-01-01", pick_date=31
        )
    with pytest.raises(ValueError, match="between -30 and 30"):
        be.add_game_template(
            user_id=owner_id, name="pick-lo", start_date="2026-01-01", pick_date=-31
        )


def test_update_template_validation_errors(be):
    owner_id = 737
    be.add_user(owner_id, "testing")
    tpl = _template(be, owner_id, name="upd-val")

    with pytest.raises(ValueError, match="At least one"):
        be.update_game_template(template_id=tpl.id)
    with pytest.raises(ValueError, match="Invalid template status"):
        be.update_game_template(template_id=tpl.id, status="paused")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="recurring_period"):
        be.update_game_template(template_id=tpl.id, recurring_period=0)
    with pytest.raises(ValueError, match="create_days_in_advance"):
        be.update_game_template(template_id=tpl.id, create_days_in_advance=-2)
    with pytest.raises(ValueError, match="game_length"):
        be.update_game_template(template_id=tpl.id, game_length=-1)


def test_next_recurring_start_helper_edge_cases(be):
    from stocks import GameLogic

    logic = GameLogic(be.sql.db)
    anchor = date(2026, 1, 31)
    assert logic._next_recurring_start(anchor, 1) == anchor
    assert logic._next_recurring_start(anchor, 1, after=anchor) == date(2026, 2, 28)
    assert logic._next_recurring_start(anchor, 3, after=date(2026, 1, 31)) == date(2026, 4, 30)
    assert logic._next_recurring_start(date(2024, 2, 29), 12, after=date(2024, 2, 29)) == date(
        2025, 2, 28
    )


def test_unique_name_collision_chain(be, mocker):
    owner_id = 738
    be.add_user(owner_id, "testing")
    be.add_game(user_id=owner_id, name="series", start_date="2025-01-01")
    be.add_game(user_id=owner_id, name="series #2", start_date="2025-02-01")
    _template(
        be,
        owner_id,
        name="series",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
    )
    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    spawned = _spawned(be, owner_id)
    assert len(spawned) == 1
    assert spawned[0].name == "series Jan 2026"


# ---------------------------------------------------------------------------
# Stop / resume edge cases (manage-recurring-games behavior at Backend layer)
# ---------------------------------------------------------------------------


def test_stop_before_first_spawn_never_creates_until_resume(be, mocker):
    """Disabled from day one: no games until Resume, then catch-up applies."""
    owner_id = 740
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="pre-stopped",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    be.update_game_template(template_id=tpl.id, status="disabled")

    logic = _logic(be, mocker, date(2026, 3, 1))
    logic.recurring_games()
    with pytest.raises(LookupError):
        be.get_many_games(owner_id=owner_id, include_private=True)

    be.update_game_template(template_id=tpl.id, status="enabled")
    logic.recurring_games()
    starts = sorted(g.start_date for g in _spawned(be, owner_id, include_ended=True))
    assert starts == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_resume_after_multi_month_gap_catches_up_all_missed(be, mocker):
    """While stopped, periods are skipped; Resume creates every overdue start."""
    owner_id = 741
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="gap-catchup",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 1

    be.update_game_template(template_id=tpl.id, status="disabled")
    for day in (date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)):
        mocker.patch.object(logic, "_today_et", return_value=day)
        logic.recurring_games()
    assert len(_spawned(be, owner_id, include_ended=True)) == 1

    be.update_game_template(template_id=tpl.id, status="enabled")
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 4, 1))
    logic.recurring_games()
    starts = sorted(g.start_date for g in _spawned(be, owner_id, include_ended=True))
    assert starts == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
    ]


def test_resume_when_next_period_not_due_creates_nothing(be, mocker):
    owner_id = 742
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="not-due-yet",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    be.update_game_template(template_id=tpl.id, status="disabled")
    be.update_game_template(template_id=tpl.id, status="enabled")

    # Mid-month: Feb occurrence not due until Feb 1
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 1, 15))
    logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 1


def test_stop_and_resume_are_idempotent(be, mocker):
    owner_id = 743
    be.add_user(owner_id, "testing")
    tpl = _template(be, owner_id, name="idem-stop", start_date="2026-06-01")

    be.update_game_template(template_id=tpl.id, status="disabled")
    be.update_game_template(template_id=tpl.id, status="disabled")
    assert be.get_game_template(tpl.id).status == "disabled"

    be.update_game_template(template_id=tpl.id, status="enabled")
    be.update_game_template(template_id=tpl.id, status="enabled")
    assert be.get_game_template(tpl.id).status == "enabled"

    logic = _logic(be, mocker, date(2026, 6, 1))
    logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 1


def test_disabled_sibling_does_not_block_enabled_template(be, mocker):
    owner_id = 744
    be.add_user(owner_id, "testing")
    stopped = _template(
        be,
        owner_id,
        name="sibling-stopped",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
    )
    be.update_game_template(template_id=stopped.id, status="disabled")
    _template(
        be,
        owner_id,
        name="sibling-live",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
    )

    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    spawned = _spawned(be, owner_id)
    assert len(spawned) == 1
    assert spawned[0].name == "sibling-live Jan 2026"


def test_manage_listing_includes_enabled_and_disabled(be):
    """manage-recurring-games loads status=None so Stopped templates stay visible."""
    owner_id = 745
    be.add_user(owner_id, "testing")
    live = _template(be, owner_id, name="list-live", start_date="2026-01-01")
    stopped = _template(be, owner_id, name="list-stopped", start_date="2026-02-01")
    be.update_game_template(template_id=stopped.id, status="disabled")

    all_tpls = be.get_many_game_templates(status=None)
    by_id = {t.id: t for t in all_tpls}
    assert by_id[live.id].status == "enabled"
    assert by_id[stopped.id].status == "disabled"

    only_enabled = be.get_many_game_templates(status="enabled")
    assert all(t.status == "enabled" for t in only_enabled)
    assert stopped.id not in {t.id for t in only_enabled}


def test_stop_then_delete_keeps_existing_game_running(be, mocker):
    owner_id = 746
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="stop-then-delete",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    logic = _logic(be, mocker, date(2026, 1, 1))
    logic.recurring_games()
    game = _spawned(be, owner_id)[0]

    be.update_game_template(template_id=tpl.id, status="disabled")
    be.remove_game_template(tpl.id)

    refreshed = be.get_game(game.id)
    assert refreshed.template_id is None
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 1, 1))
    logic.update_game_statuses()
    assert be.get_game(game.id).status == "active"

    mocker.patch.object(logic, "_today_et", return_value=date(2026, 3, 1))
    logic.recurring_games()
    assert len(be.get_many_games(owner_id=owner_id, include_private=True, include_ended=True)) == 1


def test_resume_respects_create_days_in_advance_after_stop(be, mocker):
    owner_id = 747
    be.add_user(owner_id, "testing")
    tpl = _template(
        be,
        owner_id,
        name="advance-resume",
        start_date="2026-05-10",
        create_days_in_advance=5,
        recurring_period=1,
        game_length=1,
    )
    logic = _logic(be, mocker, date(2026, 5, 5))
    logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 1

    be.update_game_template(template_id=tpl.id, status="disabled")
    be.update_game_template(template_id=tpl.id, status="enabled")

    # June start is 2026-06-10; with 5 days advance, due on 2026-06-05
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 6, 4))
    logic.recurring_games()
    assert len(_spawned(be, owner_id)) == 1

    mocker.patch.object(logic, "_today_et", return_value=date(2026, 6, 5))
    logic.recurring_games()
    starts = sorted(g.start_date for g in _spawned(be, owner_id))
    assert starts == [date(2026, 5, 10), date(2026, 6, 10)]
