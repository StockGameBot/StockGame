from __future__ import annotations

import asyncio
from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord


def _rank_page(number: int, start: int, end: int) -> dict:
    return {
        "png": b"fake-png",
        "filename": f"page-{number}.png",
        "rank_start": start,
        "rank_end": end,
    }


def _interaction(user_id: int = 10):
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=user_id)
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def _buttons(view) -> dict[str, discord.ui.Button]:
    return {
        item.label: item
        for item in view.children
        if isinstance(item, discord.ui.Button) and item.label
    }


def test_leaderboard_view_pages_ranks_and_games():
    import discord_bot as db

    async def run():
        invoking = _interaction()
        games = [
            {
                "title": "One",
                "description": "First",
                "rank_page_count": 2,
                "rank_pages": {
                    0: _rank_page(1, 1, 15),
                    1: _rank_page(2, 16, 22),
                },
            },
            {
                "title": "Two",
                "description": "Second",
                "rank_page_count": 1,
                "rank_pages": {0: _rank_page(1, 1, 4)},
            },
        ]
        view = db.UserLeaderboardView(invoking, games)

        buttons = _buttons(view)
        assert list(buttons) == [
            "Previous game",
            "Previous page",
            "Next page",
            "Next game",
            "Share",
        ]
        assert buttons["Previous game"].disabled
        assert buttons["Previous page"].disabled
        assert not buttons["Next page"].disabled
        assert not buttons["Next game"].disabled

        click = _interaction()
        await view._next_rank_page(click)
        assert view.rank_page_index == 1
        buttons = _buttons(view)
        assert not buttons["Previous page"].disabled
        assert buttons["Next page"].disabled
        assert click.edit_original_response.await_args.kwargs["attachments"][0].filename == "page-2.png"

        click = _interaction()
        await view._next_game(click)
        assert view.game_index == 1
        assert view.rank_page_index == 0
        buttons = _buttons(view)
        assert not buttons["Previous game"].disabled
        assert buttons["Next game"].disabled
        assert buttons["Previous page"].disabled
        assert buttons["Next page"].disabled

    asyncio.run(run())


def test_game_info_view_keeps_disabled_rank_buttons_visible():
    import discord_bot as db

    async def run():
        view = db.UserLeaderboardView(
            _interaction(),
            [{
                "embed": discord.Embed(title="Info"),
                "rank_page_count": 1,
                "rank_pages": {0: _rank_page(1, 1, 3)},
            }],
            show_game_controls=False,
        )
        buttons = _buttons(view)
        assert list(buttons) == ["Previous page", "Next page", "Share"]
        for label in ("Previous page", "Next page"):
            assert buttons[label].disabled
        assert not buttons["Share"].disabled

    asyncio.run(run())


def test_leaderboard_view_only_builds_pages_when_visited(mocker):
    import discord_bot as db

    async def run():
        first = SimpleNamespace(id="ONE")
        second = SimpleNamespace(id="TWO")
        games = [
            {
                "game": first,
                "leaderboard": [SimpleNamespace()] * 20,
                "title": "One",
                "rank_page_count": 2,
                "rank_pages": {},
            },
            {
                "game": second,
                "leaderboard": [SimpleNamespace()] * 4,
                "title": "Two",
                "rank_page_count": 1,
                "rank_pages": {},
            },
        ]

        async def build(game, _leaderboard, _guild, page_index):
            start = page_index * 15 + 1
            return _rank_page(page_index + 1, start, start)

        builder = mocker.patch.object(db, "_build_rank_page", side_effect=build)
        view = db.UserLeaderboardView(_interaction(), games)

        await view.prepare()
        assert [(call.args[0].id, call.args[3]) for call in builder.call_args_list] == [("ONE", 0)]

        await view._next_rank_page(_interaction())
        await view._previous_rank_page(_interaction())
        # Returning to an already rendered page reuses the per-view cache.
        assert [(call.args[0].id, call.args[3]) for call in builder.call_args_list] == [
            ("ONE", 0),
            ("ONE", 1),
        ]

        await view._next_game(_interaction())
        assert [(call.args[0].id, call.args[3]) for call in builder.call_args_list][-1] == ("TWO", 0)

    asyncio.run(run())


