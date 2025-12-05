import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

app = FastAPI(
    title="Funding Analytics Service",
    description="Microservicio para analizar funding/APR a partir de Mongo.",
    version="0.2.0",
)

# -------------------------
# Configuración MongoDB
# -------------------------

MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")
COL_FUNDING_TS = "funding_timeseries"

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
funding_ts_col = None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def apr_from_funding(funding_rate_8h: float) -> float:
    """
    funding_rate_8h en tanto por uno (ej: 0.0001 = 0.01% por periodo de 8h).
    APR = rate * 3 * 365 * 100
    """
    return funding_rate_8h * 3 * 365 * 100.0


# -------------------------
# Startup / Status
# -------------------------

@app.on_event("startup")
async def startup_event() -> None:
    """
    Inicializa cliente de Mongo y colección.
    """
    global mongo_client, db, funding_ts_col

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    funding_ts_col = db[COL_FUNDING_TS]

    # Índices recomendados (si ya existen no pasa nada)
    try:
        await funding_ts_col.create_index(
            [("exchange", 1), ("symbol", 1), ("timestamp", -1)]
        )
        await funding_ts_col.create_index(
            [("canonical_symbol", 1), ("timestamp", -1)]
        )
        await funding_ts_col.create_index([("timestamp", -1)])
    except Exception:
        pass


@app.get("/status", response_model=Dict[str, Any])
async def get_status() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "funding-analytics",
        "version": "0.2.0",
    }


# -------------------------
# Helpers de consulta
# -------------------------

async def _get_latest_snapshots() -> List[Dict[str, Any]]:
    """
    Devuelve el último snapshot por (exchange, symbol).
    Usa aggregation en Mongo:
      - Ordena por timestamp desc
      - Agrupa por exchange+symbol
      - Se queda con el más reciente
    """
    if funding_ts_col is None:
        raise RuntimeError("Mongo funding_ts_col not initialized")

    cursor = funding_ts_col.aggregate(
        [
            {"$sort": {"timestamp": -1}},
            {
                "$group": {
                    "_id": {"exchange": "$exchange", "symbol": "$symbol"},
                    "doc": {"$first": "$$ROOT"},
                }
            },
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$sort": {"timestamp": -1}},
        ]
    )

    results: List[Dict[str, Any]] = []
    async for doc in cursor:
        doc["_id"] = str(doc.get("_id", ""))  # por si algún día devolvemos el id
        results.append(doc)
    return results


def _canonical_from_symbol(doc: Dict[str, Any]) -> str:
    """
    Obtiene canonical_symbol para agrupar exchages.
    Prioriza el campo 'canonical_symbol' si existe;
    si no, usa la parte antes del '-' del símbolo.
    """
    canonical = doc.get("canonical_symbol")
    if canonical:
        return canonical
    symbol = str(doc.get("symbol", ""))
    return symbol.split("-")[0] if "-" in symbol else symbol


# -------------------------
# GET /funding/latest
# -------------------------

@app.get("/funding/latest", response_model=List[Dict[str, Any]])
async def get_latest_funding() -> List[Dict[str, Any]]:
    """
    Devuelve el último snapshot de funding para cada (exchange, symbol),
    incluyendo el APR calculado.
    """
    try:
        snapshots = await _get_latest_snapshots()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mongo error: {e}")

    enriched: List[Dict[str, Any]] = []
    for doc in snapshots:
        fr = float(doc.get("funding_rate", 0.0) or 0.0)
        apr = apr_from_funding(fr)

        enriched.append(
            {
                "exchange": doc.get("exchange"),
                "symbol": doc.get("symbol"),
                "canonical_symbol": _canonical_from_symbol(doc),
                "funding_rate": fr,
                "apr_percent": apr,
                "funding_interval_hours": doc.get("funding_interval_hours", 8),
                "timestamp": doc.get("timestamp"),
                "mark_price": doc.get("mark_price"),
                "index_price": doc.get("index_price"),
                "open_interest": doc.get("open_interest"),
            }
        )

    return enriched


