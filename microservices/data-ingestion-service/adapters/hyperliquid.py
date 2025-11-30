"""
Hyperliquid Exchange Adapter

Fetches and normalizes data from Hyperliquid perpetual futures exchange.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from hyperliquid.info import Info

from .base import ExchangeAdapter


class HyperliquidAdapter(ExchangeAdapter):
    """Adapter for Hyperliquid exchange"""

    def __init__(self):
        super().__init__("Hyperliquid")
        self.info = Info()
        self._markets_cache: List[Dict[str, Any]] = []
        self._market_ctx_cache: Dict[str, Dict[str, float]] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 60

    async def _ensure_cache(self) -> None:
        """
        Refresh markets and asset contexts if cache is stale.
        Uses meta_and_asset_ctxs + all_mids so we only hit the API once.
        """
        now = datetime.now()
        if (
            self._cache_timestamp
            and (now - self._cache_timestamp).total_seconds() < self._cache_ttl_seconds
        ):
            return

        try:
            meta, asset_ctxs = self.info.meta_and_asset_ctxs()
            mids = self.info.all_mids()

            markets: List[Dict[str, Any]] = []
            market_ctx: Dict[str, Dict[str, float]] = {}

            for asset, ctx in zip(meta.get("universe", []), asset_ctxs):
                raw_symbol = asset.get("name")
                if not raw_symbol:
                    continue

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

                market_ctx[raw_symbol] = {
                    "funding_8h": self._to_float(
                        ctx.get("funding") or ctx.get("currentFunding") or 0.0
                    ),
                    "open_interest": self._to_float(
                        ctx.get("openInterest")
                        or ctx.get("openInterestUsd")
                        or ctx.get("openInterestNotional")
                        or 0.0
                    ),
                    "volume_24h": self._to_float(
                        ctx.get("dayNtlVlm") or ctx.get("dayNtlVlmUsd") or 0.0
                    ),
                    "mid_price": self._to_float(mids.get(raw_symbol, 0.0)),
                }

            self._markets_cache = markets
            self._market_ctx_cache = market_ctx
            self._cache_timestamp = now
        except Exception as e:
            print(f"Error refreshing Hyperliquid cache: {e}")
            # Keep old cache if available; otherwise leave empty

    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all perpetual markets from Hyperliquid"""
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

            funding_rate = ctx["funding_8h"]
            timestamp = datetime.now()

            return {
                "symbol": self.normalize_symbol(raw_symbol),
                "exchange": self.exchange_name,
                "funding_rate": funding_rate,
                "timestamp": timestamp,
                "next_funding_time": timestamp + timedelta(hours=8),
            }

        except Exception as e:
            print(f"Error fetching funding rate for {symbol}: {e}")
            return None

    async def get_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive market data using cached contexts"""
        try:
            await self._ensure_cache()
            raw_symbol = self._denormalize_symbol(symbol)
            ctx = self._market_ctx_cache.get(raw_symbol)

            if not ctx:
                return None

            return self._build_market_payload(raw_symbol, ctx, include_spread=True)

        except Exception as e:
            print(f"Error fetching market data for {symbol}: {e}")
            return None

    async def get_all_market_data(
        self, include_spread: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Return market snapshots for all assets using cached contexts.
        Set include_spread=True to compute L2 spreads only for the symbols you need.
        """
        try:
            await self._ensure_cache()
            data: List[Dict[str, Any]] = []
            for raw_symbol, ctx in self._market_ctx_cache.items():
                data.append(self._build_market_payload(raw_symbol, ctx, include_spread))
            return data
        except Exception as e:
            print(f"Error fetching all market data: {e}")
            return []

    def normalize_symbol(self, raw_symbol: str) -> str:
        """
        Hyperliquid uses simple symbols like 'BTC', 'ETH'
        We normalize to 'BTC-USD', 'ETH-USD' format
        """
        if "-" in raw_symbol:
            return raw_symbol
        return f"{raw_symbol}-USD"

    def _denormalize_symbol(self, symbol: str) -> str:
        """Convert 'BTC-USD' to 'BTC' which is what the SDK expects"""
        return symbol.split("-")[0] if "-" in symbol else symbol

    def _compute_spread(self, raw_symbol: str) -> float:
        """Compute bid/ask spread using L2 snapshot; return 0.0 on error"""
        try:
            l2_data = self.info.l2_snapshot(name=raw_symbol)
            if not l2_data or "levels" not in l2_data:
                return 0.0

            bids = l2_data["levels"][0]
            asks = l2_data["levels"][1]

            if not bids or not asks:
                return 0.0

            best_bid = self._to_float(bids[0].get("px"))
            best_ask = self._to_float(asks[0].get("px"))

            if best_ask <= 0:
                return 0.0

            return (best_ask - best_bid) / best_ask
        except Exception as e:
            print(f"Error calculating spread for {raw_symbol}: {e}")
            return 0.0

    def _to_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _build_market_payload(
        self, raw_symbol: str, ctx: Dict[str, float], include_spread: bool
    ) -> Dict[str, Any]:
        """Build normalized market payload from cached context"""
        funding_rate = ctx.get("funding_8h", 0.0)
        apr = self.calculate_apr(funding_rate, funding_interval_hours=8)
        mark_price = ctx.get("mid_price", 0.0)
        spread = (
            round(self._compute_spread(raw_symbol) * 100, 4)
            if include_spread
            else 0.0
        )

        return {
            "symbol": self.normalize_symbol(raw_symbol),
            "exchange": self.exchange_name,
            "mark_price": mark_price,
            "index_price": mark_price,
            "open_interest": ctx.get("open_interest", 0.0),
            "volume_24h": ctx.get("volume_24h", 0.0),
            "funding_rate": funding_rate,
            "apr": apr,
            "spread": spread,
            "timestamp": datetime.now(),
        }
