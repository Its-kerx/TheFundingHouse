"""
Backpack Exchange Adapter

Adapter sencillo que obtiene datos crudos de Backpack:
- fundingRate, markPrice, indexPrice, nextFundingTimestamp (markPrices)
- openInterest (openInterest)

NO calcula APR ni hace derivadas: eso se hará en otros microservicios.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .base import ExchangeAdapter

BACKPACK_BASE_URL = "https://api.backpack.exchange/api/v1"


class BackpackAdapter(ExchangeAdapter):
    """Adapter para datos públicos de Backpack."""

    def __init__(self) -> None:
        super().__init__("Backpack")
        # Cliente HTTP reutilizable
        self._client = httpx.AsyncClient(
            base_url=BACKPACK_BASE_URL, timeout=10.0
        )

    async def _get(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Wrapper simple sobre GET con httpx."""
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------- Métodos abstractos -------------------

    async def get_markets(self) -> List[Dict[str, Any]]:
        """
        “Mercados” básicos derivados de markPrices.
        Solo queremos tener una lista de símbolos normalizados.
        """
        data = await self._get("/markPrices")

        markets: List[Dict[str, Any]] = []
        for item in data:
            raw_symbol = item.get("symbol")
            if not raw_symbol:
                continue

            norm = self.normalize_symbol(raw_symbol)

            markets.append(
                {
                    "symbol": norm,
                    "base_asset": raw_symbol.split("-")[0]
                    if "-" in raw_symbol
                    else raw_symbol,
                    "quote_asset": "USDC",  # casi todos son perp vs USDC
                    "exchange": self.exchange_name,
                    "raw_symbol": raw_symbol,
                }
            )

        return markets

    async def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Funding actual de un símbolo usando /markPrices?symbol=...
        """
        raw_symbol = self._denormalize_symbol(symbol)

        data = await self._get("/markPrices", params={"symbol": raw_symbol})
        if not data:
            return None

        item = data[0] if isinstance(data, list) else data

        funding_rate = self._to_float(item.get("fundingRate"))
        ts_ms = item.get("nextFundingTimestamp")
        next_funding = (
            datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if ts_ms
            else None
        )

        return {
            "symbol": self.normalize_symbol(raw_symbol),
            "exchange": self.exchange_name,
            "funding_rate": funding_rate,
            "timestamp": datetime.now(timezone.utc),
            "next_funding_time": next_funding,
        }

    async def get_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Snapshot de mercado para un símbolo:
        fundingRate + markPrice + indexPrice + openInterest.
        """
        raw_symbol = self._denormalize_symbol(symbol)

        mark_data = await self._get("/markPrices", params={"symbol": raw_symbol})
        if not mark_data:
            return None
        mp = mark_data[0] if isinstance(mark_data, list) else mark_data

        oi_data = await self._get("/openInterest", params={"symbol": raw_symbol})
        oi = None
        if oi_data:
            oi = oi_data[0] if isinstance(oi_data, list) else oi_data

        funding_rate = self._to_float(mp.get("fundingRate"))
        next_funding_ts = mp.get("nextFundingTimestamp")
        next_funding = (
            datetime.fromtimestamp(next_funding_ts / 1000, tz=timezone.utc)
            if next_funding_ts
            else None
        )

        return {
            "exchange": self.exchange_name,
            "symbol": self.normalize_symbol(raw_symbol),
            "timestamp": datetime.now(timezone.utc),
            "funding_rate": funding_rate,
            "next_funding_time": next_funding,
            "open_interest": self._to_float(oi.get("openInterest")) if oi else None,
            "volume_24h": None,  # si luego queremos, lo sacamos de /tickers
            "mark_price": self._to_float(mp.get("markPrice")),
            "index_price": self._to_float(mp.get("indexPrice")),
            "raw": {"markPrices": mp, "openInterest": oi},
        }

    # ------------------- Helper para “todos los mercados” -------------------

    async def get_all_market_data(self) -> List[Dict[str, Any]]:
        """
        Snapshot de TODOS los símbolos:
        - markPrices (fundingRate, markPrice, indexPrice, nextFundingTimestamp)
        - openInterest (openInterest)

        Devuelve una lista de dicts listos para guardar tal cual en Mongo.
        """
        mark_list = await self._get("/markPrices")
        oi_list = await self._get("/openInterest")

        oi_map = {item.get("symbol"): item for item in (oi_list or [])}

        now = datetime.now(timezone.utc)
        results: List[Dict[str, Any]] = []

        for mp in mark_list:
            raw_symbol = mp.get("symbol")
            if not raw_symbol:
                continue

            oi = oi_map.get(raw_symbol)

            funding_rate = self._to_float(mp.get("fundingRate"))
            next_funding_ts = mp.get("nextFundingTimestamp")
            next_funding = (
                datetime.fromtimestamp(next_funding_ts / 1000, tz=timezone.utc)
                if next_funding_ts
                else None
            )

            results.append(
                {
                    "exchange": self.exchange_name,
                    "symbol": self.normalize_symbol(raw_symbol),
                    "timestamp": now,
                    "funding_rate": funding_rate,
                    "next_funding_time": next_funding,
                    "open_interest": self._to_float(oi.get("openInterest"))
                    if oi
                    else None,
                    "volume_24h": None,
                    "mark_price": self._to_float(mp.get("markPrice")),
                    "index_price": self._to_float(mp.get("indexPrice")),
                    "raw": {"markPrices": mp, "openInterest": oi},
                }
            )

        return results

    # ------------------- Helpers internos -------------------

    def _denormalize_symbol(self, symbol: str) -> str:
        """
        Aquí de momento no transformamos nada.
        Si más adelante vemos el formato exacto de símbolos de perps,
        lo ajustamos.
        """
        return symbol

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
