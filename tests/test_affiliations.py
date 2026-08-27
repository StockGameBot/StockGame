"""Tests for recurring hedge-fund affiliations."""

from __future__ import annotations

from helpers.affiliations import (
    AFFILIATION_ATRIOC,
    AFFILIATION_DOUGDOUG,
    INDEPENDENT_KEY,
    aggregate_affiliation_stats,
    format_dollar_gain,
    format_hedge_fund_block,
    normalize_affiliation,
)
from helpers.affiliation_performance_image import create_affiliation_performance_image
from helpers.leaderboard_push import AFFILIATION_IMAGE_FILENAME, build_push_embed
from helpers.recurring_leaderboard_image import RecurringLeaderboardImageGenerator
from types import SimpleNamespace


def _recurring_template(fe, owner_id: int = 10, *, affiliations_enabled: bool = False):
    template_id = fe.be.add_game_template(
        user_id=owner_id,
        name=f"aff-tpl-{owner_id}",
        start_date="2026-01-01",
        starting_money=10_000,
        total_picks=10,
    )
    if affiliations_enabled:
        fe.be.update_game_template(template_id=template_id, affiliations_enabled=True)
    return fe.be.get_game_template(template_id)


def _spawn_game(fe, owner_id: int, template_id: int, *, name: str = "aff-game"):
    game_id = fe.be.add_game(
        user_id=owner_id,
        name=name,
        start_date="2026-01-01",
        starting_money=10_000,
        total_picks=10,
        template_id=template_id,
    )
    fe.be.update_game(game_id, status="active")
    return fe.be.get_game(game_id)


def test_normalize_affiliation():
    assert normalize_affiliation(None) is None
    assert normalize_affiliation("none") is None
    assert normalize_affiliation("atrioc") == AFFILIATION_ATRIOC


def test_aggregate_affiliation_stats_groups_players():
    participants = [
        SimpleNamespace(
            status="active",
            affiliation=AFFILIATION_ATRIOC,
            current_value=11_000.0,
        ),
        SimpleNamespace(
            status="active",
            affiliation=AFFILIATION_DOUGDOUG,
            current_value=9_000.0,
        ),
        SimpleNamespace(status="active", affiliation=None, current_value=10_500.0),
    ]
    stats = aggregate_affiliation_stats(participants, 10_000.0)
    assert stats[AFFILIATION_ATRIOC]["dollars"] == 1_000.0
    assert stats[AFFILIATION_DOUGDOUG]["dollars"] == -1_000.0
    assert stats[INDEPENDENT_KEY]["dollars"] == 500.0
    assert stats[AFFILIATION_ATRIOC]["members"] == 1
    assert stats[INDEPENDENT_KEY]["members"] == 1


def test_format_dollar_gain_sign_before_currency():
    assert format_dollar_gain(12.5) == "+$12.50"
    assert format_dollar_gain(-3.0) == "-$3.00"
    assert format_dollar_gain(0) == "+$0.00"


def test_set_participant_affiliation_locks_choice(fe):
    owner_id = 801
    fe.register(owner_id)
    tpl = _recurring_template(fe, owner_id, affiliations_enabled=True)
    game = _spawn_game(fe, owner_id, tpl.id, name="aff-lock")
    player_id = 802
    fe.register(player_id)
    fe.join_game(player_id, game.id)

    fe.set_participant_affiliation(player_id, game.id, AFFILIATION_ATRIOC)
    participant = fe.be.get_many_participants(game_id=game.id, user_id=player_id)[0]
    assert participant.affiliation == AFFILIATION_ATRIOC

    try:
        fe.set_participant_affiliation(player_id, game.id, AFFILIATION_DOUGDOUG)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_set_participant_affiliation_requires_enabled_template(fe):
    owner_id = 803
    fe.register(owner_id)
    tpl = _recurring_template(fe, owner_id, affiliations_enabled=False)
    game = _spawn_game(fe, owner_id, tpl.id, name="aff-off")
    player_id = 804
    fe.register(player_id)
    fe.join_game(player_id, game.id)

    try:
        fe.set_participant_affiliation(player_id, game.id, AFFILIATION_ATRIOC)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_build_push_embed_uses_signed_dollars_and_stacked_picks():
    game = SimpleNamespace(
        name="Test Recurring",
        id="ABC12",
        change_dollars=100.0,
        change_percent=1.0,
        start_date=__import__("datetime").date(2026, 1, 1),
        end_date=None,
        status="active",
    )
    embed = build_push_embed(
        game,
        best_pick={"ticker": "AXON", "pct": 5.5, "company_name": "Axon Enterprise"},
        worst_pick={"ticker": "MMM", "pct": -2.0, "company_name": "3M Company"},
    )
    assert "+$100.00" in embed.description
    assert "The Atrioc Hedge Fund" not in (embed.description or "")
    fields = {field.name: field.value for field in embed.fields}
    assert "AXON - Axon Enterprise +5.50%" in fields["Best owned pick"]
    assert "MMM - 3M Company -2.00%" in fields["Worst owned pick"]
    assert "`" not in fields["Best owned pick"]


def test_affiliation_performance_image_renders_png():
    stats = aggregate_affiliation_stats([], 10_000.0)
    buf = create_affiliation_performance_image(
        overall_dollars=250.0,
        overall_percent=2.5,
        affiliation_stats=stats,
    )
    assert buf.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_push_pages_attaches_affiliation_table_image(mocker):
    from io import BytesIO

    import helpers.leaderboard_push as lp

    lp.clear_push_image_cache()
    generator = mocker.Mock()
    generator.create_image.return_value = BytesIO(b"png")
    mocker.patch.object(lp, "get_recurring_generator", return_value=generator)
    game = SimpleNamespace(
        name="Aff Game",
        id="G99",
        change_dollars=0.0,
        change_percent=0.0,
        start_date=__import__("datetime").date(2026, 1, 1),
        end_date=None,
        status="active",
    )
    stats = aggregate_affiliation_stats([], 10_000.0)
    embed, images, affiliation_image, _fp, _hit = lp.render_push_pages(
        game,
        [],
        [],
        affiliations_enabled=True,
        affiliation_stats=stats,
    )
    assert affiliation_image is not None
    assert embed.image.url == f"attachment://{AFFILIATION_IMAGE_FILENAME}"
    assert len(images) >= 1


def test_recurring_leaderboard_image_renders_with_affiliation_icon():
    generator = RecurringLeaderboardImageGenerator()
    game_data = {"name": "Aff Game", "id": "G1", "affiliations_enabled": True}
    players = [
        {
            "rank": 1,
            "user_id": 1,
            "display_name": "Player One",
            "affiliation": AFFILIATION_ATRIOC,
            "current_value": 10_000,
            "change_dollars": 0,
            "change_percent": 0,
            "days_in_first": 0,
            "picks": [],
        }
    ]
    buf = generator.create_image(game_data, players, target_n=1)
    assert buf.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_format_hedge_fund_block_uses_fixed_order():
    stats = aggregate_affiliation_stats([], 10_000.0)
    block = format_hedge_fund_block(stats)
    lines = block.splitlines()
    assert lines[0].startswith("The Atrioc Hedge Fund")
    assert lines[-1].startswith("The Independent Hedge Fund")
