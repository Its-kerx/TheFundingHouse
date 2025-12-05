import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

app = FastAPI(
    title="Funding Analytics Service",
    description="Microservicio para analizar funding/APR a partir de Mongo.",
    version="0.4.0",
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

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
hyper_current_col = None
backpack_current_col = None
funding_ts_col = None


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

    # precio preferente
    price = d.get("price") or d.get("mark_price") or d.get("index_price")

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
        "open_interest": open_interest,
        "volume_24h": volume_24h,
        "price": price,
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


# -------------------------
# Helpers de ventanas (histórico)
# -------------------------

async def _aggregate_window_hours(
    window_hours: float,
) -> List[Dict[str, Any]]:
    """
    Agrega funding en la colección funding_timeseries en una ventana
    [now - window_hours, now].

    Para cada (exchange, symbol) calcula:
        - funding_rate = media de funding_rate en esa ventana
        - funding_timestamp = timestamp del último doc
        - price, open_interest, volume_24h = del último doc
        - apr_percent = APR anualizado a partir de la media de funding_rate
    """
    if funding_ts_col is None:
        raise RuntimeError("Mongo funding_timeseries not initialized")

    now = now_utc()
    since = now - timedelta(hours=window_hours)

    cursor = funding_ts_col.find({"timestamp": {"$gte": since}})

    rows: List[Dict[str, Any]] = await cursor.to_list(length=100_000)

    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        r = dict(r)
        r.pop("_id", None)
        key = (r.get("exchange"), r.get("symbol"))
        by_key.setdefault(key, []).append(r)

    results: List[Dict[str, Any]] = []

    for (exchange, symbol), items in by_key.items():
        if not exchange or not symbol:
            continue

        items_sorted = sorted(items, key=lambda x: x.get("timestamp", now))
        last = items_sorted[-1]

        canonical_symbol = last.get("canonical_symbol") or symbol

        # funding_rate medio en la ventana
        funding_values = [float(it.get("funding_rate") or 0.0) for it in items_sorted]
        if not funding_values:
            continue

        mean_funding = sum(funding_values) / float(len(funding_values))

        interval = float(last.get("funding_interval_hours") or 8.0)
        apr = annualize_funding(mean_funding, interval)
        abs_apr = abs(apr)

        price = (
            last.get("price")
            or last.get("mark_price")
            or last.get("index_price")
        )

        doc_norm = {
            "exchange": exchange,
            "symbol": symbol,
            "canonical_symbol": canonical_symbol,
            "funding_rate": mean_funding,
            "funding_interval_hours": interval,
            "funding_timestamp": last.get("timestamp") or now,
            "open_interest": last.get("open_interest"),
            "volume_24h": last.get("volume_24h"),
            "price": price,
            "apr_percent": apr,
            "abs_apr_percent": abs_apr,
            "samples": len(items_sorted),
            "window_hours": window_hours,
        }
        results.append(doc_norm)

    return results


async def _funding_window_endpoint(
    window_hours: float,
    exchange: Optional[str],
    canonical_symbol: Optional[str],
    min_abs_apr_percent: float,
    limit: int,
) -> List[Dict[str, Any]]:
    try:
        docs = await _aggregate_window_hours(window_hours)
    except Exception as e:
        raise HTTPException(500, f"Error leyendo histórico Mongo: {e}")

    if exchange:
        docs = [d for d in docs if d.get("exchange") == exchange]

    if canonical_symbol:
        docs = [d for d in docs if d.get("canonical_symbol") == canonical_symbol]

    if min_abs_apr_percent > 0.0:
        docs = [
            d
            for d in docs
            if float(d.get("abs_apr_percent") or 0.0) >= min_abs_apr_percent
        ]

    docs.sort(key=lambda d: d.get("abs_apr_percent", 0.0), reverse=True)

    # Quitamos los campos internos que no quieres ver en frontend
    cleaned: List[Dict[str, Any]] = []
    for d in docs[:limit]:
        cleaned.append(
            {
                "exchange": d["exchange"],
                "symbol": d["symbol"],
                "canonical_symbol": d["canonical_symbol"],
                "funding_rate": d["funding_rate"],
                "funding_timestamp": d["funding_timestamp"],
                "open_interest": d.get("open_interest"),
                "volume_24h": d.get("volume_24h"),
                "price": d.get("price"),
                "apr_percent": d["apr_percent"],
            }
        )
    return cleaned


def _clean_live_for_frontend(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    Quita lo que no quieres ver en la respuesta del snapshot Live.
    """
    return {
        "exchange": d["exchange"],
        "symbol": d["symbol"],
        "canonical_symbol": d["canonical_symbol"],
        "funding_rate": d["funding_rate"],
        "funding_timestamp": d["funding_timestamp"],
        "open_interest": d.get("open_interest"),
        "volume_24h": d.get("volume_24h"),
        "price": d.get("price"),
        "apr_percent": d["apr_percent"],
    }


# -------------------------
# Eventos
# -------------------------

@app.on_event("startup")
async def startup_event():
    global mongo_client, db, hyper_current_col, backpack_current_col, funding_ts_col

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]

    hyper_current_col = db[COL_HYPER_CURRENT]
    backpack_current_col = db[COL_BACKPACK_CURRENT]
    funding_ts_col = db[COL_FUNDING_TS]


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
        "version": "0.4.0",
        "timestamp": now_utc().isoformat(),
    }


# -------------------------
# LIVE (snapshot actual)
# -------------------------

@app.get("/funding/live", response_model=List[Dict[str, Any]])
async def funding_live(
    exchange: Optional[str] = Query(
        default=None,
        description="Filtra por exchange (Hyperliquid, Backpack, ...)",
    ),
    canonical_symbol: Optional[str] = Query(
        default=None,
        description="Filtra por canonical_symbol (BTC, ETH, PIPE, ...)",
    ),
    min_abs_apr_percent: float = Query(
        default=0.0,
        ge=0.0,
        description="Filtra por |APR| mínimo (ej: 10 => solo |APR|>=10)",
    ),
    limit: int = Query(
        default=500,
        ge=1,
        le=5000,
        description="Máximo número de mercados devueltos",
    ),
):
    """
    Snapshot actual (colecciones *_current).
    """
    try:
        docs = await _fetch_all_current_docs()
    except Exception as e:
        raise HTTPException(500, f"Error leyendo Mongo: {e}")

    if exchange:
        docs = [d for d in docs if d.get("exchange") == exchange]

    if canonical_symbol:
        docs = [d for d in docs if d.get("canonical_symbol") == canonical_symbol]

    if min_abs_apr_percent > 0.0:
        docs = [
            d
            for d in docs
            if float(d.get("abs_apr_percent") or 0.0) >= min_abs_apr_percent
        ]

    docs.sort(key=lambda d: d.get("abs_apr_percent", 0.0), reverse=True)
    return [_clean_live_for_frontend(d) for d in docs[:limit]]


# -------------------------
# Arbitraje de funding
# -------------------------


@app.get("/funding/arbitrage/pairs", response_model=List[Dict[str, Any]])
async def funding_arbitrage_pairs(
    min_spread_apr_percent: float = Query(
        default=0.0,
        ge=0.0,
        description=(
            "Mínimo spread de APR entre LONG y SHORT. "
            "Ej: 20 => solo pares con diferencia de APR >= 20 puntos."
        ),
    ),
    min_abs_apr_each_side: float = Query(
        default=0.0,
        ge=0.0,
        description=(
            "Mínimo |APR| que debe tener tanto el lado LONG como el SHORT. "
            "Ej: 10 => ambos lados deben tener |APR|>=10."
        ),
    ),
    min_oi: float = Query(
        default=0.0,
        ge=0.0,
        description="Mínimo open interest requerido en ambos lados (0 = sin filtro).",
    ),
    min_volume_24h: float = Query(
        default=0.0,
        ge=0.0,
        description="Mínimo volumen 24h requerido en ambos lados (0 = sin filtro).",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=1000,
        description="Número máximo de pares a devolver.",
    ),
):
    """
    Busca oportunidades de arbitraje de funding entre exchanges.

    Para cada canonical_symbol con mercados en >=2 exchanges:
      - LONG: mercado con APR más bajo (más negativo).
      - SHORT: mercado con APR más alto (más positivo).
    Calcula spread_apr_percent = apr_short - apr_long y aplica filtros.
    """
    try:
        docs = await _fetch_all_current_docs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo Mongo: {e}")

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for d in docs:
        c_symbol = d.get("canonical_symbol") or d.get("symbol")
        if not c_symbol:
            continue
        groups.setdefault(c_symbol, []).append(d)

    results: List[Dict[str, Any]] = []

    for c_symbol, items in groups.items():
        exchanges: Set[str] = {i.get("exchange") for i in items}
        if len(exchanges) < 2:
            continue

        long_market = min(items, key=lambda x: float(x.get("apr_percent") or 0.0))
        short_market = max(items, key=lambda x: float(x.get("apr_percent") or 0.0))

        apr_long = float(long_market.get("apr_percent") or 0.0)
        apr_short = float(short_market.get("apr_percent") or 0.0)
        spread = apr_short - apr_long

        if spread <= 0:
            continue

        if abs(apr_long) < min_abs_apr_each_side or abs(apr_short) < min_abs_apr_each_side:
            continue

        oi_long = float(long_market.get("open_interest") or 0.0)
        oi_short = float(short_market.get("open_interest") or 0.0)
        vol_long = float(long_market.get("volume_24h") or 0.0)
        vol_short = float(short_market.get("volume_24h") or 0.0)

        if min_oi > 0.0 and (oi_long < min_oi or oi_short < min_oi):
            continue

        if min_volume_24h > 0.0 and (vol_long < min_volume_24h or vol_short < min_volume_24h):
            continue

        if spread < min_spread_apr_percent:
            continue

        results.append(
            {
                "canonical_symbol": c_symbol,
                "spread_apr_percent": spread,
                "long_market": {
                    "exchange": long_market.get("exchange"),
                    "symbol": long_market.get("symbol"),
                    "apr_percent": apr_long,
                    "funding_rate": float(long_market.get("funding_rate") or 0.0),
                    "open_interest": oi_long,
                    "volume_24h": vol_long,
                },
                "short_market": {
                    "exchange": short_market.get("exchange"),
                    "symbol": short_market.get("symbol"),
                    "apr_percent": apr_short,
                    "funding_rate": float(short_market.get("funding_rate") or 0.0),
                    "open_interest": oi_short,
                    "volume_24h": vol_short,
                },
                "all_markets": items,
            }
        )

    results.sort(key=lambda r: r.get("spread_apr_percent", 0.0), reverse=True)
    return results[:limit]


# -------------------------
# VENTANAS (1h, 8h, 24h, 3d, 7d, 15d, 31d)
# -------------------------

common_exchange = Query(
    default=None,
    description="Filtra por exchange (Hyperliquid, Backpack, ...)",
)
common_canonical = Query(
    default=None,
    description="Filtra por canonical_symbol (BTC, ETH, PIPE, ...)",
)
common_min_abs_apr = Query(
    default=0.0,
    ge=0.0,
    description="Filtra por |APR| mínimo (ej: 10 => solo |APR|>=10)",
)
common_limit = Query(
    default=500,
    ge=1,
    le=5000,
    description="Máximo número de mercados devueltos",
)


@app.get("/funding/1h", response_model=List[Dict[str, Any]])
async def funding_1h(
    exchange: Optional[str] = common_exchange,
    canonical_symbol: Optional[str] = common_canonical,
    min_abs_apr_percent: float = common_min_abs_apr,
    limit: int = common_limit,
):
    return await _funding_window_endpoint(
        window_hours=1.0,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
        min_abs_apr_percent=min_abs_apr_percent,
        limit=limit,
    )


@app.get("/funding/8h", response_model=List[Dict[str, Any]])
async def funding_8h(
    exchange: Optional[str] = common_exchange,
    canonical_symbol: Optional[str] = common_canonical,
    min_abs_apr_percent: float = common_min_abs_apr,
    limit: int = common_limit,
):
    return await _funding_window_endpoint(
        window_hours=8.0,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
        min_abs_apr_percent=min_abs_apr_percent,
        limit=limit,
    )


@app.get("/funding/24h", response_model=List[Dict[str, Any]])
async def funding_24h(
    exchange: Optional[str] = common_exchange,
    canonical_symbol: Optional[str] = common_canonical,
    min_abs_apr_percent: float = common_min_abs_apr,
    limit: int = common_limit,
):
    return await _funding_window_endpoint(
        window_hours=24.0,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
        min_abs_apr_percent=min_abs_apr_percent,
        limit=limit,
    )


@app.get("/funding/3d", response_model=List[Dict[str, Any]])
async def funding_3d(
    exchange: Optional[str] = common_exchange,
    canonical_symbol: Optional[str] = common_canonical,
    min_abs_apr_percent: float = common_min_abs_apr,
    limit: int = common_limit,
):
    return await _funding_window_endpoint(
        window_hours=72.0,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
        min_abs_apr_percent=min_abs_apr_percent,
        limit=limit,
    )


@app.get("/funding/7d", response_model=List[Dict[str, Any]])
async def funding_7d(
    exchange: Optional[str] = common_exchange,
    canonical_symbol: Optional[str] = common_canonical,
    min_abs_apr_percent: float = common_min_abs_apr,
    limit: int = common_limit,
):
    return await _funding_window_endpoint(
        window_hours=7 * 24.0,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
        min_abs_apr_percent=min_abs_apr_percent,
        limit=limit,
    )


@app.get("/funding/15d", response_model=List[Dict[str, Any]])
async def funding_15d(
    exchange: Optional[str] = common_exchange,
    canonical_symbol: Optional[str] = common_canonical,
    min_abs_apr_percent: float = common_min_abs_apr,
    limit: int = common_limit,
):
    return await _funding_window_endpoint(
        window_hours=15 * 24.0,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
        min_abs_apr_percent=min_abs_apr_percent,
        limit=limit,
    )


@app.get("/funding/31d", response_model=List[Dict[str, Any]])
async def funding_31d(
    exchange: Optional[str] = common_exchange,
    canonical_symbol: Optional[str] = common_canonical,
    min_abs_apr_percent: float = common_min_abs_apr,
    limit: int = common_limit,
):
    return await _funding_window_endpoint(
        window_hours=31 * 24.0,
        exchange=exchange,
        canonical_symbol=canonical_symbol,
        min_abs_apr_percent=min_abs_apr_percent,
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
