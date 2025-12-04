import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

from adapters.hyperliquid import HyperliquidAdapter
from adapters.backpack import BackpackAdapter  # <--- NUEVO

load_dotenv()

app = FastAPI(
    title="Data Ingestion Service",
    description="Microservicio para la ingestión de funding rates reales.",
    version="0.1.0",
)

# --- Configuración Mongo ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")

HYPER_COL_NAME = "funding_hyperliquid_current"
BACKPACK_COL_NAME = "funding_backpack_current"  # <--- NUEVO

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
hyper_col = None
backpack_col = None  # <--- NUEVO


# --- MODELO API (si lo usas en algún endpoint de lectura) ---
class FundingRate(BaseModel):
    exchange_id: str
    pair: str
    timestamp: datetime
    funding_rate: float
    next_funding_time: Optional[datetime] = None
    estimated_funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    apr: Optional[float] = None
    spread: Optional[float] = None
    volume_24h: Optional[float] = None
    mark_price: Optional[float] = None
    oracle_price: Optional[float] = None
    data_source: str = "Real API"


# --- ADAPTADORES ---
hyperliquid_adapter = HyperliquidAdapter()
backpack_adapter = BackpackAdapter()  # <--- NUEVO


# --- STARTUP: conectar a Mongo y preparar colecciones ---
@app.on_event("startup")
async def startup_event():
    global mongo_client, db, hyper_col, backpack_col

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    hyper_col = db[HYPER_COL_NAME]
    backpack_col = db[BACKPACK_COL_NAME]  # <--- NUEVO


# --- HELPER: refrescar Hyperliquid (ya lo tenías parecido) ---
async def refresh_hyperliquid_markets() -> None:
    if hyper_col is None:
        raise RuntimeError("hyper_col not initialized")

    markets = await hyperliquid_adapter.get_all_market_data(include_spread=True)

    now = datetime.utcnow()
    docs: List[Dict[str, Any]] = []
    for m in markets:
        docs.append(
            {
                "exchange": m["exchange"],
                "symbol": m["symbol"],
                "funding_rate": float(m.get("funding_rate") or 0.0),
                "timestamp": m.get("timestamp") or now,
                "next_funding_time": m.get("next_funding_time"),
                "open_interest": m.get("open_interest"),
                "volume_24h": m.get("volume_24h"),
                "mark_price": m.get("mark_price"),
                "oracle_price": m.get("oracle_price"),
                "spread": m.get("spread"),
                "data_source": "Real Hyperliquid API",
                "last_updated_at": now,
                "raw": m.get("raw"),
            }
        )

    await hyper_col.delete_many({})
    if docs:
        await hyper_col.insert_many(docs)


# --- HELPER NUEVO: refrescar Backpack ---
async def refresh_backpack_markets() -> None:
    """
    Descarga snapshot de todos los mercados de Backpack y los guarda
    en la colección funding_backpack_current.
    """
    if backpack_col is None:
        raise RuntimeError("backpack_col not initialized")

    markets = await backpack_adapter.get_all_market_data()  # <- IMPORTANTE: await

    now = datetime.utcnow()
    docs: List[Dict[str, Any]] = []

    for m in markets:
        docs.append(
            {
                "exchange": m["exchange"],
                "symbol": m["symbol"],
                "funding_rate": float(m.get("funding_rate") or 0.0),
                "timestamp": m.get("timestamp") or now,
                "next_funding_time": m.get("next_funding_time"),
                "open_interest": m.get("open_interest"),
                "volume_24h": m.get("volume_24h"),
                "mark_price": m.get("mark_price"),
                "index_price": m.get("index_price"),
                "data_source": "Real Backpack API",
                "last_updated_at": now,
                "raw": m.get("raw"),
            }
        )

    await backpack_col.delete_many({})
    if docs:
        await backpack_col.insert_many(docs)


# --- ENDPOINTS BÁSICOS ---
@app.get("/status", response_model=dict)
async def get_status():
    return {
        "status": "ok",
        "service": "Data Ingestion Service",
        "version": "0.1.0",
        "message": "Microservicio operativo.",
    }


# --- Hyperliquid endpoints que ya tenías ---
@app.post("/hyperliquid/refresh")
async def hyperliquid_refresh():
    try:
        await refresh_hyperliquid_markets()
        return {"status": "ok"}
    except Exception as e:
        # Esto te mostrará el detalle del error en el 500
        raise HTTPException(status_code=500, detail=f"Error refreshing Hyperliquid: {e}")


@app.get("/hyperliquid/markets")
async def get_hyperliquid_markets(limit: int = 20):
    if hyper_col is None:
        raise HTTPException(status_code=500, detail="DB not initialized")

    cursor = hyper_col.find({}).sort("symbol", 1).limit(limit)
    docs: List[Dict[str, Any]] = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        docs.append(doc)
    return docs


# --- NUEVOS ENDPOINTS BACKPACK ---
@app.post("/backpack/refresh")
async def backpack_refresh():
    try:
        await refresh_backpack_markets()
        return {"status": "ok"}
    except Exception as e:
        # aqui verás el motivo del 500 si vuelve a fallar
        raise HTTPException(status_code=500, detail=f"Error refreshing Backpack: {e}")


@app.get("/backpack/markets")
async def get_backpack_markets(limit: int = 20):
    if backpack_col is None:
        raise HTTPException(status_code=500, detail="DB not initialized")

    cursor = backpack_col.find({}).sort("symbol", 1).limit(limit)
    docs: List[Dict[str, Any]] = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        docs.append(doc)
    return docs


# --- EJECUCIÓN LOCAL (no se usa dentro de Docker, pero no molesta) ---
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8001)))
