import asyncio
from types import SimpleNamespace

import helpers.autocomplete as autocomplete


def test_sell_ticker_autocomplete_accepts_string_game_ids():
    calls = []
    fake_frontend = SimpleNamespace(
        my_stocks=lambda **kwargs: calls.append(kwargs) or (
            SimpleNamespace(stock_ticker="AAPL", status="owned"),
        )
    )
    autocomplete.init_autocomplete(fake_frontend)
    interaction = SimpleNamespace(
        data={"options": [{"name": "game_id", "value": "ABCDE"}]},
        user=SimpleNamespace(id=42),
    )

    choices = asyncio.run(autocomplete.sell_ticker_autocomplete(interaction, "AAP"))

    assert calls == [{
        "user_id": 42,
        "game_id": "ABCDE",
        "show_pending": True,
        "show_sold": False,
    }]
    assert [(choice.name, choice.value) for choice in choices] == [("AAPL", "AAPL")]


def test_buy_ticker_autocomplete_includes_typed_ticker_not_in_db():
    fake_frontend = SimpleNamespace(
        be=SimpleNamespace(
            get_many_stocks=lambda: (
                SimpleNamespace(ticker="AAPL", company="Apple Inc."),
                SimpleNamespace(ticker="MSFT", company="Microsoft Corporation"),
            ),
        ),
    )
    autocomplete.init_autocomplete(fake_frontend)

    choices = asyncio.run(autocomplete.buy_ticker_autocomplete(SimpleNamespace(), "nvda"))

    assert choices[0].value == "NVDA"
    assert "NVDA" in choices[0].name
    # Local cache still suggested when it matches the needle
    values = [c.value for c in choices]
    assert "NVDA" in values
    assert "AAPL" not in values
    assert "MSFT" not in values


def test_buy_ticker_autocomplete_prefers_db_label_for_known_ticker():
    fake_frontend = SimpleNamespace(
        be=SimpleNamespace(
            get_many_stocks=lambda: (
                SimpleNamespace(ticker="MSFT", company="Microsoft Corporation"),
            ),
        ),
    )
    autocomplete.init_autocomplete(fake_frontend)

    choices = asyncio.run(autocomplete.buy_ticker_autocomplete(SimpleNamespace(), "msft"))

    assert [(c.name, c.value) for c in choices] == [
        ("MSFT — Microsoft Corporation", "MSFT"),
    ]


def test_buy_ticker_autocomplete_bare_ticker_when_company_missing():
    fake_frontend = SimpleNamespace(
        be=SimpleNamespace(
            get_many_stocks=lambda: (
                SimpleNamespace(ticker="RACE", company="RACE"),
                SimpleNamespace(ticker="PHYS", company=""),
            ),
        ),
    )
    autocomplete.init_autocomplete(fake_frontend)

    race = asyncio.run(autocomplete.buy_ticker_autocomplete(SimpleNamespace(), "race"))
    phys = asyncio.run(autocomplete.buy_ticker_autocomplete(SimpleNamespace(), "phys"))
    assert [(c.name, c.value) for c in race if c.value == "RACE"] == [("RACE", "RACE")]
    assert [(c.name, c.value) for c in phys if c.value == "PHYS"] == [("PHYS", "PHYS")]


def test_buy_ticker_autocomplete_works_when_db_empty():
    fake_frontend = SimpleNamespace(
        be=SimpleNamespace(get_many_stocks=lambda: (_ for _ in ()).throw(LookupError("No items found"))),
    )
    autocomplete.init_autocomplete(fake_frontend)

    choices = asyncio.run(autocomplete.buy_ticker_autocomplete(SimpleNamespace(), "TSLA"))

    assert [(c.name, c.value) for c in choices] == [("TSLA", "TSLA")]


def test_buy_ticker_autocomplete_normalizes_class_share():
    fake_frontend = SimpleNamespace(
        be=SimpleNamespace(get_many_stocks=lambda: ()),
    )
    autocomplete.init_autocomplete(fake_frontend)

    choices = asyncio.run(autocomplete.buy_ticker_autocomplete(SimpleNamespace(), "brk.b"))

    assert choices[0].value == "BRK-B"


def test_join_game_autocomplete_uses_game_list_order_and_marks_recurring():
    calls = []
    recurring = SimpleNamespace(id="REC01", name="Monthly", template_id=7)
    regular = SimpleNamespace(id="ONE01", name="One Off", template_id=None)
    already_joined = SimpleNamespace(id="JOIN1", name="Already In", template_id=None)
    fake_frontend = SimpleNamespace(
        list_games_ranked=lambda **kwargs: calls.append(kwargs) or [
            (recurring, 2),
            (already_joined, 4),
            (regular, 10),
        ],
        be=SimpleNamespace(
            get_many_participants=lambda **kwargs: (
                SimpleNamespace(game_id="JOIN1"),
            )
        ),
    )
    autocomplete.init_autocomplete(fake_frontend)

    interaction = SimpleNamespace(user=SimpleNamespace(id=42))
    choices = asyncio.run(autocomplete.join_games_autocomplete(interaction, ""))

    assert calls == [{"include_open": True, "include_active": True}]
    assert [(choice.name, choice.value) for choice in choices] == [
        ("🔁 Monthly (ID: REC01)", "REC01"),
        ("One Off (ID: ONE01)", "ONE01"),
    ]