def test_game_info_embed_contains_complete_user_facing_configuration():
    import discord_bot as db

    game = SimpleNamespace(
        id="ABCDE",
        name="Test Game",
        owner_id=10,
        status="active",
        private_game=True,
        template_id=7,
        start_date=date(2026, 8, 1),
        pick_date=date(2026, 8, 2),
        end_date=date(2026, 8, 31),
        datetime_created=datetime(2026, 7, 20, 12, 30),
        last_updated=datetime(2026, 8, 5, 13, 15),
        start_money=10_000,
        pick_count=10,
        draft_mode=True,
        allow_selling=False,
        update_frequency="alpaca",
        current_value=31_500,
        change_dollars=1_500,
        change_percent=5,
    )

    embed = db._game_info_embed(game, 3)
    fields = {field.name: field.value for field in embed.fields}

    assert set(fields) == {"Overview", "Schedule", "Rules", "Performance"}
    assert "Participants: 3" in fields["Overview"]
    assert "Exclusive picks: Yes" in fields["Rules"]
    assert "Combined value: $31,500.00" in fields["Performance"]


def test_prestart_notice_only_for_open_games_uses_discord_timestamps(mocker):
    import discord_bot as db

    mocker.patch.object(
        db.fe.gl,
        "market_open_est",
        datetime(1900, 1, 1, 9, 30),
    )
    open_game = SimpleNamespace(status="open", start_date=date(2026, 8, 2))
    active_game = SimpleNamespace(status="active", start_date=date(2026, 8, 2))

    assert db._prestart_notice(active_game) is None
    notice = db._prestart_notice(open_game)
    assert notice is not None
    assert notice.startswith("⚠️⚠️")
    assert "pending" in notice.lower()
    ts = db._first_buy_approx_unix(open_game)
    assert f"<t:{ts}:D>" in notice
    assert f"<t:{ts}:t>" in notice
    assert f"<t:{ts}:R>" in notice


def test_leaderboard_page_payload_prepends_prestart_notice(mocker):
    import discord_bot as db

    async def run():
        mocker.patch.object(db, "_prestart_notice", return_value="⚠️⚠️ NOTICE")
        view = db.UserLeaderboardView(
            SimpleNamespace(user=SimpleNamespace(id=1), guild=None),
            [
                {
                    "game": SimpleNamespace(status="open", start_date=date(2026, 8, 2)),
                    "leaderboard": [],
                    "title": "Soon Game",
                    "description": "Your rank: #1",
                    "embed": None,
                    "rank_page_count": 1,
                    "rank_pages": {
                        0: {
                            "png": b"png",
                            "filename": "lb.png",
                            "rank_start": 1,
                            "rank_end": 0,
                        }
                    },
                }
            ],
            show_game_controls=False,
        )
        embed, _file = view._page_payload()
        assert embed.description is not None
        assert embed.description.startswith("⚠️⚠️ NOTICE")
        assert "Your rank: #1" in embed.description

    asyncio.run(run())


def test_private_leaderboard_access_requires_owner_or_participation(mocker):
    import discord_bot as db

    game = SimpleNamespace(id="PRIV1", private_game=True, owner_id=10)
    assert db._user_can_view_leaderboard(game, 10)

    mock_fe = MagicMock()
    mock_fe.be.get_many_participants.side_effect = LookupError()
    mocker.patch.object(db, "fe", mock_fe)
    assert not db._user_can_view_leaderboard(game, 20)

    mock_fe.be.get_many_participants.side_effect = None
    mock_fe.be.get_many_participants.return_value = [
        SimpleNamespace(status="active")
    ]
    assert db._user_can_view_leaderboard(game, 20)


def test_public_leaderboard_is_visible_without_participation():
    import discord_bot as db

    game = SimpleNamespace(id="PUB01", private_game=False, owner_id=10)
    assert db._user_can_view_leaderboard(game, 999)


def _leaderboard_entry(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        current_value=11_000,
        joined=date(2026, 8, 1),
        change_dollars=1_000,
        change_percent=10,
        last_updated=datetime(2026, 8, 5, 13, 0),
        days_in_first=3,
    )


def _page_game(template_id) -> SimpleNamespace:
    return SimpleNamespace(
        id="GAME1",
        name="Game",
        owner_id=10,
        template_id=template_id,
        start_money=10_000,
        start_date=date(2026, 8, 1),
        end_date=None,
        status="active",
    )


def test_recurring_game_page_renders_chip_layout_with_picks(mocker):
    import discord_bot as db

    async def run():
        db._leaderboard_image_cache.clear()
        mocker.patch.object(db, "resolve_player_name", AsyncMock(return_value="Ann"))
        picks = mocker.patch.object(
            db,
            "collect_player_picks",
            return_value=[{"ticker": "AAPL", "company": "Apple", "change_percent": 4.0}],
        )
        render = mocker.patch.object(
            db, "_cached_game_info_leaderboard_png", return_value=b"png"
        )

        await db._build_rank_page(_page_game(4), [_leaderboard_entry(1)], None, 0)

        assert picks.call_args.args[1:] == ("GAME1", 1)
        processed = render.call_args.args[2]
        assert processed[0]["picks"][0]["ticker"] == "AAPL"
        assert processed[0]["days_in_first"] == 3
        assert render.call_args.args[3] is True

    asyncio.run(run())


