"""Private-game invite, kick, ownership helpers, and access gating."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers.exceptions import NotAllowedError
from stocks import Frontend


def test_join_private_force_active_skips_pending(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="InvitePrivate",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(40)
    fe.join_game(user_id=40, game_id=game_id, force_active=True)
    participant = fe.be.get_many_participants(game_id=game_id, user_id=40)[0]
    assert participant.status == "active"


def test_join_private_force_active_upgrades_pending(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="UpgradePending",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(41)
    fe.join_game(user_id=41, game_id=game_id)
    assert fe.be.get_many_participants(game_id=game_id, user_id=41)[0].status == "pending"
    fe.join_game(user_id=41, game_id=game_id, force_active=True)
    assert fe.be.get_many_participants(game_id=game_id, user_id=41)[0].status == "active"


def test_kick_player_private_only(fe: Frontend):
    public_id = fe.new_game(user_id=10, name="PublicKick", start_date="2099-01-01")
    fe.register(50)
    fe.join_game(user_id=50, game_id=public_id)
    with pytest.raises(NotAllowedError) as exc:
        fe.kick_player(user_id=10, game_id=public_id, target_user_id=50)
    assert exc.value.reason == "Not private"


def test_kick_player_success_and_blocks_owner(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="KickPrivate",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(51)
    fe.join_game(user_id=51, game_id=game_id, force_active=True)
    fe.kick_player(user_id=10, game_id=game_id, target_user_id=51)
    with pytest.raises(LookupError):
        fe.be.get_many_participants(game_id=game_id, user_id=51)

    with pytest.raises(PermissionError, match="owner"):
        fe.kick_player(user_id=10, game_id=game_id, target_user_id=10)


def test_kick_player_blocked_when_ended(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="EndedKick",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(52)
    fe.join_game(user_id=52, game_id=game_id, force_active=True)
    fe.be.update_game(game_id, status="ended")
    with pytest.raises(NotAllowedError) as exc:
        fe.kick_player(user_id=10, game_id=game_id, target_user_id=52)
    assert exc.value.reason == "Game ended"


def test_kick_player_non_owner_denied(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="KickDenied",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(53)
    fe.register(54)
    fe.join_game(user_id=53, game_id=game_id, force_active=True)
    with pytest.raises(PermissionError):
        fe.kick_player(user_id=54, game_id=game_id, target_user_id=53)


def test_user_owns_any_game_flags(fe: Frontend):
    assert fe.user_owns_any_game(10) == (False, False)
    fe.new_game(user_id=10, name="PublicOwn", start_date="2099-01-01")
    assert fe.user_owns_any_game(10) == (True, False)
    fe.new_game(
        user_id=10,
        name="PrivateOwn",
        start_date="2099-02-01",
        private_game=True,
    )
    assert fe.user_owns_any_game(10) == (True, True)


def test_user_can_view_game_info_private_gating(mocker):
    import discord_bot as db

    private = SimpleNamespace(id="P1", private_game=True, owner_id=10)

    mocker.patch.object(
        db,
        "fe",
        SimpleNamespace(
            be=SimpleNamespace(
                get_many_participants=lambda **_kwargs: (_ for _ in ()).throw(
                    LookupError()
                )
            )
        ),
    )
    assert db._user_can_view_game_info(private, 10)
    assert not db._user_can_view_game_info(private, 99)

    mocker.patch.object(
        db,
        "fe",
        SimpleNamespace(
            be=SimpleNamespace(
                get_many_participants=lambda **_kwargs: (
                    SimpleNamespace(status="pending"),
                )
            )
        ),
    )
    assert not db._user_can_view_game_info(private, 99)

    mocker.patch.object(
        db,
        "fe",
        SimpleNamespace(
            be=SimpleNamespace(
                get_many_participants=lambda **_kwargs: (
                    SimpleNamespace(status="active"),
                )
            )
        ),
    )
    assert db._user_can_view_game_info(private, 99)


def test_format_listed_game_private_lock_emoji():
    import discord_bot as db

    game = SimpleNamespace(
        name="Secret",
        id="G1",
        owner_id=10,
        pick_date=None,
        end_date=None,
        pick_count=5,
        start_date="2099-01-01",
        template_id=None,
        private_game=True,
    )
    title, _body = db._format_listed_game(game, 2)
    assert "🔒" in title
    assert "[G1]" in title


def test_user_can_view_portfolio_public_game_allows_other_players(mocker):
    import discord_bot as db

    public = SimpleNamespace(id="PUB1", private_game=False, owner_id=10)
    mocker.patch.object(
        db,
        "fe",
        SimpleNamespace(
            be=SimpleNamespace(
                get_many_participants=lambda **_kwargs: (
                    SimpleNamespace(status="active"),
                )
            )
        ),
    )
    allowed, reason = db._user_can_view_portfolio(public, viewer_user_id=99, subject_user_id=20)
    assert allowed
    assert reason is None


def test_user_can_view_portfolio_private_game_blocks_other_players():
    import discord_bot as db

    private = SimpleNamespace(id="PRIV", private_game=True, owner_id=10)
    allowed, reason = db._user_can_view_portfolio(
        private,
        viewer_user_id=99,
        subject_user_id=20,
    )
    assert not allowed
    assert reason is not None
    assert "public games" in reason.lower()


def test_user_can_view_portfolio_self_always_allowed():
    import discord_bot as db

    private = SimpleNamespace(id="PRIV", private_game=True, owner_id=10)
    allowed, reason = db._user_can_view_portfolio(
        private,
        viewer_user_id=10,
        subject_user_id=10,
    )
    assert allowed
    assert reason is None
