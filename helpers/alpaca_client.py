"""Alpaca market-data helpers (stocks only — no crypto)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger("AlpacaMarketData")

DATA_BASE = "https://data.alpaca.markets/v2"
DEFAULT_TRADING_BASE = "https://paper-api.alpaca.markets"
BATCH_SIZE = 100
SLEEP_BETWEEN_BATCHES = 0.35  # ~170 req/min max, under free-tier 200/min


def to_alpaca_symbol(ticker: str) -> str:
    """Map DB tickers (BRK-B) to Alpaca symbols (BRK.B)."""
    return ticker.strip().upper().replace("-", ".")


def to_db_ticker(ticker: str) -> str:
    """Normalize a ticker for DB storage (Alpaca BRK.B → BRK-B)."""
    return ticker.strip().upper().replace(".", "-")


def price_from_snapshot(snap: dict[str, Any]) -> Optional[float]:
    trade = snap.get("latestTrade") or {}
    if trade.get("p") is not None:
        return float(trade["p"])
    quote = snap.get("latestQuote") or {}
    ap, bp = quote.get("ap"), quote.get("bp")
    if ap is not None and bp is not None and float(ap) > 0 and float(bp) > 0:
        return (float(ap) + float(bp)) / 2
    if ap is not None and float(ap) > 0:
        return float(ap)
    if bp is not None and float(bp) > 0:
        return float(bp)
    bar = snap.get("dailyBar") or snap.get("prevDailyBar") or {}
    if bar.get("c") is not None:
        return float(bar["c"])
    return None


class AlpacaMarketData:
    """Synchronous Alpaca client for equity assets, snapshots, and market clock."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        trading_base: Optional[str] = None,
    ):
        self.api_key = (api_key if api_key is not None else os.getenv("ALPACA_API_KEY", "")).strip()
        self.secret_key = (
            secret_key if secret_key is not None else os.getenv("ALPACA_SECRET_KEY", "")
        ).strip()
        base = (
            trading_base
            if trading_base is not None
            else os.getenv("ALPACA_BASE_URL", DEFAULT_TRADING_BASE)
        )
        self.trading_base = (base or DEFAULT_TRADING_BASE).rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Accept": "application/json",
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _require_configured(self) -> None:
        if not self.configured:
            raise RuntimeError("Alpaca credentials missing (ALPACA_API_KEY / ALPACA_SECRET_KEY)")

    def _get(self, url: str, params: Optional[dict] = None) -> requests.Response:
        r = self._session.get(url, params=params, timeout=30)
        if r.status_code == 429:
            retry = float(r.headers.get("Retry-After", "5"))
            logger.warning("Alpaca rate limited; sleeping %.0fs", retry)
            time.sleep(retry)
            r = self._session.get(url, params=params, timeout=30)
        return r

    def is_market_open(self) -> Optional[bool]:
        """Return True/False from Alpaca clock, or None if the call fails."""
        if not self.configured:
            return None
        try:
            r = self._get(f"{self.trading_base}/v2/clock")
            r.raise_for_status()
            return bool(r.json().get("is_open"))
        except Exception:
            logger.exception("Failed to read Alpaca market clock")
            return None

    def get_us_equity(self, ticker: str) -> dict[str, Any]:
        """
        Look up a US equity asset on Alpaca's trading API.

        Raises:
            LookupError: Symbol not found.
            ValueError: Not an active tradable US equity (e.g. crypto).
            RuntimeError: Missing credentials or trading API unauthorized/unavailable.
        """
        self._require_configured()
        symbol = to_alpaca_symbol(ticker)
        r = self._get(f"{self.trading_base}/v2/assets/{quote(symbol, safe='')}")
        if r.status_code == 404:
            raise LookupError(f"Unable to find stock: {ticker}")
        if r.status_code in (401, 403):
            raise RuntimeError(
                f"Alpaca trading API unauthorized ({r.status_code}); "
                "market-data keys cannot use /v2/assets"
            )
        r.raise_for_status()
        asset = r.json()
        if not isinstance(asset, dict):
            raise LookupError(f"Unable to find stock: {ticker}")

        asset_class = str(asset.get("class") or "")
        if asset_class != "us_equity":
            raise ValueError("Stock is not tradeable")
        if asset.get("status") != "active" or not asset.get("tradable"):
            raise ValueError("Stock is not tradeable")

        return asset

    def equity_is_priced(self, ticker: str) -> bool:
        """True if market data returns a usable price for this symbol."""
        self._require_configured()
        prices = self.get_latest_prices([to_db_ticker(ticker)])
        return to_db_ticker(ticker) in prices

    def fetch_snapshots(self, symbols: list[str]) -> dict[str, Any]:
        """Fetch IEX snapshots for a batch of Alpaca symbols."""
        if not symbols:
            return {}
        params = {"symbols": ",".join(symbols), "feed": "iex"}
        r = self._get(f"{DATA_BASE}/stocks/snapshots", params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    def lookup_equity_price(
        self,
        ticker: str,
    ) -> tuple[Optional[float], Literal["found", "not_found", "unavailable"]]:
        """Look up a single equity price without batch side effects.

        Returns ``(price, status)`` where status distinguishes a missing symbol
        from transient API failures.
        """
        self._require_configured()
        db_ticker = to_db_ticker(ticker)
        alpaca_sym = to_alpaca_symbol(db_ticker)
        data = self._fetch_snapshots_with_retries([alpaca_sym], attempts=3)
        if data is None:
            return None, "unavailable"
        snap = data.get(alpaca_sym)
        price = price_from_snapshot(snap) if isinstance(snap, dict) else None
        if price is None:
            return None, "not_found"
        return price, "found"

    def get_latest_prices(self, tickers: list[str]) -> dict[str, float]:
        """
        Return {db_ticker: price} for every requested ticker that Alpaca can price.

        Batches requests under free-tier limits. Failed batches are retried, then
        any still-missing symbols are fetched individually so a single bad
        response cannot drop the rest of the universe.
        """
        self._require_configured()
        if not tickers:
            return {}

        # Preserve original DB ticker spelling while querying Alpaca form.
        alpaca_to_db: dict[str, str] = {}
        ordered_alpaca: list[str] = []
        for ticker in tickers:
            alpaca = to_alpaca_symbol(ticker)
            if alpaca in alpaca_to_db:
                continue
            alpaca_to_db[alpaca] = ticker
            ordered_alpaca.append(alpaca)

        prices: dict[str, float] = {}
        unresolved: list[str] = []

        for i in range(0, len(ordered_alpaca), BATCH_SIZE):
            batch = ordered_alpaca[i : i + BATCH_SIZE]
            data = self._fetch_snapshots_with_retries(batch, attempts=3)
            if data is None:
                unresolved.extend(batch)
            else:
                for alpaca_sym in batch:
                    snap = data.get(alpaca_sym)
                    price = price_from_snapshot(snap) if isinstance(snap, dict) else None
                    if price is None:
                        unresolved.append(alpaca_sym)
                    else:
                        prices[alpaca_to_db[alpaca_sym]] = price

            if i + BATCH_SIZE < len(ordered_alpaca):
                time.sleep(SLEEP_BETWEEN_BATCHES)

        # Second pass: never leave a ticker unchecked after a batch miss/failure.
        still_missing: list[str] = []
        for alpaca_sym in unresolved:
            if alpaca_to_db[alpaca_sym] in prices:
                continue
            data = self._fetch_snapshots_with_retries([alpaca_sym], attempts=3)
            if data is None:
                still_missing.append(alpaca_sym)
                continue
            snap = data.get(alpaca_sym)
            price = price_from_snapshot(snap) if isinstance(snap, dict) else None
            if price is None:
                still_missing.append(alpaca_sym)
            else:
                prices[alpaca_to_db[alpaca_sym]] = price
            time.sleep(SLEEP_BETWEEN_BATCHES)

        if still_missing:
            missing_db = [alpaca_to_db[s] for s in still_missing]
            logger.error(
                "Alpaca price fetch incomplete after retries: %s/%s tickers missing: %s",
                len(missing_db),
                len(ordered_alpaca),
                ", ".join(missing_db[:50]) + ("..." if len(missing_db) > 50 else ""),
            )

        return prices

    def _fetch_snapshots_with_retries(
        self, symbols: list[str], *, attempts: int = 3
    ) -> Optional[dict[str, Any]]:
        """Fetch snapshots, retrying on HTTP/network errors. Returns None if all attempts fail."""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                return self.fetch_snapshots(symbols)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Alpaca snapshot fetch failed (attempt %s/%s, symbols=%s): %s",
                    attempt,
                    attempts,
                    len(symbols),
                    exc,
                )
                time.sleep(SLEEP_BETWEEN_BATCHES * attempt)
        if last_exc is not None:
            logger.exception(
                "Alpaca snapshot fetch exhausted retries for %s symbol(s)",
                len(symbols),
                exc_info=last_exc,
            )
        return None
