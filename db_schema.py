"""SQLite schema creation and versioned migrate-or-remake for StockGame.

``discord_bot`` calls :func:`ensure_database` on startup. That creates a missing
DB, applies a registered migration when one exists for the version jump, or
backs up and remakes an empty schema when no migration path is registered.
"""

from __future__ import annotations

import logging
import sqlite3
import os
from pathlib import Path
from typing import Callable

from helpers.sqlhelper import SqlHelper, _iso8601
from helpers.db_backup import create_db_backup
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("DbSchema")
#TODO change datetime_updated to last_updated, and use a unix timestamp
#TODO change aggregate_value to total value for consistency
#TODO check the add a guild field to verify that the server is the same 
#NOTE ISO8601 applies to both (YYYY-MM-DD HH:MM:SS) and (YYYY-MM-DD)! keys should be named according to below
# # (YYYY-MM-DD HH:MM:SS) objects should include 'datetime' in the key name
# # (YYYY-MM-DD) objects should include 'date' in the key name

db_ver = "0.2.3"  # Current schema version

# (from_version, to_version) -> migration function that mutates ``db_name`` in place.
# When no entry matches a version jump, :func:`ensure_database` remakes empty.
MigrationFn = Callable[[str], None]
def _migrate_0_2_1_to_0_2_2(db_name: str) -> None:
    """Add ``game_invites`` for DM invite tracking and slash-command joins."""
    conn = sqlite3.connect(db_name)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS game_invites (
                invite_id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                inviter_id INTEGER NOT NULL,
                dm_channel_id INTEGER,
                dm_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                datetime_created TEXT NOT NULL,
                datetime_updated TEXT,
                FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                UNIQUE (game_id, user_id)
            );"""
        )
        conn.commit()
    finally:
        conn.close()


def _migrate_0_2_2_to_0_2_3(db_name: str) -> None:
    """Add recurring auto top-role tracking and template flag."""
    conn = sqlite3.connect(db_name)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(game_templates)")}
        if "auto_top_roles" not in cols:
            conn.execute(
                "ALTER TABLE game_templates ADD COLUMN auto_top_roles INTEGER NOT NULL DEFAULT 0"
            )
        game_cols = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
        if "top_roles_applied" not in game_cols:
            conn.execute(
                "ALTER TABLE games ADD COLUMN top_roles_applied INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS template_role_holders (
                template_id INTEGER NOT NULL,
                rank INTEGER NOT NULL CHECK(rank IN (1, 2, 3)),
                user_id INTEGER NOT NULL,
                game_id TEXT NOT NULL,
                datetime_awarded TEXT NOT NULL,
                PRIMARY KEY (template_id, rank),
                FOREIGN KEY (template_id) REFERENCES game_templates (template_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE
            );"""
        )
        conn.execute("UPDATE games SET top_roles_applied = 1 WHERE status = 'ended'")
        conn.commit()
    finally:
        conn.close()


MIGRATIONS: dict[tuple[str, str], MigrationFn] = {
    ("0.2.1", "0.2.2"): _migrate_0_2_1_to_0_2_2,
    ("0.2.2", "0.2.3"): _migrate_0_2_2_to_0_2_3,
}


