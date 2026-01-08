"""
DEX Extended Exchange Adapter

Fetches and normalizes data from DEX Extended public endpoints.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .base import ExchangeAdapter


DEX_EXTENDED_BASE_URL = os.getenv(
    "DEX_EXTENDED_BASE_URL",
    "https://api.starknet.extended.exchange",
)


class DexExtendedAdapter(ExchangeAdapter):
    """Adapter for DEX Extended exchange."""

    def __init__(self) -> None:
        super().__init__("DexExtended")
        self._client = httpx.AsyncClient(base_url=DEX_EXTENDED_BASE_URL, timeout=10.0)

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"DexExtended GET {path} failed: {exc}")

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _extract_markets(self, payload: Any) -> List[Any]:
        data = payload
        if isinstance(payload, dict):
            data = payload.get("data") or payload.get("result") or payload.get("markets")
        if isinstance(data, dict):
            nested = data.get("markets") or data.get("items") or data.get("data")
            data = nested if nested is not None else data
        if isinstance(data, dict):
            entries: List[Any] = []
            for key, value in data.items():
                if isinstance(value, dict):
                    entries.append({**value, "name": value.get("name") or key})
                else:
                    entries.append({"name": key, "value": value})
            return entries
        return data if isinstance(data, list) else []

    def _market_symbol(self, entry: Any) -> Optional[str]:
        if isinstance(entry, str):
            return entry
        if not isinstance(entry, dict):
            return None
        for key in ("name", "market", "symbol", "s"):
            val = entry.get(key)
            if val:
                return str(val)
        return None

    def _extract_stats(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(entry, dict):
            return {}
        stats = (
            entry.get("marketStats")
            or entry.get("market_stats")
            or entry.get("stats")
            or entry
        )
        return stats if isinstance(stats, dict) else {}

    def _build_market_payload(
        self,
        raw_symbol: str,
        stats: Dict[str, Any],
        raw_entry: Optional[Dict[str, Any]] = None,
        mark_price_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        mark_price = mark_price_override
        if mark_price is None:
            mark_price = self._to_float(
                stats.get("markPrice")
                or stats.get("mark_price")
                or stats.get("markPx")
                or stats.get("mark")
            )
        index_price = stats.get("indexPrice") or stats.get("index_price") or stats.get(
            "oracle_price"
        )
        if index_price is not None:
            index_price = self._to_float(index_price)
        funding_rate = self._to_float(
            stats.get("fundingRate")
            or stats.get("funding_rate")
            or stats.get("funding")
        )
        open_interest = stats.get("openInterest") or stats.get("open_interest") or stats.get(
            "openInterestNotional"
        )
        if open_interest is not None:
            open_interest = self._to_float(open_interest)
        volume_24h = stats.get("volume24h") or stats.get("volume_24h") or stats.get(
            "quoteVolume"
        )
        if volume_24h is not None:
            volume_24h = self._to_float(volume_24h)

        return {
            "exchange": self.exchange_name,
            "symbol": self.normalize_symbol(raw_symbol),
            "timestamp": datetime.now(timezone.utc),
            "funding_rate": funding_rate,
            "next_funding_time": None,
            "open_interest": open_interest,
            "volume_24h": volume_24h if (volume_24h and volume_24h > 0) else None,
            "mark_price": mark_price if mark_price else None,
            "index_price": index_price,
            "raw": {"market": raw_entry, "stats": stats},
        }

    async def _get_mid_price_from_orderbook(self, raw_symbol: str) -> Optional[float]:
        try:
            data = await self._get(f"/api/v1/info/markets/{raw_symbol}/orderbook")
        except Exception:
            return None
        book = data.get("data") if isinstance(data, dict) else data
        if not isinstance(book, dict):
            return None
        bids = book.get("bid") or book.get("bids") or []
        asks = book.get("ask") or book.get("asks") or []

        def best_price(side: Any, pick_max: bool) -> Optional[float]:
            prices: List[float] = []
            if isinstance(side, list):
                for item in side:
                    if isinstance(item, (list, tuple)) and item:
                        prices.append(self._to_float(item[0]))
                    elif isinstance(item, dict):
                        prices.append(self._to_float(item.get("price")))
            if not prices:
                return None
            return max(prices) if pick_max else min(prices)

        best_bid = best_price(bids, True)
        best_ask = best_price(asks, False)
        if best_bid is None or best_ask is None:
            return best_bid or best_ask
        return (best_bid + best_ask) / 2.0

    async def get_markets(self) -> List[Dict[str, Any]]:
        payload = await self._get("/api/v1/info/markets")
        entries = self._extract_markets(payload)
        markets: List[Dict[str, Any]] = []
        for entry in entries:
            raw_symbol = self._market_symbol(entry)
            if not raw_symbol:
                continue
            markets.append(
                {
                    "symbol": self.normalize_symbol(raw_symbol),
                    "base_asset": raw_symbol.split("-")[0] if "-" in raw_symbol else raw_symbol,
                    "quote_asset": raw_symbol.split("-")[1] if "-" in raw_symbol else "USD",
                    "exchange": self.exchange_name,
                    "raw_symbol": raw_symbol,
                }
            )
        return markets

    async def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        raw_symbol = symbol
        stats = await self._get_market_stats(raw_symbol)
        if not stats:
            return None
        funding_rate = self._to_float(
            stats.get("fundingRate") or stats.get("funding_rate") or stats.get("funding")
        )
        return {
            "symbol": self.normalize_symbol(raw_symbol),
            "exchange": self.exchange_name,
            "funding_rate": funding_rate,
            "timestamp": datetime.now(timezone.utc),
            "next_funding_time": None,
        }

    async def _get_market_stats(self, raw_symbol: str) -> Dict[str, Any]:
        try:
            stats = await self._get(f"/api/v1/info/markets/{raw_symbol}/stats")
            if isinstance(stats, dict):
                return stats.get("data") or stats
        except Exception:
            stats = None
        try:
            payload = await self._get("/api/v1/info/markets", params={"market": raw_symbol})
            entries = self._extract_markets(payload)
            for entry in entries:
                if self._market_symbol(entry) == raw_symbol:
                    return self._extract_stats(entry)
        except Exception:
            return {}
        return stats if isinstance(stats, dict) else {}

    async def get_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        raw_symbol = symbol
        stats = await self._get_market_stats(raw_symbol)
        if not stats:
            return None
        mark_price = self._to_float(
            stats.get("markPrice")
            or stats.get("mark_price")
            or stats.get("markPx")
            or stats.get("mark")
        )
        if not mark_price:
            mark_price = await self._get_mid_price_from_orderbook(raw_symbol)
        return self._build_market_payload(raw_symbol, stats, raw_entry=None, mark_price_override=mark_price)

    async def get_all_market_data(self) -> List[Dict[str, Any]]:
        payload = await self._get("/api/v1/info/markets")
        entries = self._extract_markets(payload)
        results: List[Dict[str, Any]] = []
        for entry in entries:
            raw_symbol = self._market_symbol(entry)
            if not raw_symbol:
                continue
            stats = self._extract_stats(entry)
            mark_price = self._to_float(
                stats.get("markPrice")
                or stats.get("mark_price")
                or stats.get("markPx")
                or stats.get("mark")
            )
            if not mark_price:
                mark_price = await self._get_mid_price_from_orderbook(raw_symbol)
            results.append(
                self._build_market_payload(
                    raw_symbol,
                    stats,
                    raw_entry=entry if isinstance(entry, dict) else {"market": entry},
                    mark_price_override=mark_price,
                )
            )
        return results
