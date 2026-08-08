from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from helpers.leaderboard_push import (
    bot_can_push_to_channel,
    build_push_embed,
    chunk_push_players,
    is_unknown_message_error,
    parse_leaderboard_message_ids,
    push_or_edit_leaderboard_message,
    push_or_edit_leaderboard_messages,
    serialize_leaderboard_message_ids,
)
from helpers.recurring_leaderboard_image import (
    LEADERBOARD_N_CANDIDATES,
    TITLE_BLOCK,
    estimate_recurring_leaderboard_height,
    select_leaderboard_n,
    sort_picks_by_performance,
    RecurringLeaderboardImageGenerator,
)
from helpers.sqlhelper import _iso8601
from stocks import Backend, GameLogic


def test_height_estimate_scales_with_players_and_picks():
    h5 = estimate_recurring_leaderboard_height(5, 10)
    h10 = estimate_recurring_leaderboard_height(10, 10)
    assert h10 > h5
    with_chips = estimate_recurring_leaderboard_height(1, 20)
    without = estimate_recurring_leaderboard_height(1, 0)
    assert with_chips > without


@pytest.mark.parametrize("n", list(LEADERBOARD_N_CANDIDATES))
def test_select_n_candidates(n):
    picks = [3] * 40
    chosen = select_leaderboard_n(picks, max_height=10_000, target=n)
    assert chosen == n


def test_select_n_respects_height_budget():
    picks = [12] * 30
    chosen = select_leaderboard_n(picks, max_height=1200, target=30)
    assert chosen in LEADERBOARD_N_CANDIDATES
    assert chosen < 30
    assert estimate_recurring_leaderboard_height(chosen, picks[:chosen]) <= 1200


def test_picks_sorted_best_first():
    picks = [
        {"ticker": "AAA", "change_percent": -4.0},
        {"ticker": "BBB", "change_percent": 12.5},
        {"ticker": "CCC", "change_percent": None},
        {"ticker": "DDD", "change_percent": 3.0},
    ]
    assert [p["ticker"] for p in sort_picks_by_performance(picks)] == ["BBB", "DDD", "CCC", "AAA"]


def test_recurring_image_smoke():
    players = [
        {
            "user_id": 1,
            "display_name": "Alice",
            "current_value": 10500,
            "change_dollars": 500,
            "change_percent": 5.0,
            "days_in_first": 2,
            "joined": "2026-01-01",
            "picks": [
                {"ticker": "AAPL", "company": "Apple", "change_percent": 1.2},
                {"ticker": "MSFT", "company": "Microsoft", "change_percent": -0.5},
            ],
        }
    ]
    buf = RecurringLeaderboardImageGenerator().create_image({"name": "Test", "id": "abc"}, players)
    assert buf.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_without_title_is_shorter_and_footer_uses_created_at():
    players = [
        {
            "user_id": 1,
            "display_name": "Alice",
            "current_value": 10500,
            "change_dollars": 500,
            "change_percent": 5.0,
            "days_in_first": 2,
            "picks": [],
        }
    ]
    generator = RecurringLeaderboardImageGenerator()
    with_title = generator.create_image({"name": "Test", "id": "abc"}, players, show_title=True)
    without_title = generator.create_image(
        {"name": "Test", "id": "abc"},
        players,
        show_title=False,
        created_at=datetime(2026, 8, 5, 20, 15),
    )
    from PIL import Image

    titled = Image.open(with_title)
    untitled = Image.open(without_title)
    assert untitled.height == titled.height - TITLE_BLOCK
    assert untitled.height == estimate_recurring_leaderboard_height(
        1, 0, title_block=0
    )


def test_chunk_push_players_caps_at_twenty_and_keeps_empty_page():
    players = [{"user_id": i} for i in range(23)]
    pages = chunk_push_players(players)
    assert len(pages) == 4
    assert [len(page) for page in pages] == [5, 5, 5, 5]
    assert pages[-1][-1]["user_id"] == 19
    assert chunk_push_players([]) == [[]]
    assert [len(page) for page in chunk_push_players(players[:7])] == [5, 2]


