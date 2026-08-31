"""Recurring-game hedge-fund affiliations - constants, icons, and stats."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from PIL import Image

# Canonical affiliation keys stored in game_participants.affiliation
AFFILIATION_ATRIOC = "atrioc"
AFFILIATION_DOUGDOUG = "dougdoug"
AFFILIATION_AIDEN = "aiden"
AFFILIATION_WORKING_CLASS = "working_class"
INDEPENDENT_KEY = "independent"

AFFILIATION_KEYS: tuple[str, ...] = (
    AFFILIATION_ATRIOC,
    AFFILIATION_DOUGDOUG,
    AFFILIATION_AIDEN,
    AFFILIATION_WORKING_CLASS,
)

AFFILIATION_DISPLAY: dict[str, str] = {
    AFFILIATION_ATRIOC: "Atrioc",
    AFFILIATION_DOUGDOUG: "DougDoug",
    AFFILIATION_AIDEN: "Aiden",
    AFFILIATION_WORKING_CLASS: "The Working Class",
    INDEPENDENT_KEY: "Independent",
}

# Fixed order for embed hedge-fund lines
AFFILIATION_EMBED_ORDER: tuple[str, ...] = (
    AFFILIATION_ATRIOC,
    AFFILIATION_DOUGDOUG,
    AFFILIATION_AIDEN,
    AFFILIATION_WORKING_CLASS,
    INDEPENDENT_KEY,
)

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "affiliations"
_ICON_CACHE: dict[tuple[str, int], Image.Image] = {}

AFFILIATION_WARNING = (
    "**Important:** Your **fund choice** is permanent once selected and **cannot be changed** "
    "after the game has started.\n"
    "If you stay unassigned, you may still pick a fund mid-game — but you **cannot switch funds** afterward."
)


def format_dollar_gain(amount: float) -> str:
    """Format dollar gain/loss with sign before the currency symbol (+$1.00 / -$1.00)."""
    value = float(amount or 0)
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def normalize_affiliation(value: str | None) -> str | None:
    """Return a canonical affiliation key or None for Independent."""
    if value is None or str(value).strip() == "":
        return None
    key = str(value).strip().lower()
    if key in ("none", "independent", "null"):
        return None
    if key not in AFFILIATION_KEYS:
        raise ValueError(f"Invalid affiliation: {value}")
    return key


def hedge_fund_name(key: str | None) -> str:
    """Display name for embed copy, e.g. 'The Atrioc Hedge Fund'."""
    label = AFFILIATION_DISPLAY.get(key or INDEPENDENT_KEY, "Independent")
    return f"The {label} Hedge Fund"


def format_hedge_fund_line(dollars: float, percent: float, *, final: bool = False) -> str:
    """Format one hedge-fund performance line for Discord embeds."""
    direction = "was" if final else "is"
    movement = "up" if dollars >= 0 else "down"
    return (
        f"{direction} {movement} **${dollars:+,.2f}** (**{percent:+.2f}%**) this month."
    )


def format_hedge_fund_block(
    stats: dict[str, dict[str, float]],
    *,
    final: bool = False,
) -> str:
    """Build the multi-line hedge-fund section for push embeds."""
    lines: list[str] = []
    for key in AFFILIATION_EMBED_ORDER:
        row = stats.get(key, {"dollars": 0.0, "percent": 0.0})
        name = hedge_fund_name(None if key == INDEPENDENT_KEY else key)
        perf = format_hedge_fund_line(row["dollars"], row["percent"], final=final)
        lines.append(f"{name} {perf}")
    return "\n".join(lines)


def aggregate_affiliation_stats(
    participants: Sequence[Any],
    start_money: float,
) -> dict[str, dict[str, float]]:
    """Sum portfolio performance by affiliation group."""
    buckets: dict[str, dict[str, float]] = {
        key: {"current": 0.0, "start": 0.0, "members": 0} for key in AFFILIATION_EMBED_ORDER
    }
    for participant in participants:
        status = getattr(participant, "status", "active")
        if status not in ("active", "pending"):
            continue
        raw = getattr(participant, "affiliation", None)
        key = raw if raw in AFFILIATION_KEYS else INDEPENDENT_KEY
        current = float(getattr(participant, "current_value", 0) or 0)
        buckets[key]["current"] += current
        buckets[key]["start"] += float(start_money)
        buckets[key]["members"] += 1

    result: dict[str, dict[str, float]] = {}
    for key, totals in buckets.items():
        start = totals["start"]
        current = totals["current"]
        dollars = current - start
        percent = (dollars / start * 100) if start > 0 else 0.0
        result[key] = {
            "dollars": dollars,
            "percent": percent,
            "members": int(totals["members"]),
        }
    return result


def is_affiliations_enabled(be, game) -> bool:
    """True when recurring game has affiliations enabled on its template."""
    template_id = getattr(game, "template_id", None)
    if template_id is None:
        return False
    try:
        template = be.get_game_template(int(template_id))
    except LookupError:
        return False
    return bool(getattr(template, "affiliations_enabled", False))


def affiliation_icon_path(key: str) -> Path:
    return _ASSETS_DIR / f"{key}.png"


def load_affiliation_icon(key: str, height: int = 20) -> Optional[Image.Image]:
    """Load and cache a resized affiliation badge (RGBA)."""
    if key not in AFFILIATION_KEYS:
        return None
    cache_key = (key, height)
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached
    path = affiliation_icon_path(key)
    if not path.is_file():
        return None
    img = Image.open(path).convert("RGBA")
    ratio = height / img.height
    width = max(1, int(img.width * ratio))
    resized = img.resize((width, height), Image.Resampling.LANCZOS)
    _ICON_CACHE[cache_key] = resized
    return resized


def clear_icon_cache() -> None:
    """Drop cached icons (tests)."""
    _ICON_CACHE.clear()