def test_one_off_game_page_keeps_simple_table_and_skips_pick_lookup(mocker):
    import discord_bot as db

    async def run():
        db._leaderboard_image_cache.clear()
        mocker.patch.object(db, "resolve_player_name", AsyncMock(return_value="Ann"))
        picks = mocker.patch.object(db, "collect_player_picks", return_value=[])
        render = mocker.patch.object(
            db, "_cached_game_info_leaderboard_png", return_value=b"png"
        )

        await db._build_rank_page(_page_game(None), [_leaderboard_entry(1)], None, 0)

        picks.assert_not_called()
        processed = render.call_args.args[2]
        assert "picks" not in processed[0]
        assert render.call_args.args[3] is False

    asyncio.run(run())


def test_recurring_rank_pages_contain_five_players(mocker):
    import discord_bot as db

    async def run():
        db._leaderboard_image_cache.clear()
        mocker.patch.object(db, "resolve_player_name", AsyncMock(return_value="Player"))
        mocker.patch.object(db, "collect_player_picks", return_value=[])
        render = mocker.patch.object(
            db, "_cached_game_info_leaderboard_png", return_value=b"png"
        )
        leaderboard = [_leaderboard_entry(user_id) for user_id in range(1, 13)]
        game = _page_game(4)

        descriptor = db._leaderboard_game_data(game, leaderboard)
        page = await db._build_rank_page(game, leaderboard, None, 1)

        assert descriptor["rank_page_count"] == 3
        assert page["rank_start"] == 6
        assert page["rank_end"] == 10
        processed = render.call_args.args[2]
        assert [row["user_id"] for row in processed] == [6, 7, 8, 9, 10]
        assert [row["rank"] for row in processed] == [6, 7, 8, 9, 10]

    asyncio.run(run())


def test_cached_png_switches_generator_on_recurring_flag():
    import discord_bot as db

    db._leaderboard_image_cache.clear()
    game_data = {"name": "Game", "id": "GAME1", "starting_money": 10_000}
    players = [
        {
            "rank": 16,
            "user_id": 1,
            "display_name": "Ann",
            "current_value": 11_000,
            "change_dollars": 1_000,
            "change_percent": 10,
            "days_in_first": 3,
            "joined": date(2026, 8, 1),
            "picks": [{"ticker": "AAPL", "company": "Apple", "change_percent": 4.0}],
        }
    ]

    recurring = db._cached_game_info_leaderboard_png("rec", game_data, players, True)
    simple = db._cached_game_info_leaderboard_png("simple", game_data, players, False)

    assert recurring.startswith(b"\x89PNG") and simple.startswith(b"\x89PNG")
    assert recurring != simple
    # Same key + same fingerprint returns cached bytes.
    assert db._cached_game_info_leaderboard_png("rec", game_data, players, True) == recurring
    # Different row payload misses the cache.
    assert db._cached_game_info_leaderboard_png("rec", game_data, [], True) != recurring


def test_slash_png_cache_hit_skips_generator(mocker):
    import discord_bot as db

    db._leaderboard_image_cache.clear()
    game_data = {"name": "Game", "id": "GAME1", "starting_money": 10_000}
    players = [
        {
            "rank": 1,
            "user_id": 1,
            "display_name": "Ann",
            "current_value": 11_000,
            "change_dollars": 1_000,
            "change_percent": 10,
            "days_in_first": 0,
            "joined": date(2026, 8, 1),
            "picks": [],
        }
    ]
    generator = MagicMock()
    generator.create_leaderboard_image.return_value = BytesIO(b"\x89PNG\r\n\x1a\nfirst")
    mocker.patch.object(db, "get_leaderboard_generator", return_value=generator)

    first = db._cached_game_info_leaderboard_png("g:0", game_data, players, False)
    second = db._cached_game_info_leaderboard_png("g:0", game_data, players, False)
    assert first == second
    assert generator.create_leaderboard_image.call_count == 1

    players[0]["change_percent"] = 12
    generator.create_leaderboard_image.return_value = BytesIO(b"\x89PNG\r\n\x1a\nsecond")
    third = db._cached_game_info_leaderboard_png("g:0", game_data, players, False)
    assert third != first
    assert generator.create_leaderboard_image.call_count == 2


