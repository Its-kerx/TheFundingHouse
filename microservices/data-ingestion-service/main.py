import os
from datetime import datetime, timedelta
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

# --- Cargar variables de entorno ---
load_dotenv()

# --- Configuración básica ---
SERVICE_PORT = int(os.getenv("PORT", 8001))

app = FastAPI(
    title="Data Ingestion Service",
    description="Microservicio sencillo para obtener funding rates reales de Hyperliquid.",
    version="0.1.0",
    openapi_url="/api/v1/data-ingestion/openapi.json",
)


# --- MODELOS (lo que devuelve la API) ---
class FundingRate(BaseModel):
    exchange_id: str
    pair: str
    timestamp: datetime
    funding_rate: float
    next_funding_time: datetime
    estimated_funding_rate: float
    open_interest: float
    apr: float
    spread: float
    volume_24h: float
    data_source: str = "Real API"


# --- ADAPTADOR DE EXCHANGE (Hyperliquid) ---
# Importamos tu adaptador ya existente
from adapters import HyperliquidAdapter

hyperliquid_adapter = HyperliquidAdapter()

# --- Conexión MongoDB ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")
MONGO_COL = os.getenv("MONGO_COL", "funding_rates")
# Colección específica para Hyperliquid
HYPER_COL_NAME = "funding_hyperliquid_current"

mongo_client: AsyncIOMotorClient | None = None
db = None
funding_col = None
hyper_col = None


@app.on_event("startup")
async def startup_event():
    global mongo_client, db, funding_col, hyper_col
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    funding_col = db[MONGO_COL]
    hyper_col = db[HYPER_COL_NAME]
    # Índice para tener un documento por exchange+symbol
    await funding_col.create_index([("exchange", 1), ("symbol", 1)], unique=True)
    await hyper_col.create_index([("exchange", 1), ("symbol", 1)], unique=True)

async def refresh_hyperliquid_markets() -> None:
    """
    1) Pide TODOS los mercados a Hyperliquid (datos crudos desde el adaptador).
    2) Los guarda en la colección hyper_col, un doc por (exchange, symbol).
    3) Elimina campos antiguos que no queremos (apr, spread, estimated_funding_rate).
    """
    if hyper_col is None:
        raise RuntimeError("Hyperliquid collection not initialized")

    markets: List[Dict[str, Any]] = await hyperliquid_adapter.get_all_market_data_cached(
        include_spread=False,
        max_age_seconds=0,  # fuerza datos frescos del adaptador
    )

    if not markets:
        print(">>> No markets received from Hyperliquid")
        return

    operations: List[UpdateOne] = []
    now = datetime.utcnow()

    for m in markets:
        doc = {
            "exchange": m["exchange"],
            "symbol": m["symbol"],
            "timestamp": m["timestamp"],
            "funding_rate": m["funding_rate"],
            "next_funding_time": m.get("next_funding_time"),
            "open_interest": m["open_interest"],
            "volume_24h": m["volume_24h"],
            "mark_price": m["mark_price"],
            "oracle_price": m.get("oracle_price"),
            "data_source": "Real Hyperliquid API",
            "raw": m.get("raw"),
            "last_updated_at": now,
        }

        operations.append(
            UpdateOne(
                {"exchange": doc["exchange"], "symbol": doc["symbol"]},
                {
                    "$set": doc,
                    # MUY IMPORTANTE: limpiamos los campos viejos
                    "$unset": {
                        "apr": "",
                        "spread": "",
                        "estimated_funding_rate": "",
                    },
                },
                upsert=True,
            )
        )

    if operations:
        result = await hyper_col.bulk_write(operations, ordered=False)
        print(
            f">>> Hyperliquid Mongo updated: "
            f"{result.matched_count} matched, "
            f"{result.modified_count} modified, "
            f"{len(result.upserted_ids)} upserted"
        )


# --- ENDPOINTS ---
@app.get("/status", response_model=dict)
async def get_status():
    """Estado básico del microservicio."""
    return {
        "status": "ok",
        "service": "Data Ingestion Service",
        "version": "0.1.0",
        "message": "Microservicio operativo.",
    }

@app.post("/hyperliquid/refresh")
async def hyperliquid_refresh():
    await refresh_hyperliquid_markets()
    return {"status": "ok"}


@app.get("/hyperliquid/markets")
async def get_hyperliquid_markets(limit: int = 20):
    if hyper_col is None:
        return {"error": "DB not initialized"}

    cursor = hyper_col.find({}).sort("symbol", 1).limit(limit)

    docs: List[Dict[str, Any]] = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        docs.append(doc)

    return docs


# --- EJECUCIÓN DIRECTA (para pruebas locales) ---
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