# -------------------------
# GET /funding/best
# -------------------------

@app.get("/funding/best", response_model=List[Dict[str, Any]])
async def get_best_funding(
    limit: int = Query(50, ge=1, le=500),
    min_abs_apr_percent: float = Query(0.0, ge=0.0),
) -> List[Dict[str, Any]]:
    """
    Devuelve los mejores mercados por |APR| (sin distinguir dirección),
    a partir del último snapshot por (exchange, symbol).
    """
    latest = await get_latest_funding()

    # Añadimos |APR| y filtramos por umbral
    for item in latest:
        item["abs_apr_percent"] = abs(item["apr_percent"])

    filtered = [
        x for x in latest if x["abs_apr_percent"] >= min_abs_apr_percent
    ]

    # Ordenamos descendentemente por |APR|
    filtered.sort(key=lambda x: x["abs_apr_percent"], reverse=True)

    return filtered[:limit]


# -------------------------
# GET /funding/arbitrage/pairs
# -------------------------

@app.get("/funding/arbitrage/pairs", response_model=List[Dict[str, Any]])
async def get_arbitrage_pairs(
    limit: int = Query(50, ge=1, le=500),
    min_abs_spread_percent: float = Query(0.0, ge=0.0),
) -> List[Dict[str, Any]]:
    """
    Devuelve pares de arbitraje entre exchanges para cada canonical_symbol.

    Lógica:
      1) Tomamos el último snapshot por (exchange, symbol).
      2) Agrupamos por canonical_symbol (ej: WLFI, BTC, ETH...).
      3) Para cada canonical con >= 2 exchanges:
           - Calculamos APR en cada exchange.
           - Elegimos:
               * long: exchange con APR más bajo (pagar menos funding).
               * short: exchange con APR más alto (cobrar más funding).
           - spread = apr_short - apr_long.
      4) Filtramos por |spread| >= min_abs_spread_percent.
      5) Devolvemos ordenado por |spread| desc y limitado a 'limit'.
    """
    latest = await get_latest_funding()

    # Agrupamos por canonical_symbol
    by_canonical: Dict[str, List[Dict[str, Any]]] = {}
    for item in latest:
        canonical = item["canonical_symbol"]
        by_canonical.setdefault(canonical, []).append(item)

    pairs: List[Dict[str, Any]] = []

    for canonical, entries in by_canonical.items():
        if len(entries) < 2:
            continue  # necesitamos mínimo 2 exchanges

        # Ordenamos por APR ascendente: primero APR más bajo
        entries_sorted = sorted(entries, key=lambda x: x["apr_percent"])

        long_leg = entries_sorted[0]   # APR más bajo
        short_leg = entries_sorted[-1] # APR más alto

        apr_long = float(long_leg["apr_percent"])
        apr_short = float(short_leg["apr_percent"])
        spread = apr_short - apr_long
        abs_spread = abs(spread)

        if abs_spread < min_abs_spread_percent:
            continue

        pair = {
            "canonical_symbol": canonical,
            "long": {
                "exchange": long_leg["exchange"],
                "symbol": long_leg["symbol"],
                "apr_percent": apr_long,
                "funding_rate": long_leg["funding_rate"],
                "timestamp": long_leg["timestamp"],
            },
            "short": {
                "exchange": short_leg["exchange"],
                "symbol": short_leg["symbol"],
                "apr_percent": apr_short,
                "funding_rate": short_leg["funding_rate"],
                "timestamp": short_leg["timestamp"],
            },
            "spread_apr_percent": spread,
            "abs_spread_apr_percent": abs_spread,
            # Marca temporal “más vieja” de las dos patas (para saber lo fresco que es el par)
            "pair_timestamp": min(
                long_leg["timestamp"], short_leg["timestamp"]
            ),
        }

        pairs.append(pair)

    # Ordenamos por |spread| desc y limitamos
    pairs.sort(key=lambda x: x["abs_spread_apr_percent"], reverse=True)

    return pairs[:limit]


# -------------------------
# Ejecutar local (opcional)
# -------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8002)),
    )
