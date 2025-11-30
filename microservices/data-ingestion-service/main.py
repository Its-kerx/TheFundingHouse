import os
from datetime import datetime, timedelta
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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


@app.get("/funding-rates", response_model=List[FundingRate])
async def get_funding_rates(limit: int = 20):
    """
    Devuelve funding rates reales de Hyperliquid.
    De momento:
      - No hay filtros
      - No hay base de datos
      - Solo leemos del adaptador y devolvemos los primeros 'limit'
    """
    try:
        # 1) Pedimos todos los mercados al adaptador
        markets_data = await hyperliquid_adapter.get_all_market_data(
            include_spread=True  # asumimos que el adaptador calcula spread
        )

        results: List[FundingRate] = []

        # 2) Nos quedamos solo con los primeros 'limit' mercados
        for m in markets_data[:limit]:
            # m debería tener estos campos:
            # symbol, exchange, timestamp, funding_rate, open_interest,
            # apr, spread, volume_24h
            fr = FundingRate(
                exchange_id=m["exchange"],
                pair=m["symbol"],
                timestamp=m["timestamp"],
                funding_rate=m["funding_rate"],
                # Por ahora, next_funding_time = ahora + 8h (simplificado)
                next_funding_time=datetime.utcnow() + timedelta(hours=8),
                # estimated_funding_rate = funding actual (también simplificado)
                estimated_funding_rate=m["funding_rate"],
                open_interest=m["open_interest"],
                apr=m["apr"],
                spread=m["spread"],
                volume_24h=m["volume_24h"],
                data_source=f"Real {m['exchange']} API",
            )
            results.append(fr)

        return results

    except Exception as e:
        print(f"Error in get_funding_rates: {e}")
        raise HTTPException(status_code=500, detail="Error fetching funding rates")


# --- EJECUCIÓN DIRECTA (para pruebas locales) ---
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