def _read_db_version(db_name: str) -> str | None:
    """Return ``database_info.current_version``, or None if unreadable/missing."""
    path = Path(db_name)
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    conn = sqlite3.connect(path)
    try:
        has_info = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='database_info'"
        ).fetchone()
        if not has_info:
            return None
        row = conn.execute(
            "SELECT current_version FROM database_info LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _set_db_version(db_name: str, version: str) -> None:
    """Update ``database_info.current_version`` after a successful migration."""
    sql = SqlHelper(db_name)
    info = sql.get(table="database_info")
    if info.status == "error" and info.reason == "NO ROWS RETURNED":
        sql.insert(
            table="database_info",
            items={
                "database_name": db_name,
                "original_version": version,
                "current_version": version,
                "datetime_created": _iso8601(),
            },
        )
        return
    sql.update(
        table="database_info",
        items={"current_version": version, "last_updated": _iso8601()},
        filters={"database_name": db_name},
    )


def _unlink_db_files(db_name: str) -> None:
    path = Path(db_name)
    path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def remake_db_on_mismatch(
    db_name: str,
    db_current_ver: str = db_ver,
    *,
    force: bool = False,
) -> str | None:
    """Keep the DB only when versions match; otherwise backup then remake empty schema.

    Returns the remake backup path when a remake ran, else None.
    Prefer :func:`ensure_database` at bot startup (migrate when possible).
    """
    path = Path(db_name)
    if not path.is_file() or path.stat().st_size <= 0:
        create(db_name, upgrade=False)
        return None

    current = _read_db_version(db_name)
    if current == db_current_ver and not force:
        return None

    old_label = (current or "unknown").replace("/", "_")
    new_label = db_current_ver.replace("/", "_")
    label = f"{old_label}-to-{new_label}"
    backup = create_db_backup(db_name, kind="remake", label=label)
    logger.warning(
        "DB version mismatch (found=%s, expected=%s); remaking empty schema. Backup: %s",
        current,
        db_current_ver,
        backup,
    )
    _unlink_db_files(db_name)
    create(db_name, upgrade=False)
    return str(backup) if backup else None


def upgrade_db(db_name: str, db_current_ver: str = db_ver, force_upgrade: bool = False):
    """Deprecated alias. Prefer :func:`ensure_database`."""
    return remake_db_on_mismatch(db_name, db_current_ver, force=force_upgrade)


def ensure_database(db_name: str, *, target_version: str = db_ver) -> str:
    """Ensure ``db_name`` exists at ``target_version``.

    Returns one of: ``created``, ``unchanged``, ``migrated``, ``remade``.

    * Missing / empty file → create current schema.
    * Matching version → ensure tables exist (``CREATE IF NOT EXISTS``).
    * Mismatch with a registered ``MIGRATIONS[(from, to)]`` entry → backup, migrate, stamp version.
    * Mismatch with no migration → backup and remake empty schema.
    """
    db_path = Path(db_name)
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.is_file() or db_path.stat().st_size <= 0:
        create(db_name, upgrade=False)
        logger.info("Created database %s at schema %s", db_name, target_version)
        return "created"

    current = _read_db_version(db_name)
    if current == target_version:
        create(db_name, upgrade=False)
        return "unchanged"

    migrator = MIGRATIONS.get((current or "", target_version))
    if migrator is not None:
        old_label = (current or "unknown").replace("/", "_")
        new_label = target_version.replace("/", "_")
        backup = create_db_backup(
            db_name, kind="remake", label=f"{old_label}-to-{new_label}"
        )
        logger.info(
            "Migrating database %s from %s → %s (backup: %s)",
            db_name,
            current,
            target_version,
            backup,
        )
        migrator(db_name)
        create(db_name, upgrade=False)  # pick up any new CREATE IF NOT EXISTS tables
        _set_db_version(db_name, target_version)
        return "migrated"

    remake_db_on_mismatch(db_name, target_version, force=True)
    return "remade"


def create(db_name:str, upgrade:bool=True):
    """Create database schema tables.

    Version: 0.2.3

    Args:
        db_name (str): Database name
        upgrade (bool, optional): When True and the on-disk version differs from
            ``db_ver``, run :func:`ensure_database` (migrate or remake). Defaults to True.

    # Changelog

    ## [0.2.3] - 2026-08-19
    ### Added
    - ``auto_top_roles`` on game_templates
    - ``top_roles_applied`` on games
    - ``template_role_holders`` table for recurring top-3 Discord roles

    ## [0.2.2] - 2026-08-16
    ### Added
    - ``game_invites`` table for pending DM game invites

    ## [0.2.1] - 2026-08-05
    ### Removed
    - ``name`` (custom team name) column on game_participants

    ## [0.2.0] - 2026-08-05
    ### Added
    - ``push_leaderboard``, ``leaderboard_channel_id`` on game_templates
    - ``leaderboard_message_id`` on games
    - ``days_in_first`` on game_participants
    - ``leaderboard_day_snapshots`` table
    ### Changed
    - Version mismatch remakes empty schema (backup first) unless a migration is registered

    ## [0.1.1] / [0.1.0] - 2025-06-27
    See git history for older changelog entries.
    """
    db_path = Path(db_name)
    if db_path.parent != Path('.'):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # Migrate-or-remake before CREATE IF NOT EXISTS (avoids mixed old/new schemas).
    if upgrade and db_path.is_file() and db_path.stat().st_size > 0:
        current = _read_db_version(db_name)
        if current != db_ver:
            ensure_database(db_name)
            return

    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;") # Enable foreign key constraint enforcement (important for data integrity (According to Gemini))
    
    # Permissions/roles
    # Will allow for discord role permissions instead of what we have now.
    if False:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            role_id INTEGER PRIMARY KEY,  -- Unique ID (EG: Discord role ID)
            role_name TEXT DEFAULT NULL,                -- User display name
            source TEXT NOT NULL,                       -- role source
            datetime_created TEXT NOT NULL,             -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        );""")
    
    # Meta table (store things like the database version)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS database_info (
        database_name TEXT PRIMARY KEY,             -- Database 
        original_version TEXT NOT NULL,             -- Orginal database version
        current_version TEXT NOT NULL,              
        datetime_created TEXT NOT NULL,             -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        last_updated TEXT DEFAULT NULL              -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        );""")

    # Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,                -- Unique ID (EG: Discord user ID)
        display_name TEXT,                          -- User display name
        source TEXT NOT NULL,                       -- User source
        overall_wins INT DEFAULT 0,                 -- First place finishes
        change_dollars REAL DEFAULT NULL,           -- Overall gain/loss in dollars
        change_percent REAL DEFAULT NULL,           -- Overall gain/loss percent
        permissions INT NOT NULL DEFAULT 210,       -- Store users permissions
        datetime_created TEXT NOT NULL,             -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        last_updated TEXT DEFAULT NULL              -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        );""")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_registered_user_ids ON users(user_id);") # All user IDs
    
    
    # TEMPLATES
    cursor.execute("""CREATE TABLE IF NOT EXISTS game_templates (
        template_id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_name TEXT NOT NULL,
        template_description TEXT DEFAULT NULL,
        game_name TEXT NOT NULL UNIQUE,
        game_description TEXT DEFAULT NULL,
        status TEXT NOT NULL DEFAULT 'enabled',               -- Whether to create the game or not
        owner_user_id INTEGER NOT NULL,                       -- User_ID who created the game 
        start_money REAL NOT NULL CHECK(start_money > 0),     -- Set starting money, value is in USD (Ensure positive starting amount)
        pick_count INTEGER NOT NULL CHECK(pick_count > 0),    -- Set amount of stocks each user will pick (Ensure positive number of stocks)
        pick_date INTEGER DEFAULT NULL,                       -- Days before or after start of month that picks must be in by. Negative values for after start of month. If NULL, players can join at anytime
        draft_mode BOOLEAN DEFAULT 0,                         -- When enabled, each stock can only be picked once per game.  Pick date must be on or before start date to allow this
        private_game BOOLEAN DEFAULT 0,                       -- When enabled, players must be approved to join.
        allow_selling BOOLEAN DEFAULT 0,                      -- When enabled, users can sell mid-game
        update_frequency TEXT NOT NULL DEFAULT 'alpaca',      -- Price update tag: 'alpaca', 'daily', 'hourly', 'minute', 'realtime'
        start_date TEXT NOT NULL,                             -- Game start date ISO8601 (YYYY-MM-DD). Everything else will be calculated off of this first creation date
        create_days_in_advance INTEGER NOT NULL DEFAULT 0,    -- How many days before the start should it be created
        recurring_period INTEGER NOT NULL DEFAULT 1,          -- How often should the game be created (in months)
        game_length INTEGER DEFAULT 1,                        -- How many months should the game last. 0 = infinite game
        push_leaderboard INTEGER NOT NULL DEFAULT 0,          -- Auto-push leaderboard image to a channel
        leaderboard_channel_id TEXT DEFAULT NULL,             -- Discord channel snowflake as text
        auto_top_roles INTEGER NOT NULL DEFAULT 0,          -- Auto-assign 1st/2nd/3rd roles when each game ends
        datetime_created TEXT NOT NULL,                       -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        last_updated TEXT DEFAULT NULL,                       -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        
        FOREIGN KEY (owner_user_id) REFERENCES users (user_id)
        );""")
    
    # Games table 
    cursor.execute("""CREATE TABLE IF NOT EXISTS games (
        game_id TEXT PRIMARY KEY,
        template_id DEFAULT NULL,                             -- Track games created from template
        name TEXT NOT NULL UNIQUE,
        description TEXT DEFAULT NULL,
        owner_user_id INTEGER NOT NULL,                       -- User_ID who created the game 
        start_money REAL NOT NULL CHECK(start_money > 0),     -- Set starting money, value is in USD (Ensure positive starting amount)
        pick_count INTEGER NOT NULL CHECK(pick_count > 0),    -- Set amount of stocks each user will pick (Ensure positive number of stocks)
        pick_date TEXT DEFAULT NULL,                          -- Buy/pick deadline YYYY-MM-DD. If NULL, players can buy anytime
        draft_mode BOOLEAN DEFAULT 0,                         -- When enabled, each stock can only be picked once per game.  Pick date must be on or before start date to allow this
        private_game BOOLEAN DEFAULT 0,                       -- When enabled, players must be approved to join.
        allow_selling BOOLEAN DEFAULT 0,                      -- When enabled, users can sell mid-game
        update_frequency TEXT NOT NULL DEFAULT 'alpaca',      -- Price update tag: 'alpaca', 'daily', 'hourly', 'minute', 'realtime'
        start_date TEXT NOT NULL,                             -- Game start date ISO8601 (YYYY-MM-DD)
        end_date TEXT,                                        -- OPTIONAL Game end date ISO8601 (YYYY-MM-DD)
        status TEXT NOT NULL DEFAULT 'open',                  -- Game status ('open', 'active', 'ended')
        aggregate_value REAL,                                 -- Combined value of all users
        change_dollars REAL DEFAULT NULL,
        change_percent REAL DEFAULT NULL,
        leaderboard_message_id TEXT DEFAULT NULL,             -- Comma-separated Discord message snowflakes for push page edits
        top_roles_applied INTEGER NOT NULL DEFAULT 0,         -- Recurring auto top-role processing done
        datetime_created TEXT NOT NULL,                       -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        last_updated TEXT DEFAULT NULL,                       -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        
        FOREIGN KEY (template_id) REFERENCES game_templates (template_id)
        FOREIGN KEY (owner_user_id) REFERENCES users (user_id)
        );""")
    # GAME STATUS OPTIONS
    # - 'open' # Game has not yet started, can be joined
    # - 'active' # Game started, can be joined if join_late is enabled
    # - 'ended' # Game has ended, nothing can be done


    # Stocks table 
    #TODO mark stocks as active/inactive
    cursor.execute("""CREATE TABLE IF NOT EXISTS stocks (
        stock_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,           -- Stock ticker
        exchange TEXT NOT NULL,         -- Stock exchange that it is listed on should alwaws be lowercase
        company_name TEXT,              -- Optional?
        
        UNIQUE (ticker)
        );""")

    # Stock price (current and historical) table
    #TODO add price type (daily, hourly, etc)
    cursor.execute("""CREATE TABLE IF NOT EXISTS stock_prices (
        price_id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER NOT NULL,
        price REAL NOT NULL,           -- Closing price of stock
        datetime TEXT NOT NULL,      -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        
        FOREIGN KEY (stock_id) REFERENCES stocks (stock_id) ON DELETE CASCADE,  -- When a ticker is deleted from the main table, all references to it will also be deleted?
        
        UNIQUE (stock_id, datetime)                                           -- Ensure only one price per stock per day
        );""")

    # Game participants table (track who is in which leagues/games)
    cursor.execute("""CREATE TABLE IF NOT EXISTS game_participants (
        participation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        game_id TEXT NOT NULL,
        status TEXT DEFAULT 'active',           -- A participant (player) status.  Can be 'pending', 'active', 'inactive'.  Pending will be used if a player tries to join a private game
        datetime_joined TEXT NOT NULL,          -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        current_value REAL DEFAULT NULL,        -- Current portfolio value
        change_dollars REAL DEFAULT NULL,
        change_percent REAL DEFAULT NULL,
        days_in_first INTEGER NOT NULL DEFAULT 0, -- Days ended as #1 (NYSE close snapshots)
        last_updated TEXT DEFAULT NULL,         -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
        FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE,
        
        UNIQUE (user_id, game_id) -- A user can only join a specific game once
        );""")

    # Stock picks table.  Store a users stock picks for their game(s).  Buy date not needed since game_participants join date can be used
    cursor.execute("""CREATE TABLE IF NOT EXISTS stock_picks (
        pick_id INTEGER PRIMARY KEY AUTOINCREMENT,
        participation_id INTEGER NOT NULL,                 -- Reference the game 
        stock_id INTEGER NOT NULL,
        shares REAL DEFAULT NULL,                          -- Amount of shares held
        start_value REAL DEFAULT NULL,                     -- Start value of shares
        current_value REAL DEFAULT NULL,                   -- Current value of shares
        change_dollars REAL DEFAULT NULL,
        change_percent REAL DEFAULT NULL,
        status TEXT DEFAULT 'pending_buy',            -- Status of pick. Options: 'pending_buy', 'owned', 'pending_sell', 'sold'
        datetime_created TEXT NOT NULL,                       -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        last_updated TEXT DEFAULT NULL,                    -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        
        FOREIGN KEY (participation_id) REFERENCES game_participants (participation_id) ON DELETE CASCADE,
        FOREIGN KEY (stock_id) REFERENCES stocks (stock_id) ON DELETE RESTRICT, -- Don't delete a stock if picks exist? Or CASCADE? Depends on desired behavior. RESTRICT is safer.
        
        UNIQUE (participation_id, stock_id) -- User picks a specific stock only once per game participation
        );""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS game_invites (
        invite_id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        inviter_id INTEGER NOT NULL,
        dm_channel_id INTEGER,
        dm_message_id INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        datetime_created TEXT NOT NULL,
        datetime_updated TEXT,
        FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
        UNIQUE (game_id, user_id)
        );""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS template_role_holders (
        template_id INTEGER NOT NULL,
        rank INTEGER NOT NULL CHECK(rank IN (1, 2, 3)),
        user_id INTEGER NOT NULL,
        game_id TEXT NOT NULL,
        datetime_awarded TEXT NOT NULL,
        PRIMARY KEY (template_id, rank),
        FOREIGN KEY (template_id) REFERENCES game_templates (template_id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (user_id),
        FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE
        );""")

    # Idempotent "days in first" awards per NYSE trade date
    cursor.execute("""CREATE TABLE IF NOT EXISTS leaderboard_day_snapshots (
        game_id TEXT NOT NULL,
        trade_date TEXT NOT NULL,               -- ISO8601 (YYYY-MM-DD) ET trade date
        first_user_id INTEGER NOT NULL,
        datetime_created TEXT NOT NULL,         -- ISO8601 (YYYY-MM-DD HH:MM:SS)
        PRIMARY KEY (game_id, trade_date),
        FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE,
        FOREIGN KEY (first_user_id) REFERENCES users (user_id)
        );""")

    conn.commit()
    conn.close()

    sql = SqlHelper(db_name)
    info = sql.get(table="database_info")
    if info.status == 'error' and info.reason == "NO ROWS RETURNED":
        sql.insert(
            table='database_info',
            items={
                'database_name': db_name,
                'original_version': db_ver,
                'current_version': db_ver,
                'datetime_created': _iso8601(),
            },
        )


if __name__ == "__main__":
    DB_NAME = str(os.getenv('DB_NAME'))
    print(f'DB Name is: {DB_NAME}')
    print(ensure_database(DB_NAME))