def test_parse_and_serialize_leaderboard_message_ids():
    assert parse_leaderboard_message_ids(None) == []
    assert parse_leaderboard_message_ids("111, 222,333") == ["111", "222", "333"]
    assert serialize_leaderboard_message_ids(["111", "222"]) == "111,222"
    assert serialize_leaderboard_message_ids([]) is None


def test_render_push_pages_splits_top_twenty_without_image_titles(mocker):
    from io import BytesIO

    import helpers.leaderboard_push as lp

    lp.clear_push_image_cache()
    generator = MagicMock()
    generator.create_image.side_effect = [
        BytesIO(b"png1"),
        BytesIO(b"png2"),
        BytesIO(b"png3"),
        BytesIO(b"png4"),
    ]
    mocker.patch.object(lp, "get_recurring_generator", return_value=generator)
    game = SimpleNamespace(
        name="Recurring",
        id="REC01",
        change_dollars=100,
        change_percent=1,
        start_date=date.today(),
        end_date=None,
    )
    players = [{"user_id": user_id} for user_id in range(20)]

    embed, images, fingerprint, cache_hit = lp.render_push_pages(game, players, [])

    assert cache_hit is False
    assert fingerprint
    assert len(images) == 4
    assert "(ID: REC01)" in embed.title
    calls = generator.create_image.call_args_list
    assert [call.kwargs["show_title"] for call in calls] == [False, False, False, False]
    assert [call.kwargs["target_n"] for call in calls] == [5, 5, 5, 5]
    assert [row["rank"] for row in calls[1].args[1]] == [6, 7, 8, 9, 10]
    assert all(call.kwargs["created_at"] is not None for call in calls)

    embed2, images2, fingerprint2, cache_hit2 = lp.render_push_pages(game, players, [])
    assert cache_hit2 is True
    assert fingerprint2 == fingerprint
    assert len(images2) == 4
    assert generator.create_image.call_count == 4


def test_days_in_first_idempotent(db_path, mocker):
    be = Backend(db_path)
    be.add_user(1, "discord", "One")
    be.add_user(2, "discord", "Two")
    game_id = be.add_game(
        user_id=1,
        name="Days First Game",
        start_date="2020-01-01",
        starting_money=10000,
        total_picks=2,
    )
    be.update_game(game_id=game_id, status="active")
    be.add_participant(1, game_id)
    be.add_participant(2, game_id)
    p1 = be.get_many_participants(game_id=game_id, user_id=1)[0]
    p2 = be.get_many_participants(game_id=game_id, user_id=2)[0]
    be.update_participant(p1.id, current_value=12000, change_dollars=2000, change_percent=20)
    be.update_participant(p2.id, current_value=9000, change_dollars=-1000, change_percent=-10)

    logic = GameLogic(db_path)
    mocker.patch.object(logic, "_is_market_hours", return_value=False)
    mocker.patch.object(logic, "_today_et", return_value=date(2026, 7, 30))  # Thursday

    logic.record_days_in_first(game_id=game_id)
    logic.record_days_in_first(game_id=game_id)

    leader = be.get_many_participants(game_id=game_id, user_id=1)[0]
    assert leader.days_in_first == 1
    snaps = be.sql.get(
        "leaderboard_day_snapshots",
        filters={"game_id": str(game_id), "trade_date": "2026-07-30"},
    )
    assert snaps.status == "success"
    assert len(snaps.result) == 1


def test_is_unknown_message_error():
    assert is_unknown_message_error(discord.NotFound(MagicMock(), "missing"))
    http = discord.HTTPException(MagicMock(), "Unknown Message")
    http.code = 10008
    assert is_unknown_message_error(http)
    assert not is_unknown_message_error(RuntimeError("boom"))


def test_push_embed_carries_game_title_and_not_image_attachment():
    game = MagicMock()
    game.name = "Example"
    game.id = "ABCDE"
    game.change_dollars = 100
    game.change_percent = 1
    game.start_date = date.today()
    game.end_date = None

    embed = build_push_embed(game)

    assert embed.title == "📈 Example (ID: ABCDE)"
    assert embed.image.url is None


def test_push_edits_standalone_attachment_in_place():
    import asyncio
    from io import BytesIO

    message = AsyncMock()
    message.id = 111
    channel = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    game = MagicMock(id="g1", leaderboard_message_id="111")
    fe = MagicMock()

    new_id = asyncio.run(
        push_or_edit_leaderboard_message(
            channel=channel,
            game=game,
            fe=fe,
            embed=discord.Embed(title="stats"),
            image=BytesIO(b"fakepng"),
        )
    )

    assert new_id == "111"
    message.edit.assert_awaited_once()
    kwargs = message.edit.await_args.kwargs
    assert kwargs["embed"].image.url is None
    assert len(kwargs["attachments"]) == 1
    assert kwargs["attachments"][0].filename == "recurring_leaderboard.png"
    fe.be.update_game.assert_called_with(game_id="g1", leaderboard_message_id="111")


def test_push_edit_then_resend_on_unknown():
    import asyncio
    from io import BytesIO

    channel = AsyncMock()
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(), {"message": "Unknown Message"})
    )
    partial = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    sent = MagicMock()
    sent.id = 555
    channel.send = AsyncMock(return_value=sent)

    game = MagicMock()
    game.id = "g1"
    game.leaderboard_message_id = "111"
    fe = MagicMock()
    fe.be.update_game = MagicMock()

    embed = discord.Embed(title="t")

    async def _run():
        return await push_or_edit_leaderboard_message(
            channel=channel,
            game=game,
            fe=fe,
            embed=embed,
            image=BytesIO(b"fakepng"),
        )

    new_id = asyncio.run(_run())
    assert new_id == "555"
    partial.delete.assert_awaited()
    channel.send.assert_awaited()
    fe.be.update_game.assert_called()


def test_multi_page_push_puts_embed_on_last_and_deletes_extras():
    import asyncio
    from io import BytesIO

    first = AsyncMock()
    first.id = 111
    second = AsyncMock()
    second.id = 222
    channel = AsyncMock()
    channel.fetch_message = AsyncMock(side_effect=[first, second])
    stale = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=stale)

    game = MagicMock(id="g1", leaderboard_message_id="111,222,333")
    fe = MagicMock()
    embed = discord.Embed(title="stats")

    result = asyncio.run(
        push_or_edit_leaderboard_messages(
            channel=channel,
            game=game,
            fe=fe,
            embed=embed,
            images=[BytesIO(b"one"), BytesIO(b"two")],
        )
    )

    assert result == "111,222"
    assert first.edit.await_args.kwargs["embeds"] == []
    assert first.edit.await_args.kwargs["attachments"][0].filename == "recurring_leaderboard_1.png"
    assert second.edit.await_args.kwargs["embed"].title == "stats"
    assert second.edit.await_args.kwargs["attachments"][0].filename == "recurring_leaderboard_2.png"
    channel.get_partial_message.assert_called_with(333)
    stale.delete.assert_awaited_once()
    fe.be.update_game.assert_called_with(game_id="g1", leaderboard_message_id="111,222")


def test_multi_page_push_sends_new_pages_when_needed():
    import asyncio
    from io import BytesIO

    existing = AsyncMock()
    existing.id = 111
    sent = MagicMock()
    sent.id = 222
    channel = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=existing)
    channel.send = AsyncMock(return_value=sent)

    game = MagicMock(id="g1", leaderboard_message_id="111")
    fe = MagicMock()

    result = asyncio.run(
        push_or_edit_leaderboard_messages(
            channel=channel,
            game=game,
            fe=fe,
            embed=discord.Embed(title="stats"),
            images=[BytesIO(b"one"), BytesIO(b"two")],
        )
    )

    assert result == "111,222"
    assert existing.edit.await_args.kwargs["embeds"] == []
    assert channel.send.await_args.kwargs["embed"].title == "stats"
    fe.be.update_game.assert_called_with(game_id="g1", leaderboard_message_id="111,222")


