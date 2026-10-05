"""Text leaderboard of stocks players have picked in one game."""

from __future__ import annotations

from dataclasses import dataclass

from helpers.affiliations import AFFILIATION_DISPLAY, INDEPENDENT_KEY, normalize_affiliation

STOCK_BOARD_PAGE_SIZE = 10
# Neighboring picks of the same ticker join one row when this close.
# 9.61, 10.10, and 10.59 chain together because each step is within 0.5 points.
PERCENT_GROUP_GAP = 0.5
_FIELD_NAME_LIMIT = 256
_FIELD_VALUE_LIMIT = 1024


@dataclass(frozen=True)
class PricedPick:
    ticker: str
    company_name: str | None
    change_percent: float
    user_id: int
    affiliation: str | None


@dataclass(frozen=True)
class StockBoardEntry:
    ticker: str
    company_name: str | None
    low_percent: float
    high_percent: float
    holders: tuple[tuple[int, str | None], ...]

    @property
    def grouped(self) -> bool:
        return len(self.holders) > 1


def fund_label(affiliation: str | None) -> str:
    """Display name for a stored affiliation, including Independent."""
    try:
        key = normalize_affiliation(affiliation)
    except ValueError:
        key = None
    return AFFILIATION_DISPLAY.get(key or INDEPENDENT_KEY, "Independent")


def build_stock_board(picks: list[PricedPick] | tuple[PricedPick, ...]) -> list[StockBoardEntry]:
    """Group same-ticker picks whose percentages chain within 0.5 points.

    Picks are sorted by percent, and a new row starts when the next pick is
    more than 0.5 points away from the previous one. Rows are ordered from
    the strongest high to the weakest.
    """
    by_ticker: dict[str, list[PricedPick]] = {}
    for pick in picks:
        ticker = (pick.ticker or "").strip().upper()
        if not ticker:
            continue
        by_ticker.setdefault(ticker, []).append(pick)

    entries: list[StockBoardEntry] = []
    for ticker, rows in by_ticker.items():
        ordered = sorted(rows, key=lambda row: (row.change_percent, row.user_id))
        cluster: list[PricedPick] = []
        for row in ordered:
            if cluster and row.change_percent - cluster[-1].change_percent > PERCENT_GROUP_GAP + 1e-9:
                entries.append(_entry_from_cluster(ticker, cluster))
                cluster = []
            cluster.append(row)
        if cluster:
            entries.append(_entry_from_cluster(ticker, cluster))

    entries.sort(key=lambda entry: (-entry.high_percent, entry.ticker, -entry.low_percent))
    return entries


def format_stock_board_field(
    entry: StockBoardEntry,
    rank: int,
    *,
    picker_labels: dict[int, str] | None = None,
) -> tuple[str, str]:
    """Return the embed field name and value for one board row."""
    percent = _format_percent_span(entry.low_percent, entry.high_percent)
    name = _fit_field_name(rank, entry.ticker, entry.company_name, percent)
    detail = _picked_by_line(entry, picker_labels=picker_labels)
    return name, detail


def _entry_from_cluster(ticker: str, cluster: list[PricedPick]) -> StockBoardEntry:
    company = next((row.company_name.strip() for row in cluster if row.company_name and row.company_name.strip()), None)
    ranked = sorted(cluster, key=lambda row: (-row.change_percent, row.user_id))
    seen: set[int] = set()
    holders: list[tuple[int, str | None]] = []
    for row in ranked:
        if row.user_id in seen:
            continue
        seen.add(row.user_id)
        holders.append((row.user_id, row.affiliation))
    percents = [row.change_percent for row in cluster]
    return StockBoardEntry(
        ticker=ticker,
        company_name=company,
        low_percent=min(percents),
        high_percent=max(percents),
        holders=tuple(holders),
    )


def _format_percent_span(low: float, high: float) -> str:
    if f"{low:.2f}" == f"{high:.2f}":
        return f"{low:+.2f}%"
    return f"{low:+.2f}% to {high:+.2f}%"


def _fit_field_name(rank: int, ticker: str, company: str | None, percent: str) -> str:
    separator = " • "
    prefix = f"#{rank} {ticker}"
    suffix = f"{separator}{percent}"
    if company:
        room = _FIELD_NAME_LIMIT - len(prefix) - len(suffix) - len(separator)
        if room >= 1:
            shown = company if len(company) <= room else company[: room - 1].rstrip() + "…"
            text = f"{prefix}{separator}{shown}{suffix}"
        else:
            text = f"{prefix}{suffix}"
    else:
        text = f"{prefix}{suffix}"
    return text[:_FIELD_NAME_LIMIT]


def _picker_display(user_id: int, picker_labels: dict[int, str] | None) -> str:
    if picker_labels and user_id in picker_labels:
        return picker_labels[user_id]
    return f"<@{user_id}>"


def _picked_by_line(
    entry: StockBoardEntry,
    *,
    picker_labels: dict[int, str] | None = None,
) -> str:
    fund = None if entry.grouped else fund_label(entry.holders[0][1])
    suffix = f" Fund: {fund}" if fund else ""
    mentions = [
        _picker_display(user_id, picker_labels)
        for user_id, _affiliation in entry.holders
    ]
    line = "Picked by: " + " ".join(mentions) + suffix
    if len(line) <= _FIELD_VALUE_LIMIT:
        return line

    budget = _FIELD_VALUE_LIMIT - len(suffix)
    kept: list[str] = []
    for index, mention in enumerate(mentions):
        remaining = len(mentions) - index
        extra = mention if not kept else " " + mention
        reserve = len(f" and {remaining} more") if remaining > 1 else 0
        if len("Picked by: " + " ".join(kept) + extra) + reserve > budget and kept:
            break
        kept.append(mention)
    omitted = len(mentions) - len(kept)
    text = "Picked by: " + " ".join(kept)
    if omitted:
        text += f" and {omitted} more"
    return (text + suffix)[:_FIELD_VALUE_LIMIT]
