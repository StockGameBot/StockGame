"""DM invite tracking, expiration copy, and join-via-invite behavior."""

from __future__ import annotations

from helpers.game_invites import build_expired_invite_embed
from stocks import Frontend


def test_build_expired_invite_embed_mentions_join_command():
    embed = build_expired_invite_embed("My League", "abc123")
    assert "abc123" in embed.description
    assert "/join-game" in embed.description
    assert "new invite" in embed.description.lower()


def test_record_and_get_pending_invite(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="InviteTrack",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(60)
    invite, previous = fe.record_game_invite(
        game_id=game_id,
        user_id=60,
        inviter_id=10,
        dm_channel_id=999,
        dm_message_id=111,
    )
    assert previous is None
    assert invite.status == "pending"
    assert invite.dm_message_id == 111

    pending = fe.get_pending_game_invite(60, game_id)
    assert pending is not None
    assert pending.inviter_id == 10


def test_reinvite_replaces_previous_message_ids(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="Reinvite",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(61)
    _first, _ = fe.record_game_invite(
        game_id=game_id,
        user_id=61,
        inviter_id=10,
        dm_channel_id=100,
        dm_message_id=1,
    )
    invite, previous = fe.record_game_invite(
        game_id=game_id,
        user_id=61,
        inviter_id=10,
        dm_channel_id=200,
        dm_message_id=2,
    )
    assert previous is not None
    assert previous.dm_message_id == 1
    assert invite.dm_message_id == 2
    assert invite.status == "pending"


def test_join_private_with_pending_invite_is_active(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="JoinViaInvite",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(62)
    fe.record_game_invite(
        game_id=game_id,
        user_id=62,
        inviter_id=10,
        dm_channel_id=None,
        dm_message_id=None,
    )
    assert fe.get_pending_game_invite(62, game_id) is not None
    fe.join_game(user_id=62, game_id=game_id, force_active=True)
    participant = fe.be.get_many_participants(game_id=game_id, user_id=62)[0]
    assert participant.status == "active"
    fe.finalize_game_invite(game_id=game_id, user_id=62, status="accepted")
    assert fe.get_pending_game_invite(62, game_id) is None


def test_join_private_without_invite_stays_pending(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="NoInviteJoin",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(63)
    fe.join_game(user_id=63, game_id=game_id)
    participant = fe.be.get_many_participants(game_id=game_id, user_id=63)[0]
    assert participant.status == "pending"


def test_list_pending_game_invites(fe: Frontend):
    game_id = fe.new_game(
        user_id=10,
        name="PendingList",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(64)
    fe.record_game_invite(
        game_id=game_id,
        user_id=64,
        inviter_id=10,
        dm_channel_id=1,
        dm_message_id=2,
    )
    invites = fe.list_pending_game_invites(64)
    assert len(invites) == 1
    assert invites[0].game_id == game_id
