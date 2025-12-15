import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple
import math
import time
import urllib.parse
import urllib.request
from pymongo import UpdateOne

try:
    import httpx  # type: ignore
except ImportError:
    httpx = None

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

app = FastAPI(
    title="Funding Analytics Service",
    description="Microservicio para analizar funding/APR a partir de Mongo.",
    version="0.5.0",
    openapi_url="/openapi.json",
)

# -------------------------
# Config Mongo
# -------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@mongo:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")

COL_HYPER_CURRENT = "funding_hyperliquid_current"
COL_BACKPACK_CURRENT = "funding_backpack_current"
COL_FUNDING_TS = "funding_timeseries"
COL_FUNDING_PAIRS_TS = "funding_arbitrage_pairs_ts"
BACKPACK_HISTORY_URL = os.getenv("BACKPACK_HISTORY_URL")
HYPERLIQUID_HISTORY_URL = os.getenv("HYPERLIQUID_HISTORY_URL")

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
hyper_current_col = None
backpack_current_col = None
funding_ts_col = None
funding_pairs_col = None


# -------------------------
# Helpers comunes
# -------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _minute_floor(ts: datetime) -> datetime:
    """
    Normaliza el timestamp al inicio del minuto (UTC, con tz info).
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.replace(second=0, microsecond=0)


def annualize_funding(
    funding_rate: float,
    funding_interval_hours: float = 8.0,
) -> float:
    """
    funding_rate: tanto por uno por periodo (ej: 0.0001 == 0.01% cada 8h)
    APR% = funding_rate * (#periodos/año) * 100
    """
    if funding_interval_hours <= 0:
        funding_interval_hours = 8.0

    periods_per_year = (365.0 * 24.0) / float(funding_interval_hours)
    apr = funding_rate * periods_per_year * 100.0
    return float(apr)


def _normalize_live_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza un doc de las colecciones *_current a la forma mínima
    que queremos exponer en la API (snapshot 'Live').
    """
    d = dict(doc)
    d.pop("_id", None)

    exchange = d.get("exchange")
    symbol = d.get("symbol")
    canonical_symbol = d.get("canonical_symbol") or symbol

    funding_rate = float(d.get("funding_rate") or 0.0)
    interval = float(d.get("funding_interval_hours") or 1.0)
    if interval <= 0:
        interval = 1.0

    funding_percent = funding_rate * 100.0
    periods_per_year = (365.0 * 24.0) / interval
    apr = funding_rate * periods_per_year * 100.0
    abs_apr = abs(apr)

    # timestamp del snapshot / funding
    funding_ts = d.get("funding_timestamp") or d.get("timestamp") or now_utc()

    mark_price = d.get("mark_price") or d.get("price")
    index_price = d.get("index_price")
    price = d.get("price") or mark_price or index_price

    open_interest = d.get("open_interest")
    volume_24h = d.get("volume_24h")

    return {
        "exchange": exchange,
        "symbol": symbol,
        "canonical_symbol": canonical_symbol,
        "funding_rate": funding_rate,
        "funding_percent": funding_percent,
        "funding_interval_hours": interval,
        "funding_timestamp": funding_ts,
        "timestamp": funding_ts,
        "open_interest": open_interest,
        "volume_24h": volume_24h,
        "price": price,
        "mark_price": mark_price,
        "index_price": index_price,
        "apr_percent": apr,
        "abs_apr_percent": abs_apr,
    }


def _calc_apr_from_raw(rate: Any, interval_hours: Any) -> Optional[float]:
    try:
        r = float(rate)
        h = float(interval_hours)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or not math.isfinite(h) or h <= 0:
        return None
    return r * ((365.0 * 24.0) / h) * 100.0


def _add_debug_fields(pair: Dict[str, Any]) -> Dict[str, Any]:
    """
    Añade campos debug basados en funding_rate raw y funding_interval_hours.
    Convención de spread: spread = APR_short - APR_long (positivo si el APR del short es mayor).
    """
    long_m = pair.get("long_market") or {}
    short_m = pair.get("short_market") or {}

    l_rate = long_m.get("funding_rate")
    s_rate = short_m.get("funding_rate")
    l_int = long_m.get("funding_interval_hours")
    s_int = short_m.get("funding_interval_hours")

    l_apr_raw = _calc_apr_from_raw(l_rate, l_int)
    s_apr_raw = _calc_apr_from_raw(s_rate, s_int)
    spread_raw = None
    if l_apr_raw is not None and s_apr_raw is not None:
        spread_raw = s_apr_raw - l_apr_raw  # short APR minus long APR

    pair["debug_long_funding_rate_raw"] = l_rate if l_rate is not None else None
    pair["debug_long_interval_hours"] = l_int if l_int is not None else None
    pair["debug_long_apr_from_raw"] = l_apr_raw
    pair["debug_long_apr_field_used"] = long_m.get("apr_percent")

    pair["debug_short_funding_rate_raw"] = s_rate if s_rate is not None else None
    pair["debug_short_interval_hours"] = s_int if s_int is not None else None
    pair["debug_short_apr_from_raw"] = s_apr_raw
    pair["debug_short_apr_field_used"] = short_m.get("apr_percent")

    pair["debug_spread_apr_from_raw"] = spread_raw
    pair["debug_spread_apr_field_used"] = pair.get("spread_apr_percent")
    return pair


async def _read_active_symbols(max_pairs: Optional[int] = None) -> List[str]:
    symbols = set()
    collections = [COL_BACKPACK_CURRENT, COL_HYPER_CURRENT]
    limit_each = max_pairs or 0
    for col_name in collections:
        col = db[col_name]
        cursor = col.find({}, {"canonical_symbol": 1, "symbol": 1})
        if limit_each:
            cursor = cursor.limit(limit_each)
        docs = await cursor.to_list(length=max_pairs or 5000)
        for d in docs:
            sym = d.get("canonical_symbol") or d.get("symbol")
            if sym:
                symbols.add(sym)
            if max_pairs and len(symbols) >= max_pairs:
                break
        if max_pairs and len(symbols) >= max_pairs:
            break
    return list(symbols)


def _fetch_backpack_history(symbol: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    if not BACKPACK_HISTORY_URL:
        return []
    params = {"symbol": symbol, "start": start_ms, "end": end_ms}
    data = _retry_get(BACKPACK_HISTORY_URL, params=params)
    if not data:
        return []
    entries = data if isinstance(data, list) else data.get("data", [])
    out = []
    for entry in entries:
        rate = entry.get("fundingRate") or entry.get("funding_rate")
        ts = entry.get("time") or entry.get("timestamp")
        interval = entry.get("fundingIntervalHours") or 1.0
        if rate is None or ts is None:
            continue
        apr = _avg_apr_from_rate(rate, interval)
        ts_dt = _minute_floor(datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc))
        out.append(
            {
                "timestamp": ts_dt,
                "funding_rate": rate,
                "funding_interval_hours": interval,
                "apr_percent": apr,
            }
        )
    return out


def _fetch_hyperliquid_history(symbol: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    if not HYPERLIQUID_HISTORY_URL:
        return []
    params = {"symbol": symbol, "start": start_ms, "end": end_ms}
    data = _retry_get(HYPERLIQUID_HISTORY_URL, params=params)
    if not data:
        return []
    entries = data if isinstance(data, list) else data.get("data", [])
    out = []
    for entry in entries:
        rate = entry.get("fundingRate") or entry.get("funding_rate")
        ts = entry.get("time") or entry.get("timestamp")
        interval = entry.get("fundingIntervalHours") or 1.0
        if rate is None or ts is None:
            continue
        apr = _avg_apr_from_rate(rate, interval)
        ts_dt = _minute_floor(datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc))
        out.append(
            {
                "timestamp": ts_dt,
                "funding_rate": rate,
                "funding_interval_hours": interval,
                "apr_percent": apr,
            }
        )
    return out


def _align_and_build(symbol: str, bp: List[Dict[str, Any]], hl: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            continue
        apr_bp = bp_entry.get("apr_percent")
        apr_hl = hl_entry.get("apr_percent")
        if apr_bp is None or apr_hl is None:
            continue
        long_side = ("backpack", bp_entry) if apr_bp <= apr_hl else ("hyperliquid", hl_entry)
        short_side = ("hyperliquid", hl_entry) if apr_bp <= apr_hl else ("backpack", bp_entry)
        spread = float(short_side[1].get("apr_percent") or 0) - float(long_side[1].get("apr_percent") or 0)
        ts_dt = _minute_floor(datetime.fromtimestamp(ts_key, tz=timezone.utc))
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


def _retry_get(url: str, params: Dict[str, Any], attempts: int = 3, backoff: float = 1.5) -> Optional[Any]:
    for i in range(attempts):
        try:
            if httpx:
                resp = httpx.get(url, params=params, timeout=15.0)
                if resp.status_code == 200:
                    return resp.json()
                print(f"[bootstrap-history] GET {url} {params} -> {resp.status_code}", flush=True)
            else:
                query = urllib.parse.urlencode(params or {})
                full_url = f"{url}?{query}" if query else url
                req = urllib.request.Request(full_url)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        import json
                        return json.loads(resp.read().decode("utf-8"))
                    print(f"[bootstrap-history] GET {full_url} -> {resp.status}", flush=True)
        except Exception as exc:
            print(f"[bootstrap-history] GET {url} failed: {exc}", flush=True)
        time.sleep(backoff ** i)
    return None


def _avg_apr_from_rate(rate: Any, interval_hours: Any) -> Optional[float]:
    try:
        r = float(rate)
        h = float(interval_hours)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or not math.isfinite(h) or h <= 0:
        return None
    return r * ((365.0 * 24.0) / h) * 100.0


async def _fetch_all_current_docs() -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch all docs from any funding_*_current collection and group by pair key
    (canonical_symbol if available, else symbol).
    """
    if db is None:
        raise RuntimeError("Mongo client not initialized")

    by_pair: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    coll_names = await db.list_collection_names()
    for coll_name in coll_names:
        if not (coll_name.startswith("funding_") and coll_name.endswith("_current")):
            continue

        middle = coll_name[len("funding_") : -len("_current")]
        exchange_name = middle.strip().lower()
        if not exchange_name:
            continue

        coll = db[coll_name]
        docs = await coll.find({}).to_list(length=5000)
        for doc in docs:
            symbol = doc.get("symbol")
            canonical_symbol = doc.get("canonical_symbol")

            if canonical_symbol and isinstance(canonical_symbol, str):
                pair_key = canonical_symbol
            elif symbol and isinstance(symbol, str):
                pair_key = symbol
            else:
                continue

            d = dict(doc)
            d["exchange"] = d.get("exchange") or exchange_name
            d["pair_key"] = pair_key
            if canonical_symbol and isinstance(canonical_symbol, str):
                d["canonical_symbol"] = canonical_symbol

            normalized = _normalize_live_doc(d)
            normalized["pair_key"] = pair_key
            normalized["source"] = "live"

            by_pair[pair_key].append(normalized)

    return by_pair


async def compute_arbitrage_pairs(
    docs_by_pair: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Genera todas las combinaciones de pares por canonical_symbol.
    LONG = APR mas bajo; SHORT = APR mas alto.
    """
    now = now_utc()

    results: List[Dict[str, Any]] = []

    for pair_key, items in docs_by_pair.items():
        if not items:
            continue
        latest_by_exchange: Dict[str, Dict[str, Any]] = {}
        for it in items:
            ex = it.get('exchange')
            if not ex:
                continue
            ts_it = it.get('timestamp') or it.get('funding_timestamp') or now
            existing = latest_by_exchange.get(ex)
            if existing:
                ts_existing = (
                    existing.get('timestamp')
                    or existing.get('funding_timestamp')
                    or now
                )
                if ts_existing >= ts_it:
                    continue
            latest_by_exchange[ex] = it

        exchanges = sorted(latest_by_exchange.keys())
        if len(exchanges) < 2:
            continue

        canonical_symbol = (
            items[0].get('canonical_symbol')
            or items[0].get('canonical_pair')
            or pair_key
        )

        for ex_a, ex_b in combinations(exchanges, 2):
            doc_a = latest_by_exchange.get(ex_a)
            doc_b = latest_by_exchange.get(ex_b)
            if not doc_a or not doc_b:
                continue

            apr_a = float(doc_a.get('apr_percent') or 0.0)
            apr_b = float(doc_b.get('apr_percent') or 0.0)

            if apr_a <= apr_b:
                long_doc = doc_a
                short_doc = doc_b
            else:
                long_doc = doc_b
                short_doc = doc_a

            apr_long = float(long_doc.get('apr_percent') or 0.0)
            apr_short = float(short_doc.get('apr_percent') or 0.0)
            spread = apr_short - apr_long

            ts_a = doc_a.get('timestamp') or doc_a.get('funding_timestamp') or now
            ts_b = doc_b.get('timestamp') or doc_b.get('funding_timestamp') or now
            pair_ts = _minute_floor(max(ts_a, ts_b))

            ex1, ex2 = sorted([ex_a, ex_b])
            pair_id = f"{canonical_symbol}:{ex1}-{ex2}"

            results.append(
                _add_debug_fields(
                    {
                        'pair_id': pair_id,
                        'canonical_symbol': canonical_symbol,
                        'timestamp': pair_ts,
                        'source': 'live',
                        'spread_apr_percent': spread,
                        'long_market': {
                            'exchange': long_doc.get('exchange'),
                            'symbol': long_doc.get('symbol'),
                            'funding_rate': float(long_doc.get('funding_rate') or 0.0),
                        'funding_percent': float(long_doc.get('funding_percent') or 0.0),
                        'apr_percent': apr_long,
                        'mark_price': long_doc.get('mark_price') or long_doc.get('price'),
                        'open_interest': long_doc.get('open_interest'),
                        'volume_24h': long_doc.get('volume_24h'),
                        'last_updated': (
                            long_doc.get('timestamp')
                            or long_doc.get('funding_timestamp')
                            or pair_ts
                        ),
                    },
                    'short_market': {
                        'exchange': short_doc.get('exchange'),
                        'symbol': short_doc.get('symbol'),
                        'funding_rate': float(short_doc.get('funding_rate') or 0.0),
                        'funding_percent': float(short_doc.get('funding_percent') or 0.0),
                        'apr_percent': apr_short,
                        'mark_price': short_doc.get('mark_price')
                        or short_doc.get('price'),
                        'open_interest': short_doc.get('open_interest'),
                        'volume_24h': short_doc.get('volume_24h'),
                        'last_updated': (
                            short_doc.get('timestamp')
                            or short_doc.get('funding_timestamp')
                            or pair_ts
                        ),
                        },
                    }
                )
            )

    return results



# -------------------------
# Helpers de ventanas (histórico de pares)
# -------------------------

def _filter_pairs(
    pairs: List[Dict[str, Any]],
    min_spread_apr_percent: float = 0.0,
    canonical_symbol: Optional[str] = None,
    exchange_in: Optional[List[str]] = None,
    exchange_out: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    allow_set = set(exchange_in) if exchange_in else None
    block_set = set(exchange_out) if exchange_out else None

    filtered: List[Dict[str, Any]] = []
    for p in pairs:
        spread = float(p.get("spread_apr_percent") or 0.0)
        if spread < min_spread_apr_percent:
            continue

        if canonical_symbol and p.get("canonical_symbol") != canonical_symbol:
            continue

        long_ex = (p.get("long_market") or {}).get("exchange")
        short_ex = (p.get("short_market") or {}).get("exchange")

        if allow_set is not None:
            if long_ex not in allow_set or short_ex not in allow_set:
                continue

        if block_set is not None and (long_ex in block_set or short_ex in block_set):
            continue

        p_clean = dict(p)
        p_clean.pop("_id", None)
        filtered.append(p_clean)

    filtered.sort(
        key=lambda r: (
            float(r.get("spread_apr_percent") or 0.0),
            r.get("timestamp") or datetime.min,
        ),
        reverse=True,
    )
    return filtered[:limit]


async def _compute_live_pairs_filtered(
    min_spread_apr_percent: float = 0.0,
    canonical_symbol: Optional[str] = None,
    exchange_in: Optional[List[str]] = None,
    exchange_out: Optional[List[str]] = None,
    limit: int = 100,
    docs_by_pair: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    docs_map = docs_by_pair if docs_by_pair is not None else await _fetch_all_current_docs()
    pairs = await compute_arbitrage_pairs(docs_map)
    return _filter_pairs(
        pairs,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


async def _live_pairs_with_advanced_filters(
    min_spread_apr_percent: float = 0.0,
    canonical_symbol: Optional[str] = None,
    exchange_in: Optional[List[str]] = None,
    exchange_out: Optional[List[str]] = None,
    min_abs_apr_each_side: float = 0.0,
    min_oi: float = 0.0,
    min_volume_24h: float = 0.0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    prelimit = min(5000, max(limit * 3, limit))
    docs_by_pair = await _fetch_all_current_docs()
    pairs = await _compute_live_pairs_filtered(
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=prelimit,
        docs_by_pair=docs_by_pair,
    )

    filtered: List[Dict[str, Any]] = []
    for p in pairs:
        long_m = p.get("long_market") or {}
        short_m = p.get("short_market") or {}

        apr_long_abs = abs(float(long_m.get("apr_percent") or 0.0))
        apr_short_abs = abs(float(short_m.get("apr_percent") or 0.0))
        if min_abs_apr_each_side > 0.0 and (
            apr_long_abs < min_abs_apr_each_side or apr_short_abs < min_abs_apr_each_side
        ):
            continue

        oi_long = float(long_m.get("open_interest") or 0.0)
        oi_short = float(short_m.get("open_interest") or 0.0)
        if min_oi > 0.0 and (oi_long < min_oi or oi_short < min_oi):
            continue

        vol_long = float(long_m.get("volume_24h") or 0.0)
        vol_short = float(short_m.get("volume_24h") or 0.0)
        if min_volume_24h > 0.0 and (vol_long < min_volume_24h or vol_short < min_volume_24h):
            continue

        filtered.append(p)

    return filtered[:limit]


async def get_arbitrage_window(
    window_hours: float,
    N: Optional[int] = None,
    agg: str = "mean_raw",
    min_coverage: float = 0.7,
    min_spread_apr_percent: float = 0.0,
    canonical_symbol: Optional[str] = None,
    exchange_in: Optional[List[str]] = None,
    exchange_out: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Lee funding_arbitrage_pairs_ts y promedia los ultimos N snapshots por pair_id.
    Se limita N por par para evitar sesgos de series largas.
    """
    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")

    mode = (agg or "mean_raw").strip().lower()
    allowed = {"mean_raw", "mean_apr", "last_raw", "last_apr"}
    if mode not in allowed:
        raise HTTPException(400, f"Invalid agg mode '{agg}'. Allowed: {', '.join(sorted(allowed))}")
    if min_coverage < 0 or min_coverage > 1:
        raise HTTPException(400, "min_coverage must be between 0 and 1")

    from_ts = now_utc() - timedelta(hours=window_hours)

    pipeline = [
        {"$match": {"timestamp": {"$gte": from_ts}}},
        {"$sort": {"timestamp": -1}},
    ]

    if N is not None:
        pipeline += [
            {
                "$setWindowFields": {
                    "partitionBy": "$pair_id",
                    "sortBy": {"timestamp": -1},
                    "output": {"rn": {"$documentNumber": {}}},
                }
            },
            {"$match": {"rn": {"$lte": N}}},
        ]

    pipeline.append(
        {
            "$group": {
                "_id": "$pair_id",
                "avg_spread": {"$avg": "$spread_apr_percent"},
                "avg_long_rate": {"$avg": "$long_market.funding_rate"},
                "avg_short_rate": {"$avg": "$short_market.funding_rate"},
                "avg_long_apr": {"$avg": "$long_market.apr_percent"},
                "avg_short_apr": {"$avg": "$short_market.apr_percent"},
                "min_ts": {"$min": "$timestamp"},
                "max_ts": {"$max": "$timestamp"},
                "count": {"$sum": 1},
                "last_doc": {"$first": "$$ROOT"},
                "last_long": {"$first": "$long_market"},
                "last_short": {"$first": "$short_market"},
            }
        }
    )

    docs = await funding_pairs_col.aggregate(pipeline).to_list(length=200_000)
    pairs: List[Dict[str, Any]] = []

    snapshot_interval_seconds = 60.0
    expected_count = (window_hours * 3600.0) / snapshot_interval_seconds

    for g in docs:
        last_doc = g.get("last_doc") or {}
        pair_id = last_doc.get("pair_id")
        canonical_symbol = last_doc.get("canonical_symbol")
        window_start_ts = g.get("min_ts")
        window_end_ts = g.get("max_ts")
        window_count = g.get("count") or 0
        coverage_ratio = (window_count / expected_count) if expected_count else 0.0
        if window_count == 0:
            coverage_ratio = 0.0

        latest_long = g.get("last_long") or {}
        latest_short = g.get("last_short") or {}

        avg_long_rate = g.get("avg_long_rate")
        avg_short_rate = g.get("avg_short_rate")
        avg_long_apr = g.get("avg_long_apr")
        avg_short_apr = g.get("avg_short_apr")

        def build_side(latest: Dict[str, Any], avg_rate: Any, avg_apr_side: Any):
            interval_out = latest.get("funding_interval_hours")
            rate_out = avg_rate
            apr_out = avg_apr_side

            funding_percent = rate_out * 100.0 if rate_out is not None else None

            return {
                "exchange": latest.get("exchange"),
                "symbol": latest.get("symbol"),
                "funding_rate": rate_out,
                "funding_percent": funding_percent,
                "funding_interval_hours": interval_out,
                "apr_percent": apr_out,
                "mark_price": latest.get("mark_price") or latest.get("price"),
                "open_interest": latest.get("open_interest"),
                "volume_24h": latest.get("volume_24h"),
                "last_updated": latest.get("timestamp") or latest.get("funding_timestamp"),
            }

        long_market = build_side(latest_long, avg_long_rate, avg_long_apr)
        short_market = build_side(latest_short, avg_short_rate, avg_short_apr)

        apr_long = long_market.get("apr_percent")
        apr_short = short_market.get("apr_percent")
        spread = g.get("avg_spread")
        if spread is None and apr_long is not None and apr_short is not None:
            try:
                if math.isfinite(float(apr_long)) and math.isfinite(float(apr_short)):
                    spread = float(apr_short) - float(apr_long)
            except (TypeError, ValueError):
                spread = None

        pair = {
            "pair_id": pair_id,
            "canonical_symbol": canonical_symbol,
            "timestamp": window_end_ts,
            "spread_apr_percent": spread,
            "long_market": long_market,
            "short_market": short_market,
            "window_start_ts": window_start_ts,
            "window_end_ts": window_end_ts,
            "window_count": window_count,
            "expected_count": expected_count,
            "coverage_ratio": coverage_ratio,
            "aggregation_mode": mode,
            "data_source": "rolling",
            "confidence": "high" if coverage_ratio and coverage_ratio >= min_coverage else "medium",
            "is_partial": coverage_ratio < 0.5,
        }
        pairs.append(_add_debug_fields(pair))

    return _filter_pairs(
        pairs,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


async def get_timeseries_window(
    window_hours: float,
    min_spread_apr_percent: float = 0.0,
    canonical_symbol: Optional[str] = None,
    exchange_in: Optional[List[str]] = None,
    exchange_out: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Agregación por ventana temporal: promedia APR y spread en el rango temporal.
    No usa fallback a live.
    """
    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")

    from_ts = now_utc() - timedelta(hours=window_hours)
    pipeline = [
        {"$match": {"timestamp": {"$gte": from_ts}}},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": "$pair_id",
                "avg_spread": {"$avg": "$spread_apr_percent"},
                "avg_long_apr": {"$avg": "$long_market.apr_percent"},
                "avg_short_apr": {"$avg": "$short_market.apr_percent"},
                "avg_long_rate": {"$avg": "$long_market.funding_rate"},
                "avg_short_rate": {"$avg": "$short_market.funding_rate"},
                "min_ts": {"$min": "$timestamp"},
                "max_ts": {"$max": "$timestamp"},
                "count": {"$sum": 1},
                "last_doc": {"$first": "$$ROOT"},
                "last_long": {"$first": "$long_market"},
                "last_short": {"$first": "$short_market"},
            }
        },
    ]

    docs = await funding_pairs_col.aggregate(pipeline).to_list(length=200_000)
    pairs: List[Dict[str, Any]] = []

    expected_count = window_hours * 60.0  # minutos en la ventana

    for g in docs:
        last_doc = g.get("last_doc") or {}
        pair_id = last_doc.get("pair_id")
        canonical_symbol = last_doc.get("canonical_symbol")
        window_start_ts = g.get("min_ts")
        window_end_ts = g.get("max_ts")
        window_count = g.get("count") or 0
        coverage_ratio = (window_count / expected_count) if expected_count else 0.0
        if window_count == 0:
            coverage_ratio = 0.0

        latest_long = g.get("last_long") or {}
        latest_short = g.get("last_short") or {}

        avg_long_rate = g.get("avg_long_rate")
        avg_short_rate = g.get("avg_short_rate")
        avg_long_apr = g.get("avg_long_apr")
        avg_short_apr = g.get("avg_short_apr")
        avg_spread = g.get("avg_spread")

        def build_side(latest: Dict[str, Any], avg_rate: Any, avg_apr_side: Any):
            funding_percent = avg_rate * 100.0 if avg_rate is not None else None
            return {
                "exchange": latest.get("exchange"),
                "symbol": latest.get("symbol"),
                "funding_rate": avg_rate,
                "funding_percent": funding_percent,
                "funding_interval_hours": latest.get("funding_interval_hours"),
                "apr_percent": avg_apr_side,
                "mark_price": latest.get("mark_price") or latest.get("price"),
                "open_interest": latest.get("open_interest"),
                "volume_24h": latest.get("volume_24h"),
                "last_updated": latest.get("timestamp") or latest.get("funding_timestamp"),
            }

        long_market = build_side(latest_long, avg_long_rate, avg_long_apr)
        short_market = build_side(latest_short, avg_short_rate, avg_short_apr)

        spread = avg_spread
        if spread is None and long_market.get("apr_percent") is not None and short_market.get("apr_percent") is not None:
            try:
                spread = float(short_market.get("apr_percent")) - float(long_market.get("apr_percent"))
            except (TypeError, ValueError):
                spread = None

        pair = {
            "pair_id": pair_id,
            "canonical_symbol": canonical_symbol,
            "timestamp": window_end_ts,
            "spread_apr_percent": spread,
            "long_market": long_market,
            "short_market": short_market,
            "window_start_ts": window_start_ts,
            "window_end_ts": window_end_ts,
            "window_count": window_count,
            "expected_count": expected_count,
            "coverage_ratio": coverage_ratio,
            "is_partial": coverage_ratio < 0.5,
            "aggregation_mode": "mean_raw",
            "data_source": "rolling",
        }
        pairs.append(_add_debug_fields(pair))

    # Aplicar filtros y ordenación final
    filtered = _filter_pairs(
        pairs,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )
    return filtered


async def _window_or_fallback_live(
    window_hours: float,
    N: Optional[int],
    agg: str,
    min_coverage: float,
    min_spread_apr_percent: float,
    canonical_symbol: Optional[str],
    exchange_in: Optional[List[str]],
    exchange_out: Optional[List[str]],
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Si la ventana no tiene cobertura suficiente o queda vacía, cae a Live.
    """
    window_pairs = await get_arbitrage_window(
        window_hours=window_hours,
        N=N,
        agg=agg,
        min_coverage=min_coverage,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )

    # Elimina pares sin muestras (evita APR=0 ficticio)
    window_pairs = [
        p
        for p in window_pairs
        if (p.get("window_count") or 0) > 0 and p.get("spread_apr_percent") is not None
    ]

    has_coverage = any(
        (float(p.get("coverage_ratio") or 0) >= min_coverage) for p in window_pairs
    )

    if window_pairs and has_coverage:
        for p in window_pairs:
            p.setdefault("mode", "window")
            p["data_source"] = "rolling"
            p["confidence"] = "high"
        return window_pairs

    # Bootstrap con historia agregada aunque la cobertura sea baja: no inventa datos.
    if window_pairs:
        for p in window_pairs:
            p["mode"] = "bootstrap_history"
            p["data_source"] = "bootstrap_history"
            cov = float(p.get("coverage_ratio") or 0.0)
            p["confidence"] = "medium" if cov >= 0.3 else "low"
        return window_pairs

    # Fallback a live sin inventar APRs
    live_pairs = await _compute_live_pairs_filtered(
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )
    for p in live_pairs:
        p["mode"] = "fallback_live"
        p["reason"] = "window_not_ready"
        p["data_source"] = "live_fallback"
        p["confidence"] = "low"
        p.setdefault("coverage_ratio", None)
        p.setdefault("expected_count", None)
        p.setdefault("window_count", None)
        p.setdefault("window_start_ts", None)
        p.setdefault("window_end_ts", None)
    return live_pairs


# -------------------------
# Eventos
# -------------------------

@app.on_event("startup")
async def startup_event():
    global mongo_client, db, hyper_current_col, backpack_current_col, funding_ts_col, funding_pairs_col

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]

    hyper_current_col = db[COL_HYPER_CURRENT]
    backpack_current_col = db[COL_BACKPACK_CURRENT]
    funding_ts_col = db[COL_FUNDING_TS]
    funding_pairs_col = db[COL_FUNDING_PAIRS_TS]

    # Migration: set source="live" where missing
    try:
        res = await funding_pairs_col.update_many(
            {"$or": [{"source": {"$exists": False}}, {"source": None}]},
            {"$set": {"source": "live"}},
        )
        print(f"[startup] migration set source=live updated={res.modified_count}", flush=True)
    except Exception as exc:
        print(f"[startup] migration warning: {exc}", flush=True)

    # Indexes for faster window queries (idempotent)
    try:
        await funding_pairs_col.create_index(
            [("pair_id", 1), ("timestamp", 1), ("source", 1)], unique=True
        )
        await funding_pairs_col.create_index([("pair_id", 1), ("timestamp", -1)])
        await funding_pairs_col.create_index([("timestamp", -1)])
    except Exception as exc:
        # avoid startup crash if index creation fails; log to stdout
        print(f"[startup] Index creation warning: {exc}", flush=True)


@app.on_event("shutdown")
async def shutdown_event():
    global mongo_client
    if mongo_client is not None:
        mongo_client.close()


# -------------------------
# Status
# -------------------------

@app.get("/status", response_model=dict)
async def get_status():
    return {
        "status": "ok",
        "service": "funding-analytics",
        "version": "0.5.0",
        "timestamp": now_utc().isoformat(),
    }


# -------------------------
# LIVE (snapshot actual)
# -------------------------

@app.get("/funding/live", response_model=List[Dict[str, Any]])
async def funding_live(
    canonical_symbol: Optional[str] = Query(
        default=None,
        description="Filtra por canonical_symbol (BTC, ETH, PIPE, ...)",
    ),
    min_spread_apr_percent: float = Query(
        default=0.0,
        ge=0.0,
        description=(
            "Filtra por spread minimo de APR entre las dos patas del par "
            "(ej: 20 => solo pares con diferencia >=20 puntos)."
        ),
    ),
    exchange_in: Optional[List[str]] = Query(
        default=None,
        description="Si se indica, ambos exchanges deben pertenecer a este conjunto.",
    ),
    exchange_out: Optional[List[str]] = Query(
        default=None,
        description="Excluye pares que contengan cualquiera de estos exchanges.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=5000,
        description="Maximo numero de pares devueltos",
    ),
):
    """
    Calcula en vivo los pares de arbitraje a partir de funding_*_current.
    """
    try:
        return await _compute_live_pairs_filtered(
            min_spread_apr_percent=min_spread_apr_percent,
            canonical_symbol=canonical_symbol,
            exchange_in=exchange_in,
            exchange_out=exchange_out,
            limit=limit,
        )
    except Exception as e:
        raise HTTPException(500, f"Error calculando arbitraje Live: {e}")


# -------------------------
# Arbitraje de funding
# -------------------------


@app.post("/funding/arbitrage/refresh", response_model=dict)
async def refresh_arbitrage_pairs():
    """
    Recalcula todas las combinaciones de pares y guarda un snapshot en funding_arbitrage_pairs_ts.
    """
    try:
        docs_by_pair = await _fetch_all_current_docs()
        pairs = await compute_arbitrage_pairs(docs_by_pair)
    except Exception as e:
        raise HTTPException(500, f"Error calculando pares de arbitraje: {e}")

    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")

    if pairs:
        ops = []
        for p in pairs:
            ts = _minute_floor(p.get("timestamp") or now_utc())
            pid = p.get("pair_id")
            source = p.get("source") or "live"
            p["source"] = source
            p["timestamp"] = ts
            ops.append(
                UpdateOne(
                    {"pair_id": pid, "timestamp": ts, "source": source},
                    {"$set": p, "$setOnInsert": p},
                    upsert=True,
                )
            )
        if ops:
            await funding_pairs_col.bulk_write(ops, ordered=False)

    return {
        "status": "ok",
        "pairs_inserted": len(pairs),
        "timestamp": now_utc().isoformat(),
    }


@app.get("/funding/snapshots/health", response_model=dict)
async def snapshots_health(days: int = Query(30, ge=1, le=365)):
    """
    Devuelve cobertura básica de snapshots en funding_arbitrage_pairs_ts.
    """
    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")

    now = now_utc()
    since = now - timedelta(days=days)

    newest_doc = await funding_pairs_col.find_one({}, sort=[("timestamp", -1)], projection={"timestamp": 1})
    oldest_doc = await funding_pairs_col.find_one({}, sort=[("timestamp", 1)], projection={"timestamp": 1})

    newest_ts = newest_doc["timestamp"] if newest_doc else None
    oldest_ts = oldest_doc["timestamp"] if oldest_doc else None

    # Distinct timestamps in window
    ts_pipeline = [
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {"_id": "$timestamp"}},
        {"$count": "cnt"},
    ]
    distinct_ts_doc = await funding_pairs_col.aggregate(ts_pipeline).to_list(length=1)
    distinct_ts = distinct_ts_doc[0]["cnt"] if distinct_ts_doc else 0

    # Totals in window
    total_snapshots = await funding_pairs_col.count_documents({"timestamp": {"$gte": since}})

    coverage_ok = False
    notes = ""
    if not oldest_ts:
        notes = "no data"
    else:
        if oldest_ts is not None and oldest_ts.tzinfo is None:
            oldest_ts = oldest_ts.replace(tzinfo=timezone.utc)
        coverage_ok = oldest_ts <= since
        notes = f"oldest_ts={oldest_ts}, newest_ts={newest_ts}"

    expected_snapshots = int(days * 24 * 60)
    coverage_ratio = (total_snapshots / expected_snapshots) if expected_snapshots else 0

    return {
        "earliest_ts": oldest_ts,
        "latest_ts": newest_ts,
        "total_snapshots": total_snapshots,
        "expected_snapshots": expected_snapshots,
        "coverage_ratio": coverage_ratio,
        "distinct_timestamps": distinct_ts,
        "coverage_ok": coverage_ok,
        "notes": notes,
        "coverage_hours": (now - oldest_ts).total_seconds() / 3600.0 if oldest_ts else 0,
    }


@app.post("/funding/snapshots/migrate-minute", response_model=dict)
async def migrate_snapshots_round_to_minute(
    batch_size: int = Query(1000, ge=1, le=20000),
    max_batches: int = Query(50, ge=1, le=1000),
):
    """
    Normaliza timestamps al inicio del minuto (source preservado) con upsert idempotente.
    Política: si hay duplicados, prevalece el documento que se procese más tarde (normalmente con timestamp más reciente).
    """
    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")

    migrated = 0
    deleted = 0
    batches = 0

    while batches < max_batches:
        batches += 1
        cursor = funding_pairs_col.find(
            {
                "$or": [
                    {"$expr": {"$ne": [{"$second": "$timestamp"}, 0]}},
                    {"$expr": {"$ne": [{"$millisecond": "$timestamp"}, 0]}},
                ]
            },
            projection=None,
            limit=batch_size,
        )
        docs = await cursor.to_list(length=batch_size)
        if not docs:
            break

        ops = []
        ids_to_delete = []
        for doc in docs:
            ts = doc.get("timestamp")
            if not ts:
                ids_to_delete.append(doc.get("_id"))
                continue
            ts_norm = _minute_floor(ts)
            doc_norm = dict(doc)
            doc_norm.pop("_id", None)
            doc_norm["timestamp"] = ts_norm
            doc_norm.setdefault("source", "live")
            ops.append(
                UpdateOne(
                    {"pair_id": doc_norm.get("pair_id"), "timestamp": ts_norm, "source": doc_norm.get("source")},
                    {"$set": doc_norm, "$setOnInsert": doc_norm},
                    upsert=True,
                )
            )
            ids_to_delete.append(doc.get("_id"))

        if ops:
            res = await funding_pairs_col.bulk_write(ops, ordered=False)
            migrated += res.upserted_count + res.modified_count
        if ids_to_delete:
            del_res = await funding_pairs_col.delete_many({"_id": {"$in": ids_to_delete}})
            deleted += del_res.deleted_count

    return {
        "status": "ok",
        "migrated": migrated,
        "deleted": deleted,
        "batches": batches,
    }


@app.post("/funding/snapshots/bootstrap", response_model=dict)
async def snapshots_bootstrap(
    days: int = Query(30, ge=1, le=365),
    mode: str = Query("bootstrap"),
):
    """
    Rellena minutos faltantes duplicando el último snapshot conocido (carry forward) con source='bootstrap'.
    """
    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")
    if mode != "bootstrap":
        raise HTTPException(400, "Unsupported mode")

    start = now_utc() - timedelta(days=days)
    end = now_utc()

    # Obtener todos los pair_id distintos
    pair_ids = await funding_pairs_col.distinct("pair_id")
    inserted = 0
    skipped_existing = 0
    processed = 0

    async def get_existing_minutes(pid: str) -> Dict[datetime, Dict[str, Any]]:
        cursor = funding_pairs_col.find(
            {"pair_id": pid, "timestamp": {"$gte": start, "$lte": end}},
            projection=None,
        ).sort("timestamp", 1)
        docs = await cursor.to_list(length=200000)
        by_minute = {}
        for d in docs:
            ts = d.get("timestamp")
            if not ts:
                continue
            minute = ts.replace(second=0, microsecond=0)
            by_minute[minute] = d
        return by_minute

    # Último snapshot antes del inicio para carry-forward
    async def get_last_before(pid: str) -> Optional[Dict[str, Any]]:
        return await funding_pairs_col.find_one(
            {"pair_id": pid, "timestamp": {"$lt": start}},
            sort=[("timestamp", -1)],
        )

    range_minutes = int((end - start).total_seconds() // 60)
    if range_minutes <= 0:
        raise HTTPException(400, "Invalid range")

    for pid in pair_ids:
        processed += 1
        existing = await get_existing_minutes(pid)
        last_known = await get_last_before(pid)
        if not last_known and not existing:
            continue

        current_carry = last_known or None
        ops = []
        for i in range(range_minutes + 1):
            ts_minute = start + timedelta(minutes=i)
            ts_minute = _minute_floor(ts_minute)
            if ts_minute in existing:
                current_carry = existing[ts_minute]
                continue
            if not current_carry:
                continue
            base = dict(current_carry)
            base.pop("_id", None)
            base["timestamp"] = ts_minute
            base["source"] = "bootstrap"
            ops.append(
                UpdateOne(
                    {"pair_id": base.get("pair_id"), "timestamp": ts_minute, "source": "bootstrap"},
                    {"$set": base},
                    upsert=True,
                )
            )
            if len(ops) >= 2000:
                res = await funding_pairs_col.bulk_write(ops, ordered=False)
                inserted += res.upserted_count
                skipped_existing += res.modified_count
                ops = []
        if ops:
            res = await funding_pairs_col.bulk_write(ops, ordered=False)
            inserted += res.upserted_count
            skipped_existing += res.modified_count

    return {
        "status": "ok",
        "mode": mode,
        "pairs_processed": processed,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "range_start": start,
        "range_end": end,
    }
@app.post("/funding/bootstrap-history", response_model=dict)
async def bootstrap_history(
    days: int = Query(30, ge=1, le=180),
    step_minutes: int = Query(60, ge=1, le=1440),
    limit_pairs: Optional[int] = Query(None, ge=1, le=5000),
):
    """
    Bootstrap historical funding data from exchange history APIs.
    """
    start_time = time.monotonic()
    if not BACKPACK_HISTORY_URL and not HYPERLIQUID_HISTORY_URL:
        raise HTTPException(
            status_code=501,
            detail="Historical APIs not configured (BACKPACK_HISTORY_URL, HYPERLIQUID_HISTORY_URL missing)",
        )

    symbols = await _read_active_symbols(limit_pairs)
    points_fetched = 0
    docs_upserted = 0
    docs_modified = 0
    errors: List[str] = []
    skipped_pairs: List[str] = []
    min_ts_inserted: Optional[datetime] = None
    max_ts_inserted: Optional[datetime] = None

    start_ms = int((now_utc() - timedelta(days=days)).timestamp() * 1000)
    end_ms = int(now_utc().timestamp() * 1000)

    for sym in symbols:
        try:
            bp_hist = _fetch_backpack_history(sym, start_ms, end_ms) if BACKPACK_HISTORY_URL else []
            hl_hist = _fetch_hyperliquid_history(sym, start_ms, end_ms) if HYPERLIQUID_HISTORY_URL else []
            docs = _align_and_build(sym, bp_hist, hl_hist)
            points_fetched += len(bp_hist) + len(hl_hist)
            if not docs:
                skipped_pairs.append(sym)
                continue
            ops = []
            for d in docs:
                ts_dt = d["timestamp"]
                min_ts_inserted = min(min_ts_inserted, ts_dt) if min_ts_inserted else ts_dt
                max_ts_inserted = max(max_ts_inserted, ts_dt) if max_ts_inserted else ts_dt
                ops.append(
                    UpdateOne(
                        {"pair_id": d["pair_id"], "timestamp": ts_dt, "source": "history"},
                        {"$set": d},
                        upsert=True,
                    )
                )
            if ops:
                result = await funding_pairs_col.bulk_write(ops, ordered=False)
                docs_upserted += result.upserted_count
                docs_modified += result.modified_count
            time.sleep(0.1)
        except Exception as exc:
            errors.append(f"{sym}: {exc}")

    duration = time.monotonic() - start_time

    if points_fetched == 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "No historical points fetched",
                "diagnostics": {
                    "backpack_url": BACKPACK_HISTORY_URL,
                    "hyperliquid_url": HYPERLIQUID_HISTORY_URL,
                    "symbols": symbols,
                },
            },
        )

    return {
        "status": "ok",
        "days_requested": days,
        "step_minutes": step_minutes,
        "pairs_considered": len(symbols),
        "points_fetched": points_fetched,
        "docs_upserted": docs_upserted,
        "docs_modified": docs_modified,
        "skipped_pairs": skipped_pairs,
        "errors": errors,
        "min_ts_inserted": min_ts_inserted,
        "max_ts_inserted": max_ts_inserted,
        "duration_seconds": duration,
    }


# -------------------------
# VENTANAS (1h, 8h, 24h, 3d, 7d, 15d, 31d)
# -------------------------

common_canonical = Query(
    default=None,
    description="Filtra por canonical_symbol (BTC, ETH, PIPE, ...)",
)
common_min_spread = Query(
    default=0.0,
    ge=0.0,
    description="Filtra por spread minimo de APR (ej: 20 => spread>=20)",
)
common_exchange_in = Query(
    default=None,
    description="Si se indica, ambos exchanges deben pertenecer a este conjunto.",
)
common_exchange_out = Query(
    default=None,
    description="Excluye pares que contengan cualquiera de estos exchanges.",
)
common_limit_pairs = Query(
    default=100,
    ge=1,
    le=5000,
    description="Maximo numero de pares devueltos",
)


@app.get("/funding/1h", response_model=List[Dict[str, Any]])
async def funding_1h(
    canonical_symbol: Optional[str] = common_canonical,
    min_spread_apr_percent: float = common_min_spread,
    exchange_in: Optional[List[str]] = common_exchange_in,
    exchange_out: Optional[List[str]] = common_exchange_out,
    limit: int = common_limit_pairs,
):
    """
    Ventana 1h basada en tiempo (no fallback).
    """
    return await get_timeseries_window(
        window_hours=1.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


@app.get("/funding/8h", response_model=List[Dict[str, Any]])
async def funding_8h(
    canonical_symbol: Optional[str] = common_canonical,
    min_spread_apr_percent: float = common_min_spread,
    exchange_in: Optional[List[str]] = common_exchange_in,
    exchange_out: Optional[List[str]] = common_exchange_out,
    limit: int = common_limit_pairs,
):
    return await get_timeseries_window(
        window_hours=8.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


@app.get("/funding/24h", response_model=List[Dict[str, Any]])
async def funding_24h(
    canonical_symbol: Optional[str] = common_canonical,
    min_spread_apr_percent: float = common_min_spread,
    exchange_in: Optional[List[str]] = common_exchange_in,
    exchange_out: Optional[List[str]] = common_exchange_out,
    limit: int = common_limit_pairs,
):
    return await get_timeseries_window(
        window_hours=24.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


@app.get("/funding/3d", response_model=List[Dict[str, Any]])
async def funding_3d(
    canonical_symbol: Optional[str] = common_canonical,
    min_spread_apr_percent: float = common_min_spread,
    exchange_in: Optional[List[str]] = common_exchange_in,
    exchange_out: Optional[List[str]] = common_exchange_out,
    limit: int = common_limit_pairs,
):
    return await get_timeseries_window(
        window_hours=72.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


@app.get("/funding/7d", response_model=List[Dict[str, Any]])
async def funding_7d(
    canonical_symbol: Optional[str] = common_canonical,
    min_spread_apr_percent: float = common_min_spread,
    exchange_in: Optional[List[str]] = common_exchange_in,
    exchange_out: Optional[List[str]] = common_exchange_out,
    limit: int = common_limit_pairs,
):
    return await get_timeseries_window(
        window_hours=168.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


@app.get("/funding/15d", response_model=List[Dict[str, Any]])
async def funding_15d(
    canonical_symbol: Optional[str] = common_canonical,
    min_spread_apr_percent: float = common_min_spread,
    exchange_in: Optional[List[str]] = common_exchange_in,
    exchange_out: Optional[List[str]] = common_exchange_out,
    limit: int = common_limit_pairs,
):
    return await get_timeseries_window(
        window_hours=360.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


@app.get("/funding/31d", response_model=List[Dict[str, Any]])
async def funding_31d(
    canonical_symbol: Optional[str] = common_canonical,
    min_spread_apr_percent: float = common_min_spread,
    exchange_in: Optional[List[str]] = common_exchange_in,
    exchange_out: Optional[List[str]] = common_exchange_out,
    limit: int = common_limit_pairs,
):
    return await get_timeseries_window(
        window_hours=744.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


# Self-check: ensure app is importable
_app_self_check = app


# -------------------------
# Arranque local
# -------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8002)),
    )