def test_join_game_autocomplete_shows_all_when_user_has_no_games():
    game = SimpleNamespace(id="OPEN1", name="Open Game", template_id=None)
    fake_frontend = SimpleNamespace(
        list_games_ranked=lambda **kwargs: [(game, 1)],
        be=SimpleNamespace(
            get_many_participants=lambda **kwargs: (_ for _ in ()).throw(
                LookupError()
            )
        ),
    )
    autocomplete.init_autocomplete(fake_frontend)

    choices = asyncio.run(
        autocomplete.join_games_autocomplete(
            SimpleNamespace(user=SimpleNamespace(id=99)), ""
        )
    )

    assert [(choice.name, choice.value) for choice in choices] == [
        ("Open Game (ID: OPEN1)", "OPEN1"),
    ]


def test_leaderboard_autocomplete_groups_accessible_games():
    my_recurring = SimpleNamespace(id="MYREC", name="My Monthly", template_id=7)
    my_regular = SimpleNamespace(id="MYONE", name="My One Off", template_id=None)
    other_recurring = SimpleNamespace(id="PUBRC", name="Public Monthly", template_id=8)
    other_regular = SimpleNamespace(id="PUB01", name="Public One Off", template_id=None)
    # Public listing may contain games already present in the user's list.
    fake_frontend = SimpleNamespace(
        list_my_games_ranked=lambda *args, **kwargs: [
            (my_recurring, 3),
            (my_regular, 2),
        ],
        list_games_ranked=lambda **kwargs: [
            (my_recurring, 3),
            (other_recurring, 8),
            (my_regular, 2),
            (other_regular, 12),
        ],
    )
    autocomplete.init_autocomplete(fake_frontend)
    interaction = SimpleNamespace(user=SimpleNamespace(id=42))

    choices = asyncio.run(autocomplete.leaderboard_games_autocomplete(interaction, ""))

    assert [(choice.name, choice.value) for choice in choices] == [
        ("🔁 My Monthly (ID: MYREC) [OWNER]", "MYREC"),
        ("My One Off (ID: MYONE) [OWNER]", "MYONE"),
        ("🔁 Public Monthly (ID: PUBRC)", "PUBRC"),
        ("Public One Off (ID: PUB01)", "PUB01"),
    ]


def test_participant_autocomplete_marks_recurring_and_owner():
    game = SimpleNamespace(
        id="G1",
        name="My Game",
        owner_id=42,
        template_id=7,
        private_game=False,
        status="active",
    )
    fake_frontend = SimpleNamespace(
        my_games=lambda user_id, include_ended=False: SimpleNamespace(
            games=[game],
        ),
    )
    autocomplete.init_autocomplete(fake_frontend)
    interaction = SimpleNamespace(user=SimpleNamespace(id=42))

    choices = asyncio.run(autocomplete.all_games_autocomplete(interaction, ""))

    assert [(choice.name, choice.value) for choice in choices] == [
        ("🔁 My Game (ID: G1) [OWNER]", "G1"),
    ]


def test_private_owner_autocomplete_uses_shared_label():
    game = SimpleNamespace(
        id="P1",
        name="Secret",
        owner_id=42,
        template_id=None,
        private_game=True,
        status="active",
    )
    fake_frontend = SimpleNamespace(
        my_games=lambda user_id, include_ended=False: SimpleNamespace(games=[game]),
    )
    autocomplete.init_autocomplete(fake_frontend)
    interaction = SimpleNamespace(user=SimpleNamespace(id=42))

    choices = asyncio.run(autocomplete.private_owner_games_autocomplete(interaction, ""))

    assert choices[0].name == "🔒 Secret (ID: P1) [OWNER]"


def test_leaderboard_autocomplete_searches_name_and_id():
    game = SimpleNamespace(id="ZXCVB", name="Moon League", template_id=None)
    fake_frontend = SimpleNamespace(
        list_my_games_ranked=lambda *args, **kwargs: (_ for _ in ()).throw(LookupError()),
        list_games_ranked=lambda **kwargs: [(game, 4)],
    )
    autocomplete.init_autocomplete(fake_frontend)
    interaction = SimpleNamespace(user=SimpleNamespace(id=42))

    by_name = asyncio.run(autocomplete.leaderboard_games_autocomplete(interaction, "moon"))
    by_id = asyncio.run(autocomplete.leaderboard_games_autocomplete(interaction, "xcv"))

    assert [choice.value for choice in by_name] == ["ZXCVB"]
    assert [choice.value for choice in by_id] == ["ZXCVB"]
