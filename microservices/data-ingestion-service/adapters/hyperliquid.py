"""
Hyperliquid Exchange Adapter

Fetches and normalizes data from Hyperliquid perpetual futures exchange.
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
        self._markets_cache = None
        self._cache_timestamp = None
    
    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all perpetual markets from Hyperliquid"""
        try:
            # Cache markets for 5 minutes
            now = datetime.now()
            if self._markets_cache and self._cache_timestamp:
                if (now - self._cache_timestamp).seconds < 300:
                    return self._markets_cache
            
            meta = self.info.meta()
            markets = []
            
            for market in meta.get('universe', []):
                symbol = market['name']
                markets.append({
                    'symbol': self.normalize_symbol(symbol),
                    'base_asset': symbol.split('-')[0] if '-' in symbol else symbol,
                    'quote_asset': 'USD',  # Hyperliquid uses USD
                    'exchange': self.exchange_name,
                    'raw_symbol': symbol
                })
            
            self._markets_cache = markets
            self._cache_timestamp = now
            return markets
            
        except Exception as e:
            print(f"Error fetching Hyperliquid markets: {e}")
            return []
    
    async def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current funding rate for a symbol"""
        try:
            # Denormalize symbol for Hyperliquid (e.g., "BTC-USD" -> "BTC")
            raw_symbol = symbol.split('-')[0] if '-' in symbol else symbol
            
            # Get latest funding history (last entry is most recent)
            end_time = int(datetime.now().timestamp() * 1000)
            start_time = end_time - (24 * 60 * 60 * 1000)  # Last 24h
            
            history = self.info.funding_history(
                name=raw_symbol,
                startTime=start_time,
                endTime=end_time
            )
            
            if not history:
                return None
            
            latest = history[-1]
            funding_rate = float(latest['fundingRate'])
            timestamp = datetime.fromtimestamp(latest['time'] / 1000)
            
            # Hyperliquid funding is every 8 hours
            # Next funding is 8 hours after the latest one
            from datetime import timedelta
            next_funding = timestamp + timedelta(hours=8)
            
            return {
                'symbol': self.normalize_symbol(raw_symbol),
                'exchange': self.exchange_name,
                'funding_rate': funding_rate,
                'timestamp': timestamp,
                'next_funding_time': next_funding
            }
            
        except Exception as e:
            print(f"Error fetching funding rate for {symbol}: {e}")
            return None
    
    async def get_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive market data"""
        try:
            raw_symbol = symbol.split('-')[0] if '-' in symbol else symbol
            
            # Get funding rate
            funding_data = await self.get_funding_rate(symbol)
            if not funding_data:
                return None
            
            funding_rate = funding_data['funding_rate']
            
            # Get mark price from all_mids
            mids = self.info.all_mids()
            mark_price = float(mids.get(raw_symbol, 0))
            
            # Calculate APR (8-hour funding)
            apr = self.calculate_apr(funding_rate, funding_interval_hours=8)
            
            # Get meta info for additional data
            meta = self.info.meta()
            market_info = next(
                (m for m in meta.get('universe', []) if m['name'] == raw_symbol),
                None
            )
            
            # For now, we'll use placeholder values for OI and volume
            # These require additional API calls or WebSocket subscriptions
            open_interest = 0.0  # TODO: Fetch from asset contexts
            volume_24h = 0.0  # TODO: Fetch from candles or trades
            
            # Get L2 snapshot for spread calculation
            l2_data = self.info.l2_snapshot(name=raw_symbol)
            spread = 0.0
            
            if l2_data and 'levels' in l2_data:
                bids = l2_data['levels'][0] # Buy orders
                asks = l2_data['levels'][1] # Sell orders
                
                if bids and asks:
                    best_bid = float(bids[0]['px'])
                    best_ask = float(asks[0]['px'])
                    
                    if best_ask > 0:
                        spread = (best_ask - best_bid) / best_ask
            
            # Get Open Interest from asset contexts
            # Note: Hyperliquid doesn't expose simple public OI endpoint in python sdk easily
            # We will try to use the meta info or context if available, otherwise keep 0 for now
            # But we can get volume from candles if needed.
            
            return {
                'symbol': self.normalize_symbol(raw_symbol),
                'exchange': self.exchange_name,
                'mark_price': mark_price,
                'index_price': mark_price,
                'open_interest': open_interest,
                'volume_24h': volume_24h,
                'funding_rate': funding_rate,
                'apr': apr,
                'spread': spread,
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            print(f"Error fetching market data for {symbol}: {e}")
            return None
    
    def normalize_symbol(self, raw_symbol: str) -> str:
        """
        Hyperliquid uses simple symbols like 'BTC', 'ETH'
        We normalize to 'BTC-USD', 'ETH-USD' format
        """
        if '-' in raw_symbol:
            return raw_symbol
        return f"{raw_symbol}-USD"
