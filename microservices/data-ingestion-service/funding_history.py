from __future__ import annotations

"""
Helpers to fetch and normalize funding history into 8h buckets.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import httpx

from adapters.hyperliquid import HyperliquidAdapter
from hyperliquid.info import Info

# Optional: Backpack historic endpoint is not public; left as TODO when available.
try:
    from adapters.backpack import BackpackAdapter  # noqa: F401
except Exception:
    BackpackAdapter = None  # type: ignore

BASE_INTERVAL_HOURS = 8.0
BASE_INTERVAL = timedelta(hours=BASE_INTERVAL_HOURS)

DEX_EXTENDED_BASE_URL = os.getenv(
    "DEX_EXTENDED_BASE_URL",
    "https://api.starknet.extended.exchange",
)
PACIFICA_BASE_URL = os.getenv("PACIFICA_BASE_URL", "https://api.pacifica.fi/api/v1")


async def _fetch_backpack_funding_history_for_symbol(
    symbol: str,
    from_ts: datetime,
    to_ts: datetime,
    client: httpx.AsyncClient,
) -> List[Dict[str, Any]]:
    """
    Usa GET /api/v1/fundingRates para obtener histУrico de funding por sУmbolo.
    Funding de Backpack es horario; devolvemos interval_hours=1.0.
    """
    params = {"symbol": symbol, "limit": 1000, "offset": 0}
    resp = await client.get("https://api.backpack.exchange/api/v1/fundingRates", params=params)
    resp.raise_for_status()
    data = resp.json() or []

    events: List[Dict[str, Any]] = []
    for item in data:
        ts_str = item.get("intervalEndTimestamp")
        rate_str = item.get("fundingRate")
        if not ts_str or rate_str is None:
            continue

        ts_naive = datetime.fromisoformat(str(ts_str))
        ts = ts_naive.replace(tzinfo=timezone.utc)

        # Normalizamos from_ts/to_ts a aware UTC para evitar errores de comparaciЗn
        from_ts_norm = from_ts if from_ts.tzinfo else from_ts.replace(tzinfo=timezone.utc)
        to_ts_norm = to_ts if to_ts.tzinfo else to_ts.replace(tzinfo=timezone.utc)

        if ts < from_ts_norm or ts > to_ts_norm:
            continue

        events.append(
            {
                "timestamp": ts,
                "funding_rate": float(rate_str),
                "interval_hours": 1.0,
            }
        )
    return events


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        if isinstance(value, (int, float)):
            ts_num = float(value)
            if ts_num > 1e12:
                return datetime.fromtimestamp(ts_num / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(ts_num, tz=timezone.utc)
        if isinstance(value, str):
            ts_str = value.strip()
            if ts_str.endswith("Z"):
                ts_str = ts_str.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(ts_str)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    except Exception:
        return None
    return None


async def _fetch_dex_extended_funding_history_for_symbol(
    symbol: str,
    from_ts: datetime,
    to_ts: datetime,
    client: httpx.AsyncClient,
) -> List[Dict[str, Any]]:
    start_ms = int(from_ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(to_ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
    resp = await client.get(
        f"{DEX_EXTENDED_BASE_URL}/api/v1/info/{symbol}/funding",
        params={"startTime": start_ms, "endTime": end_ms},
    )
    resp.raise_for_status()
    data = resp.json() or {}
    events: List[Dict[str, Any]] = []
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get("items") or items.get("history") or items.get("data")
    if not isinstance(items, list):
        return events
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = _parse_ts(item.get("timestamp") or item.get("time") or item.get("t"))
        if ts is None:
            continue
        if ts < from_ts or ts > to_ts:
            continue
        rate_val = (
            item.get("fundingRate")
            or item.get("funding_rate")
            or item.get("funding")
            or item.get("rate")
        )
        interval = (
            item.get("interval_hours")
            or item.get("intervalHours")
            or item.get("interval")
            or 1.0
        )
        events.append(
            {
                "timestamp": ts,
                "funding_rate": float(rate_val or 0.0),
                "interval_hours": float(interval or 1.0),
                "raw": item,
            }
        )
    return events


async def _fetch_pacifica_funding_history_for_symbol(
    symbol: str,
    from_ts: datetime,
    to_ts: datetime,
    client: httpx.AsyncClient,
) -> List[Dict[str, Any]]:
    resp = await client.get(
        f"{PACIFICA_BASE_URL}/funding_rate/history",
        params={"symbol": symbol, "limit": 1000},
    )
    resp.raise_for_status()
    data = resp.json() or {}
    items = data.get("data") if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get("items") or items.get("history") or items.get("list")
    events: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return events
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = _parse_ts(item.get("timestamp") or item.get("time") or item.get("t"))
        if ts is None:
            continue
        if ts < from_ts or ts > to_ts:
            continue
        rate_val = (
            item.get("fundingRate")
            or item.get("funding_rate")
            or item.get("rate")
            or item.get("r")
        )
        interval = (
            item.get("interval_hours")
            or item.get("intervalHours")
            or item.get("interval")
            or 1.0
        )
        events.append(
            {
                "timestamp": ts,
                "funding_rate": float(rate_val or 0.0),
                "interval_hours": float(interval or 1.0),
                "raw": item,
            }
        )
    return events


async def fetch_raw_funding_history(
    exchange: str,
    symbol: str,
    from_ts: datetime,
    to_ts: datetime,
) -> List[Dict[str, Any]]:
    """
    Fetch raw funding events from an exchange API.
    Returns events with at least: timestamp (datetime UTC), funding_rate (float), interval_hours (float).
    """
    ex = exchange.lower()

    if ex == "hyperliquid":
        info = Info()
        start_ms = int(from_ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(to_ts.replace(tzinfo=timezone.utc).timestamp() * 1000)

        # Hyperliquid expects the base asset without "-USD".
        raw_symbol = symbol.split("-")[0] if "-" in symbol else symbol

        loop = asyncio.get_running_loop()
        try:
            history = await loop.run_in_executor(
                None,
                lambda: info.funding_history(
                    name=raw_symbol,
                    startTime=start_ms,
                    endTime=end_ms,
                ),
            )
        except Exception as e:
            raise RuntimeError(f"Error fetching Hyperliquid funding history for {symbol}: {e}")

        events: List[Dict[str, Any]] = []
        for ev in history or []:
            ts_ms = ev.get("time") or ev.get("timestamp")
            if ts_ms is None:
                continue
            ts = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
            rate = float(ev.get("fundingRate") or 0.0)
            # Hyperliquid funding is hourly; keep 1h as native interval.
            events.append(
                {
                    "timestamp": ts,
                    "funding_rate": rate,
                    "interval_hours": float(ev.get("interval_hours") or 1.0),
                    "raw": ev,
                }
            )
        return events

    if ex == "backpack":
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await _fetch_backpack_funding_history_for_symbol(
                symbol=symbol,
                from_ts=from_ts,
                to_ts=to_ts,
                client=client,
            )

    if ex in {"dexextended", "dex-extended", "dex_extended", "extended"}:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await _fetch_dex_extended_funding_history_for_symbol(
                symbol=symbol,
                from_ts=from_ts,
                to_ts=to_ts,
                client=client,
            )

    if ex == "pacifica":
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await _fetch_pacifica_funding_history_for_symbol(
                symbol=symbol,
                from_ts=from_ts,
                to_ts=to_ts,
                client=client,
            )

    raise ValueError(f"Unsupported exchange for funding history: {exchange}")


def aggregate_to_8h(
    events: Iterable[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Group raw funding events into 8h buckets.
    - bucket_start = floor(timestamp / 8h) * 8h
    - bucket_end   = bucket_start + 8h
    - funding_rate = mean of funding_rate inside the bucket
    - timestamp (snapshot) = bucket_end
    """
    buckets: Dict[datetime, Dict[str, Any]] = {}

    for ev in events:
        ts = ev.get("timestamp")
        if ts is None:
            continue
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        # Floor to 8h bucket.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        seconds = (ts - epoch).total_seconds()
        bucket_seconds = BASE_INTERVAL.total_seconds()
        start_seconds = (seconds // bucket_seconds) * bucket_seconds
        bucket_start = epoch + timedelta(seconds=start_seconds)
        bucket_end = bucket_start + BASE_INTERVAL

        b = buckets.setdefault(
            bucket_start,
            {"sum": 0.0, "count": 0, "timestamp": bucket_end},
        )
        b["sum"] += float(ev.get("funding_rate") or 0.0)
        b["count"] += 1

    snapshots: List[Dict[str, Any]] = []
    for bucket_start, agg in buckets.items():
        count = agg["count"] or 1
        avg_rate = agg["sum"] / float(count)
        funding_percent = avg_rate * 100.0
        funding_interval_hours = 1.0
        snapshots.append(
            {
                "timestamp": agg["timestamp"],
                "funding_rate": avg_rate,
                "funding_percent": funding_percent,
                "funding_interval_hours": funding_interval_hours,
            }
        )

    snapshots.sort(key=lambda x: x.get("timestamp"))
    return snapshots
