"""game-list visibility and pending participant counts."""

from datetime import date


def test_list_games_ranked_includes_viewer_private_games(fe):
    fe.register(20)
    public_id = fe.new_game(user_id=20, name="PublicGame", start_date="2099-01-01")
    private_mine = fe.new_game(
        user_id=10,
        name="MyPrivate",
        start_date="2099-02-01",
        private_game=True,
    )
    private_theirs = fe.new_game(
        user_id=20,
        name="TheirPrivate",
        start_date="2099-03-01",
        private_game=True,
    )
    fe.register(30)
    fe.join_game(30, private_theirs)

    ranked = fe.list_games_ranked(viewer_user_id=10, today=date(2026, 8, 1))
    ids = {game.id for game, _count in ranked}

    assert public_id in ids
    assert private_mine in ids
    assert private_theirs not in ids


def test_count_pending_participants(fe):
    game_id = fe.new_game(
        user_id=10,
        name="PendingCount",
        start_date="2099-01-01",
        private_game=True,
    )
    fe.register(20)
    fe.register(30)
    fe.join_game(20, game_id)
    fe.join_game(30, game_id)

    assert fe.count_pending_participants(game_id) == 2
