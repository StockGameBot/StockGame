"""UI-level tests for /manage-recurring-games (RecurringTemplateManager)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


def _button_labels(view) -> list[str]:
    return [item.label for item in view.children if getattr(item, "label", None)]


def _mock_interaction(*, user_id: int = 10):
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=user_id)
    interaction.response = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.fixture
def db_mod(fe, mocker):
    """Import discord_bot with Frontend pointed at the temp test DB."""
    import discord_bot as db

    mocker.patch.object(db, "fe", fe)
    return db


def _two_templates(fe, owner_id: int = 10):
    fe.register(owner_id)
    fe.be.add_game_template(
        user_id=owner_id,
        name="mgr-one",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    fe.be.add_game_template(
        user_id=owner_id,
        name="mgr-two",
        start_date="2026-02-01",
        create_days_in_advance=0,
        recurring_period=1,
        game_length=1,
    )
    templates = [t for t in fe.be.get_many_game_templates(status=None) if t.owner_id == owner_id]
    templates.sort(key=lambda t: t.id)
    return templates


def _run_view_test(coro_factory):
    """discord.ui.View needs a running loop during construction."""
    return asyncio.run(coro_factory())


def test_stop_flips_status_and_shows_resume(db_mod, fe):
    templates = _two_templates(fe)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        assert "Stop" in _button_labels(view)
        click = _mock_interaction()
        await view._stop(click)
        assert fe.be.get_game_template(templates[0].id).status == "disabled"
        assert view.templates[0].status == "disabled"
        assert "Resume" in _button_labels(view)
        assert "Stop" not in _button_labels(view)
        click.response.edit_message.assert_awaited()
        click.followup.send.assert_awaited()
        assert "stopped" in click.followup.send.await_args.args[0].lower()

    _run_view_test(body)


def test_resume_flips_status_and_shows_stop(db_mod, fe):
    templates = _two_templates(fe)
    fe.be.update_game_template(template_id=templates[0].id, status="disabled")
    templates[0] = fe.be.get_game_template(templates[0].id)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        assert "Resume" in _button_labels(view)
        click = _mock_interaction()
        await view._resume(click)
        assert fe.be.get_game_template(templates[0].id).status == "enabled"
        assert view.templates[0].status == "enabled"
        assert "Stop" in _button_labels(view)
        assert "Resume" not in _button_labels(view)
        assert "resumed" in click.followup.send.await_args.args[0].lower()

    _run_view_test(body)


def test_stop_resume_round_trip_on_same_view(db_mod, fe):
    templates = _two_templates(fe)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        await view._stop(_mock_interaction())
        assert fe.be.get_game_template(templates[0].id).status == "disabled"
        await view._resume(_mock_interaction())
        assert fe.be.get_game_template(templates[0].id).status == "enabled"
        assert "Stop" in _button_labels(view)

    _run_view_test(body)


def test_pagination_and_stop_only_affects_current_template(db_mod, fe):
    templates = _two_templates(fe)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        assert view.index == 0
        await view._next(_mock_interaction())
        assert view.index == 1
        await view._stop(_mock_interaction())
        assert fe.be.get_game_template(templates[1].id).status == "disabled"
        assert fe.be.get_game_template(templates[0].id).status == "enabled"
        await view._previous(_mock_interaction())
        assert view.index == 0
        assert "Stop" in _button_labels(view)

    _run_view_test(body)


def test_interaction_check_rejects_other_users(db_mod, fe):
    templates = _two_templates(fe)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(user_id=10), templates)
        other = _mock_interaction(user_id=99)
        assert await view.interaction_check(other) is False
        other.response.send_message.assert_awaited()

    _run_view_test(body)


def test_delete_confirm_removes_template_and_advances(db_mod, fe):
    templates = _two_templates(fe)
    first_id, second_id = templates[0].id, templates[1].id

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        await view._ask_delete(_mock_interaction())
        assert view.confirming_delete is True
        assert "Confirm Delete" in _button_labels(view)
        await view._confirm_delete(_mock_interaction())
        with pytest.raises(LookupError):
            fe.be.get_game_template(first_id)
        assert len(view.templates) == 1
        assert view.templates[0].id == second_id
        assert view.index == 0
        assert view.confirming_delete is False

    _run_view_test(body)


def test_cancel_delete_advances_without_deleting(db_mod, fe):
    templates = _two_templates(fe)
    first_id = templates[0].id

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        await view._ask_delete(_mock_interaction())
        await view._cancel_delete(_mock_interaction())
        assert fe.be.get_game_template(first_id).status == "enabled"
        assert len(view.templates) == 2
        assert view.index == 1

    _run_view_test(body)


def test_delete_last_template_clears_view(db_mod, fe):
    fe.register(10)
    fe.be.add_game_template(
        user_id=10,
        name="only-one",
        start_date="2026-01-01",
        create_days_in_advance=0,
        recurring_period=1,
    )
    templates = [t for t in fe.be.get_many_game_templates(status=None) if t.owner_id == 10]

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        await view._ask_delete(_mock_interaction())
        click = _mock_interaction()
        await view._confirm_delete(click)
        assert view.templates == []
        embed = view.build_embed()
        assert "No recurring templates left" in embed.description
        kwargs = click.response.edit_message.await_args.kwargs
        assert kwargs.get("view") is None

    _run_view_test(body)


def test_stop_failure_sends_error(db_mod, fe, mocker):
    templates = _two_templates(fe)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        mocker.patch.object(
            db_mod.fe.be, "update_game_template", side_effect=RuntimeError("db down")
        )
        click = _mock_interaction()
        await view._stop(click)
        click.response.send_message.assert_awaited()
        assert "Failed to stop" in click.response.send_message.await_args.args[0]

    _run_view_test(body)


def test_build_embed_shows_stopped_status(db_mod, fe):
    templates = _two_templates(fe)
    fe.be.update_game_template(template_id=templates[0].id, status="disabled")
    templates[0] = fe.be.get_game_template(templates[0].id)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        embed = view.build_embed()
        assert "Stopped" in embed.description
        assert embed.color == db_mod.discord.Color.dark_grey()


def test_enable_auto_roles_updates_template(db_mod, fe):
    templates = _two_templates(fe)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        assert "Enable Auto Roles" in _button_labels(view)
        click = _mock_interaction()
        await view._enable_auto_roles(click)
        assert fe.be.get_game_template(templates[0].id).auto_top_roles is True
        assert "Disable Auto Roles" in _button_labels(view)

    _run_view_test(body)


def test_disable_auto_roles_strips_and_updates(db_mod, fe, mocker):
    templates = _two_templates(fe)
    fe.be.update_game_template(template_id=templates[0].id, auto_top_roles=True)
    templates[0] = fe.be.get_game_template(templates[0].id)
    strip_mock = mocker.patch.object(db_mod, "strip_template_top_roles", new_callable=AsyncMock)

    async def body():
        view = db_mod.RecurringTemplateManager(_mock_interaction(), templates)
        click = _mock_interaction()
        await view._disable_auto_roles(click)
        strip_mock.assert_awaited_once()
        assert fe.be.get_game_template(templates[0].id).auto_top_roles is False

    _run_view_test(body)


def _app_command_channel(*, channel_id: int = 4242, cached: bool = True):
    """Mimic discord.ui.ChannelSelect values (AppCommandChannel, not a real channel)."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    selected = MagicMock()
    selected.id = channel_id
    selected.resolve = MagicMock(return_value=channel if cached else None)
    selected.fetch = AsyncMock(return_value=channel)
    return selected, channel


def _select_with_values(mocker, view, selected):
    mocker.patch.object(
        type(view.select),
        "values",
        new_callable=mocker.PropertyMock,
        return_value=[selected],
    )


def test_push_channel_select_saves_cached_text_channel(db_mod, fe, mocker):
    template = _two_templates(fe)[0]
    selected, _channel = _app_command_channel(channel_id=4242)

    async def body():
        view = db_mod.LeaderboardChannelSelect(template.id)
        _select_with_values(mocker, view, selected)
        await view._on_select(_mock_interaction())

    _run_view_test(body)

    updated = fe.be.get_game_template(template.id)
    assert updated.push_leaderboard is True
    assert updated.leaderboard_channel_id == "4242"


def test_push_channel_select_fetches_uncached_channel(db_mod, fe, mocker):
    template = _two_templates(fe)[0]
    selected, _channel = _app_command_channel(channel_id=777, cached=False)

    async def body():
        view = db_mod.LeaderboardChannelSelect(template.id)
        _select_with_values(mocker, view, selected)
        await view._on_select(_mock_interaction())

    _run_view_test(body)

    selected.fetch.assert_awaited_once()
    assert fe.be.get_game_template(template.id).leaderboard_channel_id == "777"


def test_push_channel_select_leaves_push_off_when_channel_unavailable(db_mod, fe, mocker):
    template = _two_templates(fe)[0]
    selected = MagicMock()
    selected.id = 5150
    selected.resolve = MagicMock(return_value=None)
    selected.fetch = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "no access"))

    async def body():
        view = db_mod.LeaderboardChannelSelect(template.id)
        _select_with_values(mocker, view, selected)
        await view._on_select(_mock_interaction())

    _run_view_test(body)

    updated = fe.be.get_game_template(template.id)
    assert updated.push_leaderboard is False
    assert updated.leaderboard_channel_id is None


def test_owner_filter_excludes_others_templates(fe):
    """Same filter the slash command uses before opening the manager."""
    fe.register(10)
    fe.register(20)
    fe.be.add_game_template(user_id=10, name="mine", start_date="2026-01-01")
    fe.be.add_game_template(user_id=20, name="theirs", start_date="2026-01-01")
    all_tpls = list(fe.be.get_many_game_templates(status=None))
    mine = [t for t in all_tpls if t.owner_id == 10]
    assert {t.name for t in mine} == {"mine"}
