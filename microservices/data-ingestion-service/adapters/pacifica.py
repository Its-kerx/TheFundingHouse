"""
Pacifica Exchange Adapter

Fetches and normalizes data from Pacifica public endpoints.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .base import ExchangeAdapter


PACIFICA_BASE_URL = os.getenv("PACIFICA_BASE_URL", "https://api.pacifica.fi/api/v1")


class PacificaAdapter(ExchangeAdapter):
    """Adapter for Pacifica exchange."""

    def __init__(self) -> None:
        super().__init__("Pacifica")
        self._client = httpx.AsyncClient(base_url=PACIFICA_BASE_URL, timeout=10.0)

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Pacifica GET {path} failed: {exc}")

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _extract_symbols(self, payload: Any) -> List[str]:
        data = payload
        if isinstance(payload, dict):
            data = payload.get("data") or payload.get("result") or payload.get("symbols")
        if isinstance(data, dict):
            data = data.get("symbols") or data.get("markets") or data.get("data") or data
        if isinstance(data, list):
            symbols: List[str] = []
            for item in data:
                if isinstance(item, str):
                    symbols.append(item)
                    continue
                if isinstance(item, dict):
                    sym = item.get("symbol") or item.get("s") or item.get("name")
                    if sym:
                        symbols.append(str(sym))
            return symbols
        if isinstance(data, dict):
            return [str(key) for key in data.keys()]
        return []

    async def get_markets(self) -> List[Dict[str, Any]]:
        payload = await self._get("/info")
        symbols = self._extract_symbols(payload)
        markets: List[Dict[str, Any]] = []
        for sym in symbols:
            markets.append(
                {
                    "symbol": self.normalize_symbol(sym),
                    "base_asset": sym,
                    "quote_asset": "USD",
                    "exchange": self.exchange_name,
                    "raw_symbol": sym,
                }
            )
        return markets

    async def _get_mid_price(self, symbol: str) -> Optional[float]:
        try:
            payload = await self._get("/book", params={"symbol": symbol})
        except Exception:
            return None
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, dict):
            return None
        bids = data.get("bids") or data.get("bid") or data.get("b") or []
        asks = data.get("asks") or data.get("ask") or data.get("a") or []

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

    async def _get_latest_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            payload = await self._get(
                "/funding_rate/history",
                params={"symbol": symbol, "limit": 1},
            )
        except Exception:
            return None
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, dict):
            data = data.get("items") or data.get("history") or data.get("list")
        if not isinstance(data, list) or not data:
            return None
        item = data[0]
        if not isinstance(item, dict):
            return None
        rate = (
            item.get("fundingRate")
            or item.get("funding_rate")
            or item.get("rate")
            or item.get("r")
        )
        ts = item.get("timestamp") or item.get("time") or item.get("t")
        return {"rate": rate, "timestamp": ts}

    async def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        info = await self._get_latest_funding_rate(symbol)
        if not info:
            return None
        return {
            "symbol": self.normalize_symbol(symbol),
            "exchange": self.exchange_name,
            "funding_rate": self._to_float(info.get("rate")),
            "timestamp": datetime.now(timezone.utc),
            "next_funding_time": None,
        }

    async def get_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        mark_price = await self._get_mid_price(symbol)
        funding_info = await self._get_latest_funding_rate(symbol)
        funding_rate = self._to_float(funding_info.get("rate") if funding_info else None)
        return {
            "exchange": self.exchange_name,
            "symbol": self.normalize_symbol(symbol),
            "timestamp": datetime.now(timezone.utc),
            "funding_rate": funding_rate,
            "next_funding_time": None,
            "open_interest": None,
            "volume_24h": None,
            "mark_price": mark_price,
            "index_price": None,
            "raw": {"funding": funding_info, "mark_price": mark_price},
        }

    async def get_all_market_data(self) -> List[Dict[str, Any]]:
        markets = await self.get_markets()
        results: List[Dict[str, Any]] = []
        for market in markets:
            symbol = market.get("raw_symbol") or market.get("symbol")
            if not symbol:
                continue
            snap = await self.get_market_data(symbol)
            if snap:
                results.append(snap)
        return results
