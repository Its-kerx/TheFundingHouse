"""
Exchange Adapter Base Class

This module defines the base interface that all exchange adapters must implement.
Each exchange adapter is responsible for fetching and normalizing data from a specific exchange.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime


class ExchangeAdapter(ABC):
    """
    Base class for all exchange adapters.
    Each exchange must implement these methods to provide normalized data.
    """
    
    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
    
    @abstractmethod
    async def get_markets(self) -> List[Dict[str, Any]]:
        """
        Get all available perpetual markets.
        
        Returns:
            List of market dictionaries with standard fields:
            - symbol: str (e.g., "BTC-USD")
            - base_asset: str (e.g., "BTC")
            - quote_asset: str (e.g., "USD")
            - exchange: str
        """
        pass
    
    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get current funding rate for a specific market.
        
        Args:
            symbol: Market symbol (e.g., "BTC-USD")
            
        Returns:
            Dictionary with standard fields:
            - symbol: str
            - exchange: str
            - funding_rate: float (as decimal, e.g., 0.0001 for 0.01%)
            - timestamp: datetime
            - next_funding_time: datetime
        """
        pass
    
    @abstractmethod
    async def get_market_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive market data for a symbol.
        
        Args:
            symbol: Market symbol
            
        Returns:
            Dictionary with standard fields:
            - symbol: str
            - exchange: str
            - mark_price: float
            - index_price: float (if available)
            - open_interest: float
            - volume_24h: float
            - funding_rate: float
            - apr: float (calculated annualized return)
            - spread: float (bid-ask spread as percentage)
            - timestamp: datetime
        """
        pass
    
    def normalize_symbol(self, raw_symbol: str) -> str:
        """
        Normalize exchange-specific symbol to our standard format.
        Override this in subclass if needed.
        
        Args:
            raw_symbol: Exchange-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC-USD")
        """
        return raw_symbol
    
    def calculate_apr(self, funding_rate: float, funding_interval_hours: int = 8) -> float:
        """
        Calculate annualized percentage rate from funding rate.
        
        Args:
            funding_rate: Funding rate as decimal
            funding_interval_hours: Hours between funding payments
            
        Returns:
            APR as percentage (e.g., 15.5 for 15.5%)
        """
        periods_per_year = (365 * 24) / funding_interval_hours
        apr = abs(funding_rate) * periods_per_year * 100
        return round(apr, 2)
