import os
import random
from datetime import datetime, timedelta
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

# Cargar variables de entorno del archivo .env
load_dotenv()

app = FastAPI(
    title="Data Ingestion Service",
    description="Microservicio para la ingestión y agregación de datos de funding rates de PERP-dexes.",
    version="1.0.0",
    openapi_url="/api/v1/data-ingestion/openapi.json",
)

# Configuración del puerto y Mongo
SERVICE_PORT = int(os.getenv("PORT", 8001))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")
MONGO_COL = os.getenv("MONGO_COL", "funding_rates")

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
col = None


# --- Modelos ---
# --- Modelos ---
class FundingRate(BaseModel):
    exchange_id: str
    pair: str
    timestamp: datetime
    funding_rate: float
    next_funding_time: datetime
    estimated_funding_rate: float
    open_interest: float
    # New fields for UI
    apr: float
    spread: float
    volume_24h: float
    data_source: str = "Simulated API"


class FundingIn(BaseModel):
    symbol: str = Field(..., examples=["BTC-USD", "ETH-USD"])
    exchange_long: str = Field(..., examples=["Hyperliquid"])
    exchange_short: str = Field(..., examples=["Backpack"])
    funding_long: float = Field(..., description="En % (ej. 0.02)")
    funding_short: float = Field(..., description="En % (ej. -0.05)")
    ts: int = Field(..., description="timestamp ms")


class FundingOut(FundingIn):
    id: Optional[str] = None
    # New fields for UI
    apr: float = 0.0
    spread: float = 0.0
    open_interest: float = 0.0
    volume_24h: float = 0.0


# --- SIMULACIÓN DE DATOS ---
# En un entorno real, esto haría llamadas a APIs de PERP-dexes externos.
def generate_simulated_funding_rate(exchange: str, pair: str) -> FundingRate:
    now = datetime.utcnow()
    funding_rate = round(random.uniform(-0.001, 0.001), 5)
    # Simulate APR based on funding rate (roughly funding * 3 * 365 * 100 for display)
    # This is just a dummy calculation for visual purposes
    apr = abs(funding_rate) * 3 * 365 * 100 
    
    return FundingRate(
        exchange_id=exchange,
        pair=pair,
        timestamp=now,
        funding_rate=funding_rate,  # Entre -0.1% y 0.1%
        next_funding_time=now + timedelta(hours=8),  # Cada 8 horas
        estimated_funding_rate=round(random.uniform(-0.0005, 0.0005), 5),
        open_interest=round(random.uniform(1_000_000, 50_000_000), 2),
        apr=round(apr, 2),
        spread=round(random.uniform(0.01, 0.05), 4),
        volume_24h=round(random.uniform(500_000, 10_000_000), 2),
        data_source=f"Simulated {exchange} API",
    )


# --- Ciclo de vida ---
@app.on_event("startup")
async def startup_event():
    global mongo_client, db, col
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]
    col = db[MONGO_COL]
    await col.create_index([("symbol", 1), ("ts", -1)])


@app.on_event("shutdown")
async def shutdown_event():
    if mongo_client:
        mongo_client.close()


# --- ENDPOINTS ---
@app.get("/status", response_model=dict)
async def get_status():
    """Retorna el estado de este microservicio."""
    return {
        "status": "ok",
        "service": "Data Ingestion Service",
        "version": "1.0.0",
        "message": "Microservicio de ingestión de datos operativo.",
    }


# Initialize exchange adapters
from adapters import HyperliquidAdapter

hyperliquid_adapter = HyperliquidAdapter()


@app.get("/funding-rates", response_model=list[FundingRate])
async def get_funding_rates(
    exchange: str = None, 
    pair: str = None, 
    limit: int = 20,
    search: str = None,
    min_apr: float = None,
    max_apr: float = None
):
    """
    Retorna las tasas de funding rates from real exchanges.
    Currently supports: Hyperliquid
    """
    results = []
    
    try:
        # For now, we only support Hyperliquid
        # Later we'll add more adapters and filter by exchange parameter
        
        # Get all markets from Hyperliquid
        markets = await hyperliquid_adapter.get_markets()
        
        # Filter by search term if provided
        if search:
            markets = [m for m in markets if search.lower() in m['symbol'].lower()]
        
        # Get market data for each market (up to limit)
        for market in markets[:min(len(markets), limit * 2)]:  # Fetch more than limit for filtering
            try:
                market_data = await hyperliquid_adapter.get_market_data(market['symbol'])
                
                if not market_data:
                    continue
                
                # Apply filters
                if min_apr is not None and market_data['apr'] < min_apr:
                    continue
                if max_apr is not None and market_data['apr'] > max_apr:
                    continue
                
                # Convert to FundingRate model
                funding_rate = FundingRate(
                    exchange_id=market_data['exchange'],
                    pair=market_data['symbol'],
                    timestamp=market_data['timestamp'],
                    funding_rate=market_data['funding_rate'],
                    next_funding_time=datetime.now() + timedelta(hours=8),  # Hyperliquid 8h cycle
                    estimated_funding_rate=market_data['funding_rate'],  # Use current as estimate
                    open_interest=market_data['open_interest'],
                    apr=market_data['apr'],
                    spread=market_data['spread'],
                    volume_24h=market_data['volume_24h'],
                    data_source=f"Real {market_data['exchange']} API"
                )
                
                results.append(funding_rate)
                
                if len(results) >= limit:
                    break
                    
            except Exception as e:
                print(f"Error fetching data for {market['symbol']}: {e}")
                continue
        
        return results
        
    except Exception as e:
        print(f"Error in get_funding_rates: {e}")
        # Fallback to empty list on error
        return []


@app.get("/db/health")
async def db_health():
    if not db or not col:
        raise HTTPException(status_code=503, detail="Mongo no inicializado")
    await db.command("ping")
    count = await col.estimated_document_count()
    return {"mongo": "ok", "collection": MONGO_COL, "count": count}


@app.post("/funding", response_model=FundingOut)
async def insert_funding(item: FundingIn):
    if not col:
        raise HTTPException(status_code=503, detail="Mongo no inicializado")
    doc = item.model_dump()
    res = await col.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    return doc


@app.get("/funding", response_model=List[FundingOut])
async def list_funding(symbol: Optional[str] = None, limit: int = 50):
    if not col:
        raise HTTPException(status_code=503, detail="Mongo no inicializado")
    query = {"symbol": symbol} if symbol else {}
    cursor = col.find(query).sort("ts", -1).limit(min(limit, 200))
    out: List[FundingOut] = []
    async for d in cursor:
        d["id"] = str(d["_id"])
        d.pop("_id", None)
        out.append(FundingOut(**d))
    return out


# Para ejecutar Uvicorn desde un script Python (útil para pruebas o en un futuro Docker)
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
