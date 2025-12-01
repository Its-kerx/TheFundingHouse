import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

app = FastAPI(
    title="Funding Analytics Service",
    description="Microservicio de análisis de funding (APR, rankings, etc.)",
    version="0.1.0",
)

# --- MongoDB (solo lectura) ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")
HYPER_COL_NAME = "funding_hyperliquid_current"

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
hyper_col = None

@app.on_event("startup")
async def startup_event():
    global mongo_client, db, hyper_col
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    hyper_col = db[HYPER_COL_NAME]

@app.get("/status", response_model=dict)
async def get_status():
    return {
        "status": "ok",
        "service": "Funding Analytics Service",
        "version": "0.1.0",
    }


def compute_apr_from_funding(funding_rate_8h: float) -> float:
    """
    funding_rate_8h: funding por periodo (8h) en tanto por uno.
    APR = rate * 3 (periodos/día) * 365 (días) * 100 (para %)
    """
    return funding_rate_8h * 3 * 365 * 100.0


@app.get("/funding/hyperliquid/by-symbol")
async def get_hyperliquid_by_symbol(symbol: str):
    """
    Devuelve el último snapshot crudo + APR para un símbolo concreto (ej: '0G-USD').
    """
    if hyper_col is None:
        raise HTTPException(status_code=500, detail="DB not initialized")

    doc = await hyper_col.find_one(
        {"exchange": "Hyperliquid", "symbol": symbol}
    )

    if not doc:
        raise HTTPException(status_code=404, detail="Symbol not found")

    # Convertir _id a string
    doc["_id"] = str(doc["_id"])

    funding_rate = float(doc.get("funding_rate", 0.0))
    apr = compute_apr_from_funding(funding_rate)

    return {
        "exchange": doc["exchange"],
        "symbol": doc["symbol"],
        "timestamp": doc["timestamp"],
        "funding_rate": funding_rate,
        "funding_rate_percent_8h": funding_rate * 100.0,
        "apr_percent": apr,
        "open_interest": doc.get("open_interest"),
        "volume_24h": doc.get("volume_24h"),
        "mark_price": doc.get("mark_price"),
        "oracle_price": doc.get("oracle_price"),
        "raw": doc.get("raw"),
    }


@app.get("/funding/hyperliquid/top-apr")
async def get_hyperliquid_top_apr(
    limit: int = Query(20, ge=1, le=200),
    sort_desc: bool = True,
):
    """
    Devuelve los mercados de Hyperliquid ordenados por APR (derivado de funding_rate).
    - limit: número de mercados.
    - sort_desc: True = APR más alto primero, False = más bajo.
    """
    if hyper_col is None:
        raise HTTPException(status_code=500, detail="DB not initialized")

    # Leemos TODOS los docs necesarios y calculamos APR en Python
    cursor = hyper_col.find({"exchange": "Hyperliquid"})

    items: List[Dict[str, Any]] = []
    async for doc in cursor:
        funding_rate = float(doc.get("funding_rate", 0.0))
        apr = compute_apr_from_funding(funding_rate)

        items.append(
            {
                "symbol": doc["symbol"],
                "funding_rate": funding_rate,
                "funding_rate_percent_8h": funding_rate * 100.0,
                "apr_percent": apr,
                "open_interest": doc.get("open_interest"),
                "volume_24h": doc.get("volume_24h"),
                "mark_price": doc.get("mark_price"),
                "oracle_price": doc.get("oracle_price"),
            }
        )

    # Ordenamos en memoria por APR
    items.sort(key=lambda x: x["apr_percent"], reverse=sort_desc)

    return items[:limit]

# EJECUCIÓN LOCAL
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8002)))