def test_game_arrows_only_include_own_and_other_recurring_games(mocker):
    import discord_bot as db

    mine = SimpleNamespace(id="MINE1", template_id=None)
    duplicate_mine = SimpleNamespace(id="MINE1", template_id=None)
    recurring = SimpleNamespace(id="REC01", template_id=7)
    unrelated = SimpleNamespace(id="PUB01", template_id=None)
    fake_fe = SimpleNamespace(
        list_my_games_ranked=lambda _user_id: [(mine, 2)],
        list_games_ranked=lambda **_kwargs: [
            (recurring, 8),
            (duplicate_mine, 2),
            (unrelated, 20),
        ],
    )
    mocker.patch.object(db, "fe", fake_fe)

    result = db._leaderboard_browse_games(42)

    assert [game.id for game, _count in result] == ["MINE1", "REC01"]


def _text_channel_interaction(*, can_share: bool = True):
    interaction = _interaction()
    perms = SimpleNamespace(
        view_channel=can_share,
        send_messages=can_share,
        attach_files=can_share,
    )
    me = SimpleNamespace()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 999
    channel.mention = "#general"
    channel.guild = SimpleNamespace(me=me)
    channel.permissions_for = MagicMock(return_value=perms)
    channel.send = AsyncMock()
    interaction.channel = channel
    interaction.user = SimpleNamespace(id=10, mention="<@10>")
    interaction.followup = SimpleNamespace(send=AsyncMock())
    return interaction


def test_share_posts_image_and_attribution_when_allowed():
    import discord_bot as db

    async def run():
        view = db.UserLeaderboardView(
            _text_channel_interaction(),
            [{
                "title": "My Game",
                "rank_page_count": 1,
                "rank_pages": {0: _rank_page(1, 1, 5)},
            }],
        )
        click = _text_channel_interaction()
        await view._share(click)

        click.response.defer.assert_awaited_once_with(ephemeral=True)
        click.channel.send.assert_awaited_once()
        sent = click.channel.send.await_args
        assert sent.kwargs["content"] == "Shared by <@10> | Leaderboard | My Game"
        assert sent.kwargs["file"].filename == "page-1.png"
        click.followup.send.assert_not_awaited()

    asyncio.run(run())


def test_share_denied_when_bot_lacks_permissions(mocker):
    import discord_bot as db

    async def run():
        click = _text_channel_interaction(can_share=False)
        await db._post_shared_image(
            click,
            png_bytes=b"png",
            filename="share.png",
            context="Portfolio | Game [G1]",
        )

        click.channel.send.assert_not_awaited()
        click.followup.send.assert_awaited_once()
        assert click.followup.send.await_args.kwargs["embed"].title == "Cannot share here"

    asyncio.run(run())


def test_portfolio_share_view_posts_publicly():
    import discord_bot as db

    async def run():
        owner = _text_channel_interaction()
        view = db.PortfolioShareView(
            owner,
            png_bytes=b"portfolio-png",
            filename="portfolio.png",
            context="Portfolio | Game [G1]",
        )
        click = _text_channel_interaction()
        share_button = _buttons(view)["Share"]
        await share_button.callback(click)

        click.channel.send.assert_awaited_once()
        assert "Portfolio | Game [G1]" in click.channel.send.await_args.kwargs["content"]

    asyncio.run(run())


def test_leaderboard_view_timeout_disables_share_and_pagination():
    import discord_bot as db

    async def run():
        interaction = _interaction()
        message = MagicMock()
        message.edit = AsyncMock()
        interaction.original_response = AsyncMock(return_value=message)
        view = db.UserLeaderboardView(
            interaction,
            [{
                "title": "One",
                "rank_page_count": 2,
                "rank_pages": {
                    0: _rank_page(1, 1, 15),
                    1: _rank_page(2, 16, 22),
                },
            },
            {
                "title": "Two",
                "rank_page_count": 1,
                "rank_pages": {0: _rank_page(1, 1, 4)},
            }],
        )
        await view.on_timeout()

        message.edit.assert_awaited_once()
        expired = message.edit.await_args.kwargs["view"]
        buttons = _buttons(expired)
        assert all(button.disabled for button in buttons.values())

    asyncio.run(run())


def test_portfolio_share_view_timeout_disables_share():
    import discord_bot as db

    async def run():
        interaction = _interaction()
        message = MagicMock()
        message.edit = AsyncMock()
        interaction.original_response = AsyncMock(return_value=message)
        view = db.PortfolioShareView(
            interaction,
            png_bytes=b"portfolio-png",
            filename="portfolio.png",
            context="Portfolio | Game [G1]",
        )
        await view.on_timeout()

        message.edit.assert_awaited_once()
        share = _buttons(message.edit.await_args.kwargs["view"])["Share"]
        assert share.disabled

    asyncio.run(run())

