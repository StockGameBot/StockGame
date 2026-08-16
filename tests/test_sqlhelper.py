from helpers.sqlhelper import SqlHelper


def test_insert_many_inserts_all_rows(db_path):
    sql = SqlHelper(db_path)
    sql.send_query("CREATE TABLE samples (id INTEGER PRIMARY KEY, name TEXT NOT NULL)", mode="insert")

    result = sql._insert_many(
        "samples",
        columns=["id", "name"],
        rows=[{"id": 1, "name": "one"}, {"id": 2, "name": "two"}],
    )

    assert result.status == "success"
    rows = sql.get("samples")
    assert rows.status == "success"
    assert rows.result == ({"id": 1, "name": "one"}, {"id": 2, "name": "two"})


def test_update_requires_force_without_filters(db_path):
    sql = SqlHelper(db_path)
    sql.send_query("CREATE TABLE samples (id INTEGER PRIMARY KEY, name TEXT NOT NULL)", mode="insert")
    sql.insert("samples", {"id": 1, "name": "one"})

    blocked = sql.update("samples", {"name": "changed"})
    assert blocked.status == "error"
    assert blocked.reason == "FORCE REQUIRED"

    applied = sql.update("samples", {"name": "changed"}, force=True)
    assert applied.status == "success"


def test_delete_table_uses_current_schema_allowlist(db_path):
    sql = SqlHelper(db_path)
    sql.send_query("CREATE TABLE users (id INTEGER PRIMARY KEY)", mode="insert")

    result = sql.delete_table("users", force=True)

    assert result.status == "success"
    missing = sql.get("users")
    assert missing.status == "error"


def test_concurrent_helpers_on_same_db_do_not_corrupt(db_path):
    """Frontend + GameLogic each own a SqlHelper on the same file (production layout)."""
    import threading

    sql_a = SqlHelper(db_path)
    sql_b = SqlHelper(db_path)
    sql_a.send_query("CREATE TABLE samples (id INTEGER PRIMARY KEY, name TEXT NOT NULL)", mode="insert")
    errors: list[BaseException] = []

    def writer(prefix: str, sql: SqlHelper) -> None:
        try:
            for i in range(50):
                sql.insert("samples", {"id": int(f"{prefix}{i:02d}"), "name": f"{prefix}-{i}"})
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=("1", sql_a)),
        threading.Thread(target=writer, args=("2", sql_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    rows = sql_a.get("samples")
    assert rows.status == "success"
    assert len(rows.result) == 100

