# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

SQLite schema versions match `db_schema.db_ver`. **Beta** began with [PR #162](https://github.com/StockGameBot/StockGame/pull/162) (2026-08-20) and ended at **1.0.0**. Everything before beta is summarized under [Alpha / development](#alpha--development).

## [Unreleased]

### Added

### Changed

### Fixed

- `/remove-stock` ticker autocomplete resolves `game_id` when omitted (single eligible game) or lists pending picks across games when several match

## [1.0.0] - 2026-08-30

First stable release after the beta line (0.2.x).

### Added

- **Join Game** button on recurring leaderboard push messages and `/leaderboard` when viewing a joinable recurring game
- Expanded `/user-stats`: best/worst pick ever, recurring 1st/2nd/3rd podiums, best recurring finish
- Fund label in my-stocks portfolio summary box (when funds are enabled and chosen)
- One-time startup fix: stress test game `LWFN6` end date **2026-08-29**

### Changed

- Open recurring games post a **join-only** announcement (embed + Join button) to the push channel until the game goes active; leaderboard images follow once play starts
- Leaderboard and portfolio images use guild nicknames with pixel-width clipping (no overlap with other columns)
- Optional `game_id` on `/buy-stock`, `/remove-stock`, `/my-stocks`, `/game-info`, and `/leave-game` when only one eligible game matches
- My-stocks **Fund** label is right-aligned in the summary box so long names stay inside the layout
- User-facing **Fund** terminology replaces Affiliation (internal schema unchanged)

### Fixed

## [0.2.8] - 2026-08-27 (Beta)

### Added

- Corporate action handling (splits, renames, mergers, delistings) via Alpaca date-only CA poll; two-phase stage (9:00 ET) / apply (9:30 ET)
- Schema 0.2.8: `stocks.trade_status`, `stock_picks.event_label`, `staged_corporate_actions`, `applied_corporate_actions`
- Split-price retry loop for post-open trades; portfolio red event badges for CA labels
- Market-aligned update schedule (9:15 pre-open through 16:15 post-close, 15-min grid)
- One-time IMCC 2026-08-27 reverse-split repair on bot startup

### Changed

- Alpaca snapshot batch size raised to 500; price polling gated to weekday market windows unless forced
- `pending_buy` picks settle only during market hours
- `/buy-stock` rejects delisted and Alpaca-inactive/untradable tickers with clear errors
- New tickers require Alpaca `active` + `tradable` asset status (not just a stale snapshot price)

### Fixed

- Split-day phantom gains when share count was not adjusted before revaluation
- Players could buy delisted/inactive symbols (e.g. ZEUS, BAD) that only had stale IEX snapshot prices

## [0.2.7] - 2026-08-26 (Beta)

### Added

- Recurring-game **affiliations** (optional per template): Atrioc, DougDoug, Aiden, The Working Class, or Independent
- Schema: `affiliations_enabled` on `game_templates`, `affiliation` on `game_participants` (migration 0.2.6 → 0.2.7)
- Affiliation prompt after join/invite; **Choose Affiliation** on `/my-stocks`, `/leaderboard`, and `/game-info`
- Affiliation badges on the recurring leaderboard image (left of player names)
- **Overall Performance** hedge-fund table image on live leaderboard push embeds (`helpers/affiliation_performance_image.py`)
- PNG assets under `assets/affiliations/`

### Changed

- Recurring leaderboard affiliation icons: larger size, vertically centered with usernames
- Affiliation performance table: fund icons beside names (Independent has none); image width increased
- Portfolio summary **Money Left to Spend** uses unfilled pick slots (matches game cash logic), not market value
- Portfolio image: company names in stock rows, consistent `+$` / `-$` gain formatting, ticker/company box layout
- `/leaderboard` pagination: **First page** and **Last page** buttons; controls split across three rows (pages / games / actions)

### Fixed

- Portfolio image showed non-zero “money left” whenever stocks moved up or down (inverted gain/loss)
- `discord_bot.py` indentation/syntax errors that blocked imports and tests

## [0.2.6] - 2026-08-26 (Beta)

### Added

- Schema: `invalid_stocks` table — short-lived cache when Alpaca cannot resolve a ticker (migration 0.2.5 → 0.2.6)
- `/buy-stock` accepts multiple tickers in one command; shared validation and lower duplicate API work
- Tests for multi-ticker buy flow and invalid-ticker caching

### Changed

- `/buy-stock` defers once up front, uses a bounded in-memory Alpaca price cache, and trims Discord intents / member caching

## [0.2.5] - 2026-08-24 (Beta)

### Fixed

- Schema: backfill `stock_picks.start_value` when low starting-cash games rounded per-pick allocation to zero (migration 0.2.4 → 0.2.5)
- Portfolio totals could divide by zero when `start_value` was stored as zero
- Exclusive picks vs pick-deadline validation in game creation and `/create-recurring-game`
- Game autocomplete no longer surfaced games the user cannot access

## [0.2.4] - 2026-08-20 (Beta)

First beta release to main: [PR #162](https://github.com/StockGameBot/StockGame/pull/162).

### Added

- Schema: `leaderboard_final_pushed` on `games` for a one-time final-standings push when a game ends (migration 0.2.3 → 0.2.4)
- Final standings **podium** image generation and push wiring (`helpers/final_standings_podium.py`)
- Game ID autocomplete labels include a status emoji

### Fixed

- Recurring live leaderboard push regression after podium/final-standings work

### Changed

- **Share** on `/leaderboard` and `/my-stocks`: post the current image to the channel with attribution
- `/my-stocks` can show another player’s portfolio in **public** games (owner/moderator rules apply)

## [0.2.3] - 2026-08-19

Shipped during alpha; included in the first beta builds.

### Added

- Schema: `auto_top_roles` on `game_templates`, `top_roles_applied` on `games`, `template_role_holders` table (migration 0.2.2 → 0.2.3)
- Automatic 1st / 2nd / 3rd Discord role assignment for recurring templates (`helpers/recurring_top_roles.py`)
- `/manage-recurring-games` toggle for auto top roles

## [0.2.2] - 2026-08-16

### Added

- Schema: `game_invites` table for pending DM game invites (migration 0.2.1 → 0.2.2)
- Invite flow aligned between frontend and backend; in-channel fallback when DMs are closed

### Fixed

- `delete_game` permission vulnerability
- Misc database and workflow script issues

### Changed

- QoL on game creation wizard, autocomplete consolidation, Pillow image generation optimizations
- Pre-start notice on open games (picks stay pending until start date)

## [0.2.1] - 2026-08-05

### Removed

- Schema: custom team `name` column on `game_participants` (migration 0.2.0 → 0.2.1)

## [0.2.0] - 2026-08-05

### Added

- Schema: recurring **push leaderboard** (`push_leaderboard`, `leaderboard_channel_id` on templates; `leaderboard_message_id` on games)
- Schema: `days_in_first` on `game_participants`, `leaderboard_day_snapshots` table
- Live recurring leaderboard images edited in a configured channel (`helpers/leaderboard_push.py`, `helpers/recurring_leaderboard_image.py`)
- Hourly DB backups, migrate-or-remake startup via `db_schema.ensure_database()`

### Changed

- Version mismatch on an existing DB triggers backup + migration when registered, otherwise remake (with backup)

---

## Alpha / development

Work before [PR #162](https://github.com/StockGameBot/StockGame/pull/162) / the beta line. Not production-complete; see git history for detail.

### Highlights (2025-08 → 2026-08)

- Discord bot merged into the main repo; Alpaca market data; Docker Compose deployment
- Public / private games, pick deadlines, exclusive picks, pending join approval
- Recurring game templates and lifecycle (`/create-recurring-game`, `/manage-recurring-games`)
- Paginated `/leaderboard` with lazy rank-page rendering and recurring vs one-off layouts
- `/my-stocks` portfolio PNG, `/game-info`, `/game-list`, `/my-games`, kicks, invites, moderators
- Async command handlers, expanded test suite, CI workflows
- Schema versions **0.1.x** (legacy participant/game fields, price updates)

### [0.1.0] / [0.1.1] - 2025-06-27

- Early SQLite schema and migration scaffolding

### [0.0.1] - 2025-04-29

- Initial changelog, readme, and Discord bot framework
- Backend renamed from `StockGame` to `Backend`; core CRUD for users, games, stocks, picks
- SQLite helper refactor (`sqlhelper.py`), basic validation

### [0.0.0] - Template

- Project scaffold
