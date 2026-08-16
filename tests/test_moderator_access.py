"""Moderator guild scoping and privileged-command gates."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord


def _member_interaction(*, guild_id: int, admin: bool = False, user_id: int = 99):
    interaction = MagicMock()
    guild = MagicMock()
    guild.id = guild_id
    interaction.guild = guild
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.roles = []
    perms = MagicMock()
    perms.administrator = admin
    member.guild_permissions = perms
    interaction.user = member
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_admin_in_foreign_guild_is_not_moderator():
    import discord_bot as db

    interaction = _member_interaction(guild_id=999999999999, admin=True)
    assert db.is_moderator(interaction) is False


def test_admin_in_home_guild_is_moderator():
    import discord_bot as db

    interaction = _member_interaction(guild_id=db.HOME_GUILD_ID, admin=True)
    assert db.is_moderator(interaction) is True


def test_bot_owner_is_moderator_in_any_guild():
    import discord_bot as db

    interaction = _member_interaction(guild_id=999999999999, admin=False, user_id=db.OWNER_ID)
    assert db.is_moderator(interaction) is True


def test_delete_game_denied_for_foreign_admin(mocker):
    import discord_bot as db

    interaction = _member_interaction(guild_id=999999999999, admin=True, user_id=50)
    game = SimpleNamespace(name="Public", owner_id=999)
    mocker.patch.object(db.fe, "game_info", return_value=SimpleNamespace(game=game))

    asyncio.run(db.delete_game.callback(interaction, "GAME1"))

    interaction.response.send_message.assert_awaited_once()
    assert "Not Allowed" in interaction.response.send_message.await_args.kwargs["embed"].title


def test_update_denied_for_foreign_admin():
    import discord_bot as db

    interaction = _member_interaction(guild_id=999999999999, admin=True)
    asyncio.run(db.update.callback(interaction))

    interaction.response.send_message.assert_awaited_once()
    assert "Not Allowed" in interaction.response.send_message.await_args.kwargs["embed"].title


def test_manage_recurring_games_shows_all_templates_for_home_admin(fe, mocker):
    import discord_bot as db

    mocker.patch.object(db, "fe", fe)
    fe.register(10)
    fe.register(20)
    fe.be.add_game_template(
        user_id=10,
        name="owner-a",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    fe.be.add_game_template(
        user_id=20,
        name="owner-b",
        start_date="2026-02-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )

    interaction = _member_interaction(guild_id=db.HOME_GUILD_ID, admin=True, user_id=50)
    mocker.patch.object(db, "is_moderator", return_value=True)
    mocker.patch.object(
        db,
        "RecurringTemplateManager",
        side_effect=lambda _interaction, templates: SimpleNamespace(
            build_embed=lambda: discord.Embed(title=f"count={len(templates)}")
        ),
    )

    asyncio.run(db.manage_recurring_games.callback(interaction))

    manager = db.RecurringTemplateManager.call_args.args[1]
    assert len(manager) == 2


def test_manage_recurring_games_shows_only_owned_for_regular_user(fe, mocker):
    import discord_bot as db

    mocker.patch.object(db, "fe", fe)
    fe.register(10)
    fe.register(20)
    fe.be.add_game_template(
        user_id=10,
        name="mine",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    fe.be.add_game_template(
        user_id=20,
        name="theirs",
        start_date="2026-02-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )

    interaction = _member_interaction(guild_id=db.HOME_GUILD_ID, admin=False, user_id=10)
    mocker.patch.object(db, "is_moderator", return_value=False)
    mocker.patch.object(
        db,
        "RecurringTemplateManager",
        side_effect=lambda _interaction, templates: SimpleNamespace(
            build_embed=lambda: discord.Embed(title=f"count={len(templates)}")
        ),
    )

    asyncio.run(db.manage_recurring_games.callback(interaction))

    manager = db.RecurringTemplateManager.call_args.args[1]
    assert len(manager) == 1
    assert manager[0].owner_id == 10