def test_push_uses_live_name_resolver():
    import asyncio
    from io import BytesIO

    import helpers.leaderboard_push as lp

    game = MagicMock()
    game.id = "g1"
    game.template_id = 7
    template = MagicMock()
    template.push_leaderboard = 1
    template.leaderboard_channel_id = "42"

    fe = MagicMock()
    fe.be.get_many_games.return_value = [game]
    fe.be.get_game_template.return_value = template
    fe.be.get_game.return_value = game

    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = MagicMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel

    rendered: dict = {}

    def fake_render(_game, players, _owned):
        rendered["players"] = [dict(p) for p in players]
        return discord.Embed(title="t"), [BytesIO(b"png")], "fp1", False

    async def resolver(_user_id, _guild):
        return "LiveName"

    with patch.object(lp, "collect_push_players", return_value=([{"user_id": 5, "display_name": "ID(5)"}], [])), \
         patch.object(lp, "render_push_pages", side_effect=fake_render), \
         patch.object(lp, "bot_can_push_to_channel", return_value=True), \
         patch.object(lp, "push_or_edit_leaderboard_messages", new=AsyncMock(return_value="1")):
        asyncio.run(lp.push_all_recurring_leaderboards(bot, fe, name_resolver=resolver))

    assert rendered["players"][0]["display_name"] == "LiveName"


def test_fingerprint_push_pages_stable_until_value_changes():
    import helpers.leaderboard_push as lp

    game = SimpleNamespace(id="G1", name="Game")
    players = [
        {
            "user_id": 1,
            "rank": 1,
            "display_name": "Ann",
            "current_value": 11000.123456,
            "change_dollars": 1000.987,
            "change_percent": 10.5555,
            "days_in_first": 2,
            "picks": [{"ticker": "AAPL", "company": "Apple", "change_percent": 4.1111}],
        }
    ]
    first = lp.fingerprint_push_pages(game, players)
    second = lp.fingerprint_push_pages(game, players)
    assert first == second

    players[0]["change_percent"] = 10.5556
    assert lp.fingerprint_push_pages(game, players) != first


def test_push_all_skips_discord_edit_on_cache_hit(mocker):
    import asyncio
    from io import BytesIO

    import helpers.leaderboard_push as lp

    lp.clear_push_image_cache()
    game = SimpleNamespace(
        id="REC01",
        name="Recurring",
        status="active",
        template_id=1,
        leaderboard_channel_id="99",
        leaderboard_message_id="111",
        change_dollars=0,
        change_percent=0,
        start_date=date.today(),
        end_date=None,
    )
    fe = MagicMock()
    fe.list_games_ranked.return_value = [(game, 1)]
    fe.be.get_game.return_value = game

    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = MagicMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel

    push_edit = AsyncMock(return_value="111")
    players = [{"user_id": 5, "display_name": "Ann", "rank": 1, "current_value": 10000,
                "change_dollars": 0, "change_percent": 0, "days_in_first": 0, "picks": []}]

    def fake_render(_game, _players, _owned):
        return discord.Embed(title="t"), [BytesIO(b"png")], "same-fp", True

    with patch.object(lp, "collect_push_players", return_value=(players, [])), \
         patch.object(lp, "render_push_pages", side_effect=fake_render), \
         patch.object(lp, "bot_can_push_to_channel", return_value=True), \
         patch.object(lp, "push_or_edit_leaderboard_messages", new=push_edit):
        asyncio.run(lp.push_all_recurring_leaderboards(bot, fe, name_resolver=AsyncMock(return_value="Ann")))

    push_edit.assert_not_called()


def test_bot_can_push_permissions():
    channel = MagicMock()
    me = MagicMock()
    perms = MagicMock()
    perms.view_channel = True
    perms.send_messages = True
    perms.embed_links = True
    perms.attach_files = True
    channel.permissions_for.return_value = perms
    assert bot_can_push_to_channel(channel, me) is True
    perms.attach_files = False
    assert bot_can_push_to_channel(channel, me) is False
