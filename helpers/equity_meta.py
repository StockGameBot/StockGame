"""Lightweight equity metadata helpers (no extra package deps).

Company names are best-effort: Alpaca trading /assets when credentials allow,
otherwise Yahoo Finance search over plain HTTP.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from helpers.alpaca_client import AlpacaMarketData, to_alpaca_symbol, to_db_ticker

logger = logging.getLogger("EquityMeta")

YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_USER_AGENT = "StockGame/1.0 (equity-meta)"


def _clean_name(name: Optional[str], ticker: str) -> Optional[str]:
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    if cleaned.upper() == to_db_ticker(ticker).upper():
        return None
    if cleaned.upper() == to_alpaca_symbol(ticker).upper():
        return None
    return cleaned


def _yahoo_company_name(ticker: str) -> Optional[str]:
    db = to_db_ticker(ticker)
    alpaca = to_alpaca_symbol(ticker)
    try:
        response = requests.get(
            YAHOO_SEARCH_URL,
            params={"q": alpaca, "quotesCount": 8, "newsCount": 0},
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
        quotes = response.json().get("quotes") or []
    except Exception as exc:
        logger.debug("Yahoo name lookup failed for %s: %s", ticker, exc)
        return None

    wanted = {db.upper(), alpaca.upper()}
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        symbol = str(quote.get("symbol") or "").strip().upper()
        if symbol not in wanted and to_db_ticker(symbol) not in wanted:
            continue
        name = _clean_name(
            str(quote.get("longname") or quote.get("shortname") or ""),
            db,
        )
        if name:
            return name
    return None


def lookup_company_name(
    ticker: str,
    alpaca: Optional[AlpacaMarketData] = None,
) -> Optional[str]:
    """
    Best-effort company name for a ticker.

    Returns None when no distinct name is available (caller should store/show
    ticker-only).
    """
    db = to_db_ticker(ticker)
    client = alpaca or AlpacaMarketData()
    if client.configured:
        try:
            asset = client.get_us_equity(db)
            name = _clean_name(str(asset.get("name") or ""), db)
            if name:
                return name
        except Exception as exc:
            logger.debug("Alpaca asset name lookup skipped for %s: %s", db, exc)

    return _yahoo_company_name(db)


def autocomplete_label(ticker: str, company_name: Optional[str]) -> str:
    """Discord choice label: ``TICKER - Name`` or bare ``TICKER``."""
    name = _clean_name(company_name, ticker)
    if not name:
        return ticker
    return f"{ticker} - {name}"
