from pathlib import Path
import sqlite3

from helpers.db_backup import create_db_backup, prune_backups, maybe_daily_backup
from helpers.sqlhelper import SqlHelper
from db_schema import create, db_ver, remake_db_on_mismatch, ensure_database, MIGRATIONS, repair_zero_stock_pick_start_values


def test_create_fresh_database_has_current_version(db_path):
    create(db_path, upgrade=False)
    info = SqlHelper(db_path).get("database_info", filters={"database_name": db_path})
    assert info.status == "success"
    assert info.result[0]["current_version"] == db_ver
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(game_templates)")}
        assert "push_leaderboard" in cols
        assert "leaderboard_channel_id" in cols
        assert "auto_top_roles" in cols
        game_cols = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
        assert "top_roles_applied" in game_cols
        assert "leaderboard_final_pushed" in game_cols
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "template_role_holders" in tables
    finally:
        conn.close()


def test_remake_on_version_mismatch_backs_up_and_wipes(db_path):
    create(db_path, upgrade=False)
    sql = SqlHelper(db_path)
    sql.insert(
        "users",
        {
            "user_id": 9001,
            "source": "testing",
            "datetime_created": "2025-01-01 00:00:00",
        },
    )
    sql.update(
        "database_info",
        {"current_version": "0.0.5"},
        filters={"database_name": db_path},
    )

    backup = remake_db_on_mismatch(db_path)

    assert backup is not None
    assert Path(backup).is_file()
    rebuilt = SqlHelper(db_path)
    user = rebuilt.get("users", filters={"user_id": 9001})
    assert user.status == "error"
    info = rebuilt.get("database_info", filters={"database_name": db_path})
    assert info.result[0]["current_version"] == db_ver


def test_create_remakes_legacy_database_without_metadata(db_path):
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO users (user_id) VALUES (77)")
    connection.commit()
    connection.close()

    create(db_path)

    rebuilt = SqlHelper(db_path)
    user = rebuilt.get("users", filters={"user_id": 77})
    assert user.status == "error"  # wiped
    info = rebuilt.get("database_info", filters={"database_name": db_path})
    assert info.result[0]["current_version"] == db_ver


def test_backup_prune_retention(db_path, tmp_path):
    create(db_path, upgrade=False)
    stem = Path(db_path).stem
    for _ in range(5):
        path = create_db_backup(db_path, kind="hourly")
        assert path is not None
    removed = prune_backups(db_path, kind="hourly", keep=2)
    assert removed >= 3
    remaining = [
        p
        for p in Path(db_path).resolve().parent.joinpath("backups").glob(f"{stem}-hourly-*.db")
    ]
    assert len(remaining) == 2


def test_maybe_daily_backup_once_per_day(db_path):
    create(db_path, upgrade=False)
    first = maybe_daily_backup(db_path)
    second = maybe_daily_backup(db_path)
    assert first is not None
    assert second is None


def test_ensure_database_creates_missing_file(tmp_path):
    target = tmp_path / "fresh.sqlite"
    assert ensure_database(str(target)) == "created"
    assert ensure_database(str(target)) == "unchanged"


def test_ensure_database_remakes_when_no_migration(db_path):
    create(db_path, upgrade=False)
    sql = SqlHelper(db_path)
    sql.insert(
        "users",
        {
            "user_id": 42,
            "source": "testing",
            "datetime_created": "2025-01-01 00:00:00",
        },
    )
    sql.update(
        "database_info",
        {"current_version": "0.0.9"},
        filters={"database_name": db_path},
    )

    assert ensure_database(db_path) == "remade"
    assert SqlHelper(db_path).get("users", filters={"user_id": 42}).status == "error"


def test_ensure_database_runs_registered_migration(db_path):
    create(db_path, upgrade=False)
    sql = SqlHelper(db_path)
    sql.insert(
        "users",
        {
            "user_id": 99,
            "source": "testing",
            "datetime_created": "2025-01-01 00:00:00",
        },
    )
    sql.update(
        "database_info",
        {"current_version": "0.0.9"},
        filters={"database_name": db_path},
    )

    def fake_migrate(_db_name: str) -> None:
        # Preserve rows; only the version stamp changes via ensure_database.
        return None

    MIGRATIONS[("0.0.9", db_ver)] = fake_migrate
    try:
        assert ensure_database(db_path) == "migrated"
        rebuilt = SqlHelper(db_path)
        assert rebuilt.get("users", filters={"user_id": 99}).status == "success"
        info = rebuilt.get("database_info", filters={"database_name": db_path})
        assert info.result[0]["current_version"] == db_ver
    finally:
        MIGRATIONS.pop(("0.0.9", db_ver), None)


def test_repair_zero_stock_pick_start_values(be):
    owner_id = 501
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="BadStartValues",
        start_date="2025-01-01",
        starting_money=1.0,
        total_picks=500,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(game_id=game_id)[0]
    be.add_stock("FIXME", "NASDAQ", "Fix Me Co")
    stock = be.get_stock("FIXME")
    be.add_stock_pick(participant.id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    be.update_stock_pick(
        pick.id,
        status="owned",
        shares=0.0002,
        start_value=0.0,
        current_value=0.01,
        change_dollars=0.01,
        change_percent=0.0,
    )

    assert repair_zero_stock_pick_start_values(be.sql.db) == 1

    updated = be.get_stock_pick(pick.id)
    assert updated.start_value == 0.002
    assert updated.change_dollars == 0.008
    assert updated.change_percent == 400.0


def test_ensure_database_migrates_0_2_4_to_0_2_5_and_repairs_picks(be):
    owner_id = 502
    be.add_user(owner_id, "testing")
    game_id = be.add_game(
        user_id=owner_id,
        name="MigrateRepair",
        start_date="2025-01-01",
        starting_money=1.0,
        total_picks=500,
    )
    be.update_game(game_id, status="active")
    be.add_participant(owner_id, game_id)
    participant = be.get_many_participants(game_id=game_id)[0]
    be.add_stock("MIGFX", "NASDAQ", "Migrate Fix Co")
    stock = be.get_stock("MIGFX")
    be.add_stock_pick(participant.id, stock.id)
    pick = be.get_many_stock_picks(participant_id=participant.id)[0]
    be.update_stock_pick(
        pick.id,
        status="owned",
        shares=0.0002,
        start_value=0.0,
        current_value=0.002,
    )
    be.sql.update(
        "database_info",
        {"current_version": "0.2.4"},
        filters={"database_name": be.sql.db},
    )

    assert ensure_database(be.sql.db) == "migrated"
    info = be.sql.get("database_info", filters={"database_name": be.sql.db})
    assert info.result[0]["current_version"] == "0.2.5"
    updated = be.get_stock_pick(pick.id)
    assert updated.start_value == 0.002
