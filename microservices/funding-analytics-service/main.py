import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient

# ----------------------------------------------------------------------
# Configuración básica
# ----------------------------------------------------------------------

load_dotenv()

app = FastAPI(
    title="Funding Analytics Service",
    description=(
        "Microservicio de análisis de funding: APR por mercado y "
        "oportunidades de arbitraje delta-neutral entre exchanges."
    ),
    version="0.2.0",
)

# --- MongoDB ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")

# Mapear exchange -> colección con snapshot ACTUAL
# Cuando integres más exchanges, solo hay que añadir aquí.
EXCHANGE_COLLECTIONS: Dict[str, str] = {
    "Hyperliquid": "funding_hyperliquid_current",
    # "Binance": "funding_binance_current",
    # "Bybit": "funding_bybit_current",
    # ...
}

mongo_client: Optional[AsyncIOMotorClient] = None
db = None


@app.on_event("startup")
async def startup_event():
    global mongo_client, db
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------

def compute_apr_from_funding(funding_rate_8h: float) -> float:
    """
    funding_rate_8h: funding por periodo de 8h en tanto por uno.
    APR = rate * 3 (periodos/día) * 365 (días) * 100 (para %).
    """
    return funding_rate_8h * 3 * 365 * 100.0


async def load_all_funding_snapshots(
    include_exchanges: Optional[List[str]] = None,
    exclude_exchanges: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Lee los snapshots de funding de todas las colecciones definidas
    en EXCHANGE_COLLECTIONS y devuelve una lista unificada.
    """
    if db is None:
        raise RuntimeError("DB not initialized")

    include_set = set(include_exchanges) if include_exchanges else None
    exclude_set = set(exclude_exchanges) if exclude_exchanges else set()

    snapshots: List[Dict[str, Any]] = []

    for exchange_name, col_name in EXCHANGE_COLLECTIONS.items():
        # aplicar filtros de include/exclude a nivel de exchange
        if include_set is not None and exchange_name not in include_set:
            continue
        if exchange_name in exclude_set:
            continue

        col = db[col_name]
        cursor = col.find({})

        async for doc in cursor:
            snapshots.append(
                {
                    "exchange": doc.get("exchange", exchange_name),
                    "symbol": doc["symbol"],
                    "funding_rate": float(doc.get("funding_rate", 0.0)),
                    "open_interest": doc.get("open_interest"),
                    "volume_24h": doc.get("volume_24h"),
                    "mark_price": doc.get("mark_price"),
                    "oracle_price": doc.get("oracle_price"),
                    "timestamp": doc.get("timestamp"),
                }
            )

    return snapshots


# ----------------------------------------------------------------------
# Endpoints básicos
# ----------------------------------------------------------------------

@app.get("/status", response_model=dict)
async def get_status():
    """Estado básico del microservicio."""
    return {
        "status": "ok",
        "service": "Funding Analytics Service",
        "version": "0.2.0",
        "exchanges": list(EXCHANGE_COLLECTIONS.keys()),
    }


@app.get("/funding/snapshots")
async def list_funding_snapshots(
    limit: int = Query(50, ge=1, le=500),
    include_exchanges: Optional[List[str]] = Query(None),
    exclude_exchanges: Optional[List[str]] = Query(None),
):
    """
    Devuelve snapshots actuales (crudos + APR) por mercado,
    útil para debug o para una tabla simple de markets.
    """
    snapshots = await load_all_funding_snapshots(
        include_exchanges=include_exchanges,
        exclude_exchanges=exclude_exchanges,
    )

    # calcular APR por mercado
    items: List[Dict[str, Any]] = []
    for s in snapshots:
        f = s["funding_rate"]
        items.append(
            {
                "exchange": s["exchange"],
                "symbol": s["symbol"],
                "timestamp": s["timestamp"],
                "funding_rate_8h": f,
                "funding_rate_percent_8h": f * 100.0,
                "apr_percent": compute_apr_from_funding(f),
                "open_interest": s["open_interest"],
                "volume_24h": s["volume_24h"],
                "mark_price": s["mark_price"],
                "oracle_price": s["oracle_price"],
            }
        )

    # ordenar por APR absoluto (opcional: podrías cambiar criterio)
    items.sort(key=lambda x: x["apr_percent"], reverse=True)
    return items[:limit]


@app.get("/funding/by-symbol")
async def get_by_symbol(
    symbol: str,
    include_exchanges: Optional[List[str]] = Query(None),
    exclude_exchanges: Optional[List[str]] = Query(None),
):
    """
    Devuelve funding + APR por símbolo para TODAS las exchanges
    (útil para ver cómo está BTC-USD en todas las plataformas).
    """
    snapshots = await load_all_funding_snapshots(
        include_exchanges=include_exchanges,
        exclude_exchanges=exclude_exchanges,
    )

    filtered = [s for s in snapshots if s["symbol"] == symbol]
    if not filtered:
        raise HTTPException(status_code=404, detail="Symbol not found in any exchange")

    results = []
    for s in filtered:
        f = s["funding_rate"]
        results.append(
            {
                "exchange": s["exchange"],
                "symbol": s["symbol"],
                "timestamp": s["timestamp"],
                "funding_rate_8h": f,
                "funding_rate_percent_8h": f * 100.0,
                "apr_percent": compute_apr_from_funding(f),
                "open_interest": s["open_interest"],
                "volume_24h": s["volume_24h"],
                "mark_price": s["mark_price"],
                "oracle_price": s["oracle_price"],
            }
        )

    # ordenar por APR de ese símbolo
    results.sort(key=lambda x: x["apr_percent"], reverse=True)
    return results


# ----------------------------------------------------------------------
# Endpoint principal: TODAS las combinaciones long/short entre exchanges
# ----------------------------------------------------------------------

@app.get("/funding/arbitrage/all-pairs")
async def get_all_arbitrage_pairs(
    limit: int = Query(200, ge=1, le=2000),
    only_positive: bool = True,
    min_volume_24h: float = 0.0,
    min_open_interest: float = 0.0,
    include_exchanges: Optional[List[str]] = Query(None),
    exclude_exchanges: Optional[List[str]] = Query(None),
):
    """
    Devuelve TODAS las combinaciones (long_exchange, short_exchange) por símbolo,
    calculando el carry neto de funding y su APR anualizado.

    - Long en X, Short en Y:
        carry_8h = f_short - f_long
      (f > 0 => long paga, short cobra; f < 0 => long cobra, short paga)

    - only_positive:
        True  -> solo oportunidades con carry_8h > 0 (estrategia gana funding neto)
        False -> incluye también carry_8h <= 0 (por si quieres ver todo)

    - min_volume_24h / min_open_interest:
        Filtran por liquidez MÍNIMA en AMBAS patas.
    """
    snapshots = await load_all_funding_snapshots(
        include_exchanges=include_exchanges,
        exclude_exchanges=exclude_exchanges,
    )

    # Filtrar por liquidez mínima
    def liquid_enough(s: Dict[str, Any]) -> bool:
        v = s.get("volume_24h") or 0.0
        oi = s.get("open_interest") or 0.0
        return v >= min_volume_24h and oi >= min_open_interest

    snapshots = [s for s in snapshots if liquid_enough(s)]

    # Agrupar por símbolo
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in snapshots:
        by_symbol[s["symbol"]].append(s)

    opportunities: List[Dict[str, Any]] = []

    for symbol, markets in by_symbol.items():
        n = len(markets)
        if n < 2:
            continue  # con solo una exchange no hay arbitraje

        # Generar TODAS las combinaciones (long, short) con X != Y
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue

                long_mkt = markets[i]
                short_mkt = markets[j]

                f_long = long_mkt["funding_rate"]
                f_short = short_mkt["funding_rate"]

                # carry 8h de la estrategia delta-neutral:
                #   long en i => -f_long
                #   short en j => +f_short
                #   neto => f_short - f_long
                carry_8h = f_short - f_long

                if only_positive and carry_8h <= 0:
                    continue

                carry_apr = carry_8h * 3 * 365 * 100.0

                opportunities.append(
                    {
                        "symbol": symbol,
                        "long_exchange": long_mkt["exchange"],
                        "short_exchange": short_mkt["exchange"],
                        "funding_long_8h": f_long,
                        "funding_long_percent_8h": f_long * 100.0,
                        "funding_short_8h": f_short,
                        "funding_short_percent_8h": f_short * 100.0,
                        "carry_8h": carry_8h,
                        "carry_apr_percent": carry_apr,
                        "long_mark_price": long_mkt["mark_price"],
                        "short_mark_price": short_mkt["mark_price"],
                        "long_volume_24h": long_mkt["volume_24h"],
                        "short_volume_24h": short_mkt["volume_24h"],
                        "long_open_interest": long_mkt["open_interest"],
                        "short_open_interest": short_mkt["open_interest"],
                    }
                )

    # Ordenar por APR neto de la estrategia
    opportunities.sort(key=lambda x: x["carry_apr_percent"], reverse=True)
    return opportunities[:limit]


# ----------------------------------------------------------------------
# Ejecución local
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8002)))