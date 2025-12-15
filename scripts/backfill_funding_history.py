"""
Backfill funding history into funding_arbitrage_pairs_ts using exchange historical APIs.

Usage:
    python scripts/backfill_funding_history.py --days 30 --step_minutes 60 --max_pairs 50

Notes:
- This does NOT invent OI/volume/mark_price; those fields stay null when using history.
- Upserts are idempotent via unique index on (pair_id, timestamp, source).
"""

import argparse
import os
import sys
import time
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple

import requests
from pymongo import MongoClient, UpdateOne

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_funding_history")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill funding history")
    parser.add_argument("--days", type=int, default=30, help="Days to backfill (default 30)")
    parser.add_argument(
        "--step_minutes",
        type=int,
        default=60,
        help="Sampling step in minutes (default 60; exchanges usually 1h/8h)",
    )
    parser.add_argument("--max_pairs", type=int, default=None, help="Optional cap on number of pairs")
    return parser.parse_args()


MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@mongo:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")
BACKPACK_HISTORY_URL = os.getenv("BACKPACK_HISTORY_URL")  # e.g. https://api.backpack.exchange/api/v1/funding
HYPERLIQUID_HISTORY_URL = os.getenv("HYPERLIQUID_HISTORY_URL")  # e.g. https://api.hyperliquid.xyz/info

COL_FUNDING_PAIRS_TS = "funding_arbitrage_pairs_ts"
COL_BACKPACK_CURRENT = "funding_backpack_current"
COL_HYPER_CURRENT = "funding_hyperliquid_current"


def _retry_get(url: str, params: Dict[str, Any], attempts: int = 3, backoff: float = 1.5) -> Optional[Dict[str, Any]]:
    for i in range(attempts):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            log.warning("GET %s %s -> %s", url, params, resp.status_code)
        except requests.RequestException as exc:
            log.warning("GET %s failed: %s", url, exc)
        time.sleep(backoff ** i)
    return None


def _avg_apr_from_rate(rate: float, interval_hours: float) -> Optional[float]:
    try:
        r = float(rate)
        h = float(interval_hours)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or not math.isfinite(h) or h <= 0:
        return None
    return r * ((365 * 24) / h) * 100.0


def fetch_backpack_history(symbol: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    if not BACKPACK_HISTORY_URL:
        log.debug("BACKPACK_HISTORY_URL not set; skipping backpack history")
        return []
    params = {"symbol": symbol, "start": start_ts, "end": end_ts}
    data = _retry_get(BACKPACK_HISTORY_URL, params=params)
    if not data:
        return []
    # Expecting list of entries with fundingRate and time
    results = []
    for entry in data if isinstance(data, list) else data.get("data", []):
        rate = entry.get("fundingRate") or entry.get("funding_rate")
        ts = entry.get("time") or entry.get("timestamp")
        interval_hours = entry.get("fundingIntervalHours") or 1.0
        if rate is None or ts is None:
            continue
        apr = _avg_apr_from_rate(rate, interval_hours)
        results.append(
            {
                "timestamp": datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc),
                "funding_rate": rate,
                "funding_interval_hours": interval_hours,
                "apr_percent": apr,
            }
        )
    return results


def fetch_hyperliquid_history(symbol: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    if not HYPERLIQUID_HISTORY_URL:
        log.debug("HYPERLIQUID_HISTORY_URL not set; skipping hyperliquid history")
        return []
    params = {"symbol": symbol, "start": start_ts, "end": end_ts}
    data = _retry_get(HYPERLIQUID_HISTORY_URL, params=params)
    if not data:
        return []
    results = []
    for entry in data if isinstance(data, list) else data.get("data", []):
        rate = entry.get("fundingRate") or entry.get("funding_rate")
        ts = entry.get("time") or entry.get("timestamp")
        interval_hours = entry.get("fundingIntervalHours") or 1.0
        if rate is None or ts is None:
            continue
        apr = _avg_apr_from_rate(rate, interval_hours)
        results.append(
            {
                "timestamp": datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc),
                "funding_rate": rate,
                "funding_interval_hours": interval_hours,
                "apr_percent": apr,
            }
        )
    return results


def read_active_pairs(db, max_pairs: Optional[int]) -> List[str]:
    symbols = set()
    for col_name in [COL_BACKPACK_CURRENT, COL_HYPER_CURRENT]:
        col = db[col_name]
        docs = col.find({}, {"canonical_symbol": 1}).limit(max_pairs or 10000)
        for d in docs:
            sym = d.get("canonical_symbol") or d.get("symbol")
            if sym:
                symbols.add(sym)
            if max_pairs and len(symbols) >= max_pairs:
                break
        if max_pairs and len(symbols) >= max_pairs:
            break
    return list(symbols)


def align_and_build(
    symbol: str,
    bp: List[Dict[str, Any]],
    hl: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Align by timestamp (minute-level) and build arbitrage docs."""
    by_ts: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for entry in bp:
        ts_key = int(entry["timestamp"].timestamp())
        by_ts.setdefault(ts_key, {})["backpack"] = entry
    for entry in hl:
        ts_key = int(entry["timestamp"].timestamp())
        by_ts.setdefault(ts_key, {})["hyperliquid"] = entry

    docs = []
    for ts_key, data in by_ts.items():
        bp_entry = data.get("backpack")
        hl_entry = data.get("hyperliquid")
        if not bp_entry or not hl_entry:
            continue  # need both sides to compute spread

        ts_dt = datetime.fromtimestamp(ts_key, tz=timezone.utc)
        # Long = lower APR, Short = higher APR (consistent with live spread calc)
        apr_bp = bp_entry.get("apr_percent")
        apr_hl = hl_entry.get("apr_percent")
        if apr_bp is None or apr_hl is None:
            continue

        long_side = ("backpack", bp_entry) if apr_bp <= apr_hl else ("hyperliquid", hl_entry)
        short_side = ("hyperliquid", hl_entry) if apr_bp <= apr_hl else ("backpack", bp_entry)

        spread = float(short_side[1].get("apr_percent") or 0) - float(long_side[1].get("apr_percent") or 0)

        docs.append(
            {
                "pair_id": f"{symbol}:backpack-hyperliquid",
                "canonical_symbol": symbol,
                "timestamp": ts_dt,
                "source": "history",
                "data_quality": "history_bootstrap",
                "spread_apr_percent": spread,
                "long_market": {
                    "exchange": long_side[0],
                    "symbol": symbol,
                    "funding_rate": long_side[1].get("funding_rate"),
                    "funding_interval_hours": long_side[1].get("funding_interval_hours"),
                    "apr_percent": long_side[1].get("apr_percent"),
                    "mark_price": None,
                    "open_interest": None,
                    "volume_24h": None,
                    "last_updated": ts_dt,
                },
                "short_market": {
                    "exchange": short_side[0],
                    "symbol": symbol,
                    "funding_rate": short_side[1].get("funding_rate"),
                    "funding_interval_hours": short_side[1].get("funding_interval_hours"),
                    "apr_percent": short_side[1].get("apr_percent"),
                    "mark_price": None,
                    "open_interest": None,
                    "volume_24h": None,
                    "last_updated": ts_dt,
                },
            }
        )
    return docs


def main():
    args = parse_args()
    start = utcnow() - timedelta(days=args.days)
    end = utcnow()
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    pairs_col = db[COL_FUNDING_PAIRS_TS]

    symbols = read_active_pairs(db, args.max_pairs)
    log.info("Found %s active symbols", len(symbols))

    total_inserted = 0
    total_pairs = 0
    for sym in symbols:
        bp_hist = fetch_backpack_history(sym, start_ms, end_ms)
        hl_hist = fetch_hyperliquid_history(sym, start_ms, end_ms)
        docs = align_and_build(sym, bp_hist, hl_hist)
        if not docs:
            continue
        total_pairs += 1
        ops = []
        for d in docs:
            ops.append(
                UpdateOne(
                    {"pair_id": d["pair_id"], "timestamp": d["timestamp"], "source": "history"},
                    {"$set": d},
                    upsert=True,
                )
            )
        if ops:
            result = pairs_col.bulk_write(ops, ordered=False)
            total_inserted += result.upserted_count + result.modified_count
        time.sleep(0.2)  # simple rate limit

    log.info(
        "Backfill completed. symbols_processed=%s, records_upserted=%s, window_days=%s, step_minutes=%s",
        total_pairs,
        total_inserted,
        args.days,
        args.step_minutes,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
