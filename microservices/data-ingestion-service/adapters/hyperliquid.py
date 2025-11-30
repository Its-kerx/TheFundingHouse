"""
Hyperliquid Exchange Adapter

Fetches and normalizes data from Hyperliquid perpetual futures exchange.
Only extracts raw fields from the API (no derived calculations).
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from hyperliquid.info import Info

from .base import ExchangeAdapter


class HyperliquidAdapter(ExchangeAdapter):
    """Adapter for Hyperliquid exchange"""

    def __init__(self):
        super().__init__("Hyperliquid")
        self.info = Info()

        # Cache for market metadata + contexts
        self._markets_cache: List[Dict[str, Any]] = []
        self._market_ctx_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 60  # seconds

        # Cache for "all markets" payload (used por get_all_market_data_cached)
        self._cache_all_markets: Optional[List[Dict[str, Any]]] = None
        self._cache_all_markets_ts: Optional[datetime] = None

    async def _ensure_cache(self) -> None:
        """
        Refresh markets and asset contexts if cache is stale.
        Uses meta_and_asset_ctxs + all_mids so we only hit the API once.
        """
        now = datetime.utcnow()
        if (
            self._cache_timestamp is not None
            and (now - self._cache_timestamp).total_seconds() < self._cache_ttl_seconds
        ):
            return

        try:
            # meta: info about markets, asset_ctxs: per-market context with raw fields
            meta, asset_ctxs = self.info.meta_and_asset_ctxs()
            mids = self.info.all_mids()

            markets: List[Dict[str, Any]] = []
            market_ctx: Dict[str, Dict[str, Any]] = {}

            for asset, ctx in zip(meta.get("universe", []), asset_ctxs):
                raw_symbol = asset.get("name")
                if not raw_symbol:
                    continue

                raw_ctx = ctx  # naming claro

                markets.append(
                    {
                        "symbol": self.normalize_symbol(raw_symbol),
                        "base_asset": raw_symbol.split("-")[0]
                        if "-" in raw_symbol
                        else raw_symbol,
                        "quote_asset": "USD",
                        "exchange": self.exchange_name,
                        "raw_symbol": raw_symbol,
                    }
                )

                # Solo campos CRUDOS, convertidos a float donde toca
                market_ctx[raw_symbol] = {
                    "funding_raw": self._to_float(
                        raw_ctx.get("funding")
                        or raw_ctx.get("currentFunding")
                        or 0.0
                    ),
                    "open_interest": self._to_float(
                        raw_ctx.get("openInterest")
                        or raw_ctx.get("openInterestNotional")
                        or 0.0
                    ),
                    "volume_24h": self._to_float(
                        raw_ctx.get("dayNtlVlm") or raw_ctx.get("dayBaseVlm") or 0.0
                    ),
                    "mark_price": self._to_float(
                        raw_ctx.get("markPx") or mids.get(raw_symbol, 0.0)
                    ),
                    "oracle_price": self._to_float(raw_ctx.get("oraclePx"))
                    if raw_ctx.get("oraclePx") is not None
                    else None,
                    "mid_price": self._to_float(
                        raw_ctx.get("midPx") or mids.get(raw_symbol, 0.0)
                    ),
                    "raw_ctx": raw_ctx,
                }

            self._markets_cache = markets
            self._market_ctx_cache = market_ctx
            self._cache_timestamp = now

        except Exception as e:
            print(f"Error refreshing Hyperliquid cache: {e}")
            # Si falla, mantenemos la caché anterior si existe

    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all perpetual markets from Hyperliquid (normalized symbols)"""
        try:
            await self._ensure_cache()
            return self._markets_cache
        except Exception as e:
            print(f"Error fetching Hyperliquid markets: {e}")
            return []

    async def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current funding rate for a symbol using cached asset contexts"""
        try:
            await self._ensure_cache()
            raw_symbol = self._denormalize_symbol(symbol)
            ctx = self._market_ctx_cache.get(raw_symbol)

            if not ctx:
                return None

            timestamp = datetime.utcnow()
            funding_rate = ctx.get("funding_raw", 0.0)

            return {
                "symbol": self.normalize_symbol(raw_symbol),
                "exchange": self.exchange_name,
                "funding_rate": funding_rate,
                "timestamp": timestamp,
                "next_funding_time": None,  # no inferimos nada
            }

        except Exception as e:
            print(f"Error fetching funding rate for {symbol}: {e}")
            return None

    async def get_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get raw market data snapshot for a single symbol"""
        try:
            await self._ensure_cache()
            raw_symbol = self._denormalize_symbol(symbol)
            ctx = self._market_ctx_cache.get(raw_symbol)

            if not ctx:
                return None

            return self._build_market_payload(raw_symbol, ctx)

        except Exception as e:
            print(f"Error fetching market data for {symbol}: {e}")
            return None

    async def get_all_market_data(
        self, include_spread: bool = False  # kept for compat; se ignora
    ) -> List[Dict[str, Any]]:
        """
        Return raw market snapshots for all assets using cached contexts.
        No derived calculations, solo datos tal cual de Hyperliquid.
        """
        try:
            await self._ensure_cache()
            data: List[Dict[str, Any]] = []
            for raw_symbol, ctx in self._market_ctx_cache.items():
                data.append(self._build_market_payload(raw_symbol, ctx))
            return data
        except Exception as e:
            print(f"Error fetching all market data: {e}")
            return []

    async def get_all_market_data_cached(
        self, include_spread: bool = True, max_age_seconds: int = 60 * 30
    ) -> List[Dict[str, Any]]:
        """
        Return cached market snapshots for all assets, refreshing when stale.
        include_spread se ignora (no calculamos spread aquí).
        """
        try:
            now = datetime.utcnow()
            if (
                self._cache_all_markets is not None
                and self._cache_all_markets_ts is not None
                and (now - self._cache_all_markets_ts).total_seconds() < max_age_seconds
            ):
                return self._cache_all_markets

            data = await self.get_all_market_data(include_spread=include_spread)
            self._cache_all_markets = data
            self._cache_all_markets_ts = now
            return data
        except Exception as e:
            print(f"Error fetching cached market data: {e}")
            return []

    # --------- Helpers ---------

    def normalize_symbol(self, raw_symbol: str) -> str:
        """
        Hyperliquid usa símbolos como 'BTC', 'ETH' o '0G-USD'.
        Normalizamos a 'BASE-USD' cuando no lleva guion.
        """
        if "-" in raw_symbol:
            return raw_symbol
        return f"{raw_symbol}-USD"

    def _denormalize_symbol(self, symbol: str) -> str:
        """Convert 'BTC-USD' to 'BTC' which is what the SDK expects"""
        return symbol.split("-")[0] if "-" in symbol else symbol

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _build_market_payload(
        self,
        raw_symbol: str,
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Build raw market payload for a given symbol.
        Solo mapeamos campos crudos de Hyperliquid a nombres consistentes.
        """
        raw_ctx = ctx.get("raw_ctx", {})

        # Opcional: debug para un símbolo concreto
        # if self.normalize_symbol(raw_symbol) == "0G-USD":
        #     print("RAW 0G-USD FROM HYPERLIQUID:", raw_ctx)

        timestamp = datetime.utcnow()

        return {
            "exchange": self.exchange_name,
            "symbol": self.normalize_symbol(raw_symbol),
            "timestamp": timestamp,
            "funding_rate": ctx.get("funding_raw", 0.0),      # float crudo
            "next_funding_time": None,                        # no inferimos
            "open_interest": ctx.get("open_interest", 0.0),   # float crudo
            "volume_24h": ctx.get("volume_24h", 0.0),         # float crudo
            "mark_price": ctx.get("mark_price", 0.0),         # float crudo
            "oracle_price": ctx.get("oracle_price", None),    # float o None
            # contexto completo tal y como viene del SDK (para trazabilidad)
            "raw": raw_ctx,
        }