from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord


def _fake_frontend(game_statuses: list[str]):
    participants = [
        SimpleNamespace(game_id=f"GAME{index}", status="active")
        for index, _status in enumerate(game_statuses)
    ]
    statuses = {
        participant.game_id: status
        for participant, status in zip(participants, game_statuses)
    }
    return SimpleNamespace(
        be=SimpleNamespace(
            get_many_participants=lambda **_kwargs: tuple(participants),
            get_game=lambda game_id: SimpleNamespace(status=statuses[game_id]),
        )
    )


def test_quick_start_is_shown_without_any_games(mocker):
    import discord_bot as db

    frontend = _fake_frontend([])
    frontend.be.get_many_participants = lambda **_kwargs: (_ for _ in ()).throw(
        LookupError()
    )
    mocker.patch.object(db, "fe", frontend)

    assert db._should_show_quick_start(10)


def test_quick_start_is_shown_without_current_game(mocker):
    import discord_bot as db

    mocker.patch.object(db, "fe", _fake_frontend(["ended"]))

    assert db._should_show_quick_start(10)


def test_quick_start_is_shown_without_ended_history(mocker):
    import discord_bot as db

    mocker.patch.object(db, "fe", _fake_frontend(["active"]))

    assert db._should_show_quick_start(10)


def test_regular_help_is_shown_with_current_and_ended_games(mocker):
    import discord_bot as db

    mocker.patch.object(db, "fe", _fake_frontend(["active", "ended"]))

    assert not db._should_show_quick_start(10)


def test_regular_help_base_omits_owner_and_moderator_commands():
    import discord_bot as db

    embed = db._regular_help_embed()
    content = "\n".join(str(field.value) for field in embed.fields)

    for command in ("/join-game", "/buy-stock", "/leaderboard", "/create-game"):
        assert command in content
    for command in (
        "/manage-game",
        "/invite",
        "/delete-game",
        "/manage-pending",
        "/kick-player",
        "/create-recurring-game",
        "/logs",
    ):
        assert command not in content


def test_regular_help_appends_owner_private_and_moderator_sections():
    import discord_bot as db

    embed = db._regular_help_embed(
        owns_game=True,
        owns_private_game=True,
        moderator=True,
    )
    content = "\n".join(str(field.value) for field in embed.fields)

    for command in (
        "/invite",
        "/manage-game",
        "/delete-game",
        "/manage-pending",
        "/kick-player",
        "/create-recurring-game",
        "/logs",
    ):
        assert command in content
    assert all(" - " in line for line in content.splitlines())


def test_advanced_button_replaces_quick_start_with_regular_help():
    import discord_bot as db

    async def run():
        view = db.QuickStartHelpView(
            10,
            owns_game=True,
            owns_private_game=False,
            moderator=False,
        )
        button = next(
            item
            for item in view.children
            if isinstance(item, discord.ui.Button) and item.label == "Advanced"
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )

        await button.callback(interaction)

        kwargs = interaction.response.edit_message.await_args.kwargs
        assert kwargs["embed"].title == "Stock Game Bot - Command Guide"
        assert "/manage-game" in "\n".join(
            str(field.value) for field in kwargs["embed"].fields
        )
        assert kwargs["view"] is None

    asyncio.run(run())
