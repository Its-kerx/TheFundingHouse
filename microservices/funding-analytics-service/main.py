import os
from datetime import datetime, timezone, timedelta
from itertools import combinations
from typing import Any, Dict, List, Optional

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
    interval = float(d.get("funding_interval_hours") or 8.0)

    # timestamp del snapshot / funding
    funding_ts = d.get("funding_timestamp") or d.get("timestamp") or now_utc()

    mark_price = d.get("mark_price") or d.get("price")
    index_price = d.get("index_price")
    price = d.get("price") or mark_price or index_price

    open_interest = d.get("open_interest")
    volume_24h = d.get("volume_24h")

    apr = annualize_funding(funding_rate, interval)
    abs_apr = abs(apr)

    return {
        "exchange": exchange,
        "symbol": symbol,
        "canonical_symbol": canonical_symbol,
        "funding_rate": funding_rate,
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


async def _fetch_all_current_docs() -> List[Dict[str, Any]]:
    """
    Lee ambas colecciones *_current y devuelve docs normalizados.
    """
    if hyper_current_col is None or backpack_current_col is None:
        raise RuntimeError("Mongo collections not initialized")

    docs: List[Dict[str, Any]] = []
    hyper_docs = await hyper_current_col.find({}).to_list(length=5000)
    backpack_docs = await backpack_current_col.find({}).to_list(length=5000)
    docs.extend(hyper_docs)
    docs.extend(backpack_docs)

    return [_normalize_live_doc(d) for d in docs]


async def compute_arbitrage_pairs(
    docs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Genera todas las combinaciones de pares por canonical_symbol.
    LONG = APR mケs bajo; SHORT = APR mケs alto.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    now = now_utc()

    for d in docs:
        c_symbol = d.get("canonical_symbol") or d.get("symbol")
        exchange = d.get("exchange")
        if not c_symbol or not exchange:
            continue
        grouped.setdefault(c_symbol, []).append(d)

    results: List[Dict[str, Any]] = []

    for canonical_symbol, items in grouped.items():
        latest_by_exchange: Dict[str, Dict[str, Any]] = {}
        for it in items:
            ex = it.get("exchange")
            if not ex:
                continue
            ts_it = it.get("timestamp") or it.get("funding_timestamp") or now
            existing = latest_by_exchange.get(ex)
            if existing:
                ts_existing = (
                    existing.get("timestamp")
                    or existing.get("funding_timestamp")
                    or now
                )
                if ts_existing >= ts_it:
                    continue
            latest_by_exchange[ex] = it

        exchanges = sorted(latest_by_exchange.keys())
        if len(exchanges) < 2:
            continue

        for ex_a, ex_b in combinations(exchanges, 2):
            doc_a = latest_by_exchange.get(ex_a)
            doc_b = latest_by_exchange.get(ex_b)
            if not doc_a or not doc_b:
                continue

            apr_a = float(doc_a.get("apr_percent") or 0.0)
            apr_b = float(doc_b.get("apr_percent") or 0.0)

            if apr_a <= apr_b:
                long_doc = doc_a
                short_doc = doc_b
            else:
                long_doc = doc_b
                short_doc = doc_a

            apr_long = float(long_doc.get("apr_percent") or 0.0)
            apr_short = float(short_doc.get("apr_percent") or 0.0)
            spread = apr_short - apr_long

            ts_a = doc_a.get("timestamp") or doc_a.get("funding_timestamp") or now
            ts_b = doc_b.get("timestamp") or doc_b.get("funding_timestamp") or now
            pair_ts = max(ts_a, ts_b)

            ex1, ex2 = sorted([ex_a, ex_b])
            pair_id = f"{canonical_symbol}:{ex1}-{ex2}"

            results.append(
                {
                    "pair_id": pair_id,
                    "canonical_symbol": canonical_symbol,
                    "timestamp": pair_ts,
                    "spread_apr_percent": spread,
                    "long_market": {
                        "exchange": long_doc.get("exchange"),
                        "symbol": long_doc.get("symbol"),
                        "funding_rate": float(long_doc.get("funding_rate") or 0.0),
                        "apr_percent": apr_long,
                        "mark_price": long_doc.get("mark_price") or long_doc.get("price"),
                        "open_interest": long_doc.get("open_interest"),
                        "volume_24h": long_doc.get("volume_24h"),
                        "last_updated": (
                            long_doc.get("timestamp")
                            or long_doc.get("funding_timestamp")
                            or pair_ts
                        ),
                    },
                    "short_market": {
                        "exchange": short_doc.get("exchange"),
                        "symbol": short_doc.get("symbol"),
                        "funding_rate": float(short_doc.get("funding_rate") or 0.0),
                        "apr_percent": apr_short,
                        "mark_price": short_doc.get("mark_price")
                        or short_doc.get("price"),
                        "open_interest": short_doc.get("open_interest"),
                        "volume_24h": short_doc.get("volume_24h"),
                        "last_updated": (
                            short_doc.get("timestamp")
                            or short_doc.get("funding_timestamp")
                            or pair_ts
                        ),
                    },
                }
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
) -> List[Dict[str, Any]]:
    docs = await _fetch_all_current_docs()
    pairs = await compute_arbitrage_pairs(docs)
    return _filter_pairs(
        pairs,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


async def get_arbitrage_window(
    window_hours: float,
    min_spread_apr_percent: float = 0.0,
    canonical_symbol: Optional[str] = None,
    exchange_in: Optional[List[str]] = None,
    exchange_out: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")

    from_ts = now_utc() - timedelta(hours=window_hours)
    cursor = funding_pairs_col.find({"timestamp": {"$gte": from_ts}})
    docs = await cursor.to_list(length=100_000)
    pairs: List[Dict[str, Any]] = []
    for d in docs:
        d = dict(d)
        d.pop("_id", None)
        pairs.append(d)

    return _filter_pairs(
        pairs,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


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
        docs = await _fetch_all_current_docs()
        pairs = await compute_arbitrage_pairs(docs)
    except Exception as e:
        raise HTTPException(500, f"Error calculando pares de arbitraje: {e}")

    if funding_pairs_col is None:
        raise HTTPException(500, "Mongo collection funding_arbitrage_pairs_ts not initialized")

    if pairs:
        await funding_pairs_col.insert_many(pairs)

    return {
        "status": "ok",
        "pairs_inserted": len(pairs),
        "timestamp": now_utc().isoformat(),
    }


@app.get("/funding/arbitrage/pairs", response_model=List[Dict[str, Any]])
async def funding_arbitrage_pairs(
    min_spread_apr_percent: float = Query(
        default=0.0,
        ge=0.0,
        description=(
            "Minimo spread de APR entre LONG y SHORT. "
            "Ej: 20 => solo pares con diferencia de APR >= 20 puntos."
        ),
    ),
    min_abs_apr_each_side: float = Query(
        default=0.0,
        ge=0.0,
        description=(
            "Minimo |APR| que debe tener tanto el lado LONG como el SHORT. "
            "Ej: 10 => ambos lados deben tener |APR|>=10."
        ),
    ),
    min_oi: float = Query(
        default=0.0,
        ge=0.0,
        description="Minimo open interest requerido en ambos lados (0 = sin filtro).",
    ),
    min_volume_24h: float = Query(
        default=0.0,
        ge=0.0,
        description="Minimo volumen 24h requerido en ambos lados (0 = sin filtro).",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=1000,
        description="Numero maximo de pares a devolver.",
    ),
):
    """
    Busca oportunidades de arbitraje de funding entre exchanges (live).
    """
    try:
        prelimit = min(5000, max(limit * 3, limit))
        pairs = await _compute_live_pairs_filtered(
            min_spread_apr_percent=min_spread_apr_percent,
            limit=prelimit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo Mongo: {e}")

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
    return await get_arbitrage_window(
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
    return await get_arbitrage_window(
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
    return await get_arbitrage_window(
        window_hours=24.0,
        min_spread_apr_percent=min_spread_apr_percent,
        canonical_symbol=canonical_symbol,
        exchange_in=exchange_in,
        exchange_out=exchange_out,
        limit=limit,
    )


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
