"""Recurring auto top-3 role tracking and Discord sync."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from helpers.recurring_top_roles import (
    HOME_GUILD_ID,
    TOP_ROLE_IDS,
    strip_template_top_roles,
    sync_recurring_top_roles,
)

def _recurring_template(fe, owner_id: int = 10, *, auto_top_roles: bool = False):
    fe.register(owner_id)
    template_id = fe.be.add_game_template(
        user_id=owner_id,
        name="roles-series",
        start_date="2099-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    if auto_top_roles:
        fe.be.update_game_template(template_id=template_id, auto_top_roles=True)
    return fe.be.get_game_template(template_id)


def _spawn_game(
    fe,
    owner_id: int,
    template_id: int,
    *,
    end_date: str = "2099-01-31",
    name_suffix: str = "",
):
    game_id = fe.be.add_game(
        user_id=owner_id,
        name=f"roles-game-{template_id}{name_suffix}"[:35],
        start_date="2099-01-01",
        end_date=end_date,
        template_id=template_id,
    )
    fe.be.update_game(game_id=game_id, status="active")
    return fe.be.get_game(game_id)


def _holder_game(fe, owner_id: int, template_id: int, *, suffix: str = "10"):
    fe.register(101, username="holder1")
    fe.register(102, username="holder2")
    fe.register(201, username="holder3")
    fe.register(202, username="holder4")
    fe.register(203, username="holder5")
    return _spawn_game(
        fe,
        owner_id,
        template_id,
        end_date=f"2099-02-{suffix}",
        name_suffix=f"-{suffix}",
    )


def test_replace_template_role_holders(fe):
    tpl = _recurring_template(fe)
    game_a = _holder_game(fe, 10, tpl.id, suffix="10")
    game_b = _holder_game(fe, 10, tpl.id, suffix="11")
    fe.be.replace_template_role_holders(
        tpl.id,
        game_id=game_a.id,
        ranked_user_ids=[101, 102],
    )
    holders = fe.be.get_template_role_holders(tpl.id)
    assert len(holders) == 2
    assert holders[0].rank == 1 and holders[0].user_id == 101
    assert holders[1].rank == 2 and holders[1].user_id == 102

    fe.be.replace_template_role_holders(
        tpl.id,
        game_id=game_b.id,
        ranked_user_ids=[201, 202, 203],
    )
    holders = fe.be.get_template_role_holders(tpl.id)
    assert len(holders) == 3
    assert {h.user_id for h in holders} == {201, 202, 203}


def test_clear_template_role_holders(fe):
    tpl = _recurring_template(fe)
    game = _holder_game(fe, 10, tpl.id)
    fe.be.replace_template_role_holders(
        tpl.id,
        game_id=game.id,
        ranked_user_ids=[101],
    )
    fe.be.clear_template_role_holders(tpl.id)
    assert fe.be.get_template_role_holders(tpl.id) == ()


def test_get_games_pending_top_roles(fe):
    tpl = _recurring_template(fe, auto_top_roles=True)
    game = _spawn_game(fe, 10, tpl.id)
    fe.be.update_game(game_id=game.id, status="ended")
    pending = fe.be.get_games_pending_top_roles()
    assert any(g.id == game.id for g in pending)

    fe.be.update_game(game_id=game.id, top_roles_applied=True)
    pending = fe.be.get_games_pending_top_roles()
    assert not any(g.id == game.id for g in pending)


def _mock_guild(*, member_ids: set[int]):
    guild = MagicMock()
    guild.id = HOME_GUILD_ID

    roles = {}
    for rank, role_id in TOP_ROLE_IDS.items():
        role = MagicMock()
        role.id = role_id
        role.__ge__ = lambda self, other, r=role: False
        roles[rank] = role
    guild.get_role.side_effect = lambda rid: next(
        (r for r in roles.values() if r.id == rid),
        None,
    )

    members = {}
    for uid in member_ids:
        member = MagicMock()
        member.id = uid
        member.roles = []
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()
        members[uid] = member

    guild.get_member.side_effect = lambda uid: members.get(uid)
    guild.fetch_member = AsyncMock(side_effect=lambda uid: members[uid])

    me = MagicMock()
    me.guild_permissions.manage_roles = True
    me.top_role = MagicMock()
    guild.me = me

    return guild, members, roles


def test_sync_recurring_top_roles_assigns_and_tracks(fe):
    tpl = _recurring_template(fe, auto_top_roles=True)
    game = _spawn_game(fe, 10, tpl.id)
    fe.register(501, username="p1")
    fe.register(502, username="p2")
    fe.join_game(user_id=501, game_id=game.id)
    fe.join_game(user_id=502, game_id=game.id)
    fe.be.update_participant(
        fe.be.get_many_participants(game_id=game.id, user_id=501)[0].id,
        current_value=2000.0,
    )
    fe.be.update_participant(
        fe.be.get_many_participants(game_id=game.id, user_id=502)[0].id,
        current_value=1500.0,
    )
    fe.be.update_game(game_id=game.id, status="ended")

    guild, members, roles = _mock_guild(member_ids={501, 502})
    bot = MagicMock()
    bot.get_guild.return_value = guild

    asyncio.run(sync_recurring_top_roles(bot, fe))

    assert fe.be.get_game(game.id).top_roles_applied is True
    holders = fe.be.get_template_role_holders(tpl.id)
    assert len(holders) == 2
    assert holders[0].user_id == 501
    members[501].add_roles.assert_awaited()
    members[502].add_roles.assert_awaited()


def test_sync_skips_when_auto_top_roles_disabled(fe):
    tpl = _recurring_template(fe, auto_top_roles=False)
    game = _spawn_game(fe, 10, tpl.id)
    fe.be.update_game(game_id=game.id, status="ended")

    guild, _members, _roles = _mock_guild(member_ids=set())
    bot = MagicMock()
    bot.get_guild.return_value = guild

    asyncio.run(sync_recurring_top_roles(bot, fe))

    assert fe.be.get_game(game.id).top_roles_applied is True
    assert fe.be.get_template_role_holders(tpl.id) == ()


def test_strip_template_top_roles_clears_db(fe):
    tpl = _recurring_template(fe, auto_top_roles=True)
    fe.register(601, username="strip1")
    fe.register(602, username="strip2")
    fe.register(603, username="strip3")
    game = _spawn_game(fe, 10, tpl.id)
    fe.be.replace_template_role_holders(
        tpl.id,
        game_id=game.id,
        ranked_user_ids=[601, 602, 603],
    )
    guild, members, roles = _mock_guild(member_ids={601, 602, 603})
    for uid in (601, 602, 603):
        members[uid].roles.append(roles[1])

    bot = MagicMock()
    bot.get_guild.return_value = guild

    asyncio.run(strip_template_top_roles(bot, fe, tpl.id))

    assert fe.be.get_template_role_holders(tpl.id) == ()
    members[601].remove_roles.assert_awaited()


def test_migrate_0_2_2_to_0_2_3_backfills_ended_games(db_path):
    from db_schema import create, _set_db_version, MIGRATIONS

    create(db_path, upgrade=False)
    _set_db_version(db_path, "0.2.2")
    sql = __import__("helpers.sqlhelper", fromlist=["SqlHelper"]).SqlHelper(db_path)
    sql.insert(
        "users",
        {
            "user_id": 1,
            "source": "testing",
            "datetime_created": "2025-01-01 00:00:00",
        },
    )
    sql.insert(
        "game_templates",
        {
            "template_name": "t",
            "game_name": "ended-backfill",
            "status": "enabled",
            "owner_user_id": 1,
            "start_money": 10000,
            "pick_count": 10,
            "start_date": "2099-01-01",
            "create_days_in_advance": 0,
            "recurring_period": 1,
            "game_length": 1,
            "datetime_created": "2025-01-01 00:00:00",
        },
    )
    sql.insert(
        "games",
        {
            "game_id": "ended1",
            "template_id": 1,
            "name": "ended-backfill",
            "owner_user_id": 1,
            "start_money": 10000,
            "pick_count": 10,
            "start_date": "2099-01-01",
            "status": "ended",
            "update_frequency": "alpaca",
            "datetime_created": "2025-01-01 00:00:00",
        },
    )

    MIGRATIONS[("0.2.2", "0.2.3")](db_path)

    row = sql.get("games", filters={"game_id": "ended1"})
    assert row.result[0]["top_roles_applied"] == 1
