"""Lightweight news/event fetcher for traded symbols."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

# CryptoPanic API — free tier: 5 requests/minute
# Docs: https://cryptopanic.com/developers/api/
_CRYPTOPANIC_BASE = "https://cryptopanic.com/api/free/v1/posts/"


@dataclass(frozen=True)
class NewsEvent:
    """A single news event relevant to a symbol."""

    symbol: str
    title: str
    source: str
    published_at: datetime
    sentiment: str          # "bullish", "bearish", "neutral"
    severity: str           # "low", "medium", "high"
    url: str = ""


class CryptoPanicClient:
    """Fetch recent crypto news from CryptoPanic API.

    Requires a free API key from https://cryptopanic.com/developers/api/
    Set via environment variable CRYPTOPANIC_API_KEY.
    """

    def __init__(self, api_key: str, timeout: int = 10) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def fetch_recent(
        self,
        symbols: list[str] | None = None,
        max_results: int = 20,
    ) -> list[NewsEvent]:
        """Fetch recent news, optionally filtered by symbol tickers.

        Parameters
        ----------
        symbols : list[str] | None
            E.g. ["BTC", "ETH"]. Pass None for all crypto news.
        max_results : int
            Maximum events to return.

        Returns
        -------
        list[NewsEvent]
            Parsed events sorted by recency.
        """
        params: dict[str, Any] = {
            "auth_token": self._api_key,
            "kind": "news",
            "filter": "important",  # Only important news
            "public": "true",
        }
        if symbols:
            # CryptoPanic uses base tickers (BTC, not BTCUSDT)
            params["currencies"] = ",".join(symbols)

        try:
            resp = requests.get(
                _CRYPTOPANIC_BASE, params=params, timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("CryptoPanic fetch failed: %s", e)
            return []

        events: list[NewsEvent] = []
        for item in (data.get("results") or [])[:max_results]:
            # Map CryptoPanic votes to sentiment
            votes = item.get("votes", {})
            positive = votes.get("positive", 0) + votes.get("liked", 0)
            negative = votes.get("negative", 0) + votes.get("disliked", 0)

            if positive > negative * 2:
                sentiment = "bullish"
            elif negative > positive * 2:
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            # Severity based on vote count
            total_votes = positive + negative
            if total_votes >= 20:
                severity = "high"
            elif total_votes >= 5:
                severity = "medium"
            else:
                severity = "low"

            # Map currencies to USDT pairs
            currencies = item.get("currencies") or []
            for curr in currencies:
                code = curr.get("code", "").upper()
                usdt_symbol = f"{code}USDT"
                events.append(NewsEvent(
                    symbol=usdt_symbol,
                    title=item.get("title", ""),
                    source=item.get("source", {}).get("title", "unknown"),
                    published_at=datetime.fromisoformat(
                        item.get("published_at", "").replace("Z", "+00:00")
                    ) if item.get("published_at") else datetime.now(timezone.utc),
                    sentiment=sentiment,
                    severity=severity,
                    url=item.get("url", ""),
                ))

        return events


def symbol_to_base_ticker(usdt_symbol: str) -> str:
    """Convert 'BTCUSDT' to 'BTC'."""
    return usdt_symbol.replace("USDT", "").replace("BUSD", "")
