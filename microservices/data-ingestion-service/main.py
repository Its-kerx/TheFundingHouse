import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from adapters.backpack import BackpackAdapter
from adapters.hyperliquid import HyperliquidAdapter

load_dotenv()

app = FastAPI(
    title="Data Ingestion Service",
    description="Microservicio para la ingestión de funding rates y su histórico.",
    version="0.2.0",
    openapi_url="/openapi.json",
)

# -------------------------
# Configuración Mongo
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

hyper_adapter = HyperliquidAdapter()
backpack_adapter = BackpackAdapter()

FUNDING_INTERVAL_HOURS_DEFAULT = 8.0


# -------------------------
# Helpers
# -------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_symbol_from_snapshot(snap: Dict[str, Any]) -> str:
    """
    Intenta generar un canonical_symbol común entre exchanges.

    Ejemplos:
      - Backpack:  "kLUNC_USDC_PERP" -> "kLUNC"
      - Backpack:  "BTC_USDC_PERP"   -> "BTC"
      - Hyper:     "kLUNC-USD"       -> "kLUNC"
      - Hyper:     "BTC-USD"         -> "BTC"
      - Si no se reconoce, devuelve symbol tal cual.
    """
    symbol = str(snap.get("symbol") or "")

    # Hyperliquid suele usar 'ASSET-USD'
    if symbol.endswith("-USD"):
        return symbol[:-4]

    # Backpack suele usar 'ASSET_USDC_PERP'
    if symbol.endswith("_USDC_PERP"):
        return symbol.replace("_USDC_PERP", "")

    if symbol.endswith("_PERP"):
        return symbol[:-5]

    return symbol


async def insert_funding_timeseries(snapshot: Dict[str, Any]) -> None:
    """
    Inserta una fila en funding_timeseries con campos normalizados.
    """
    if funding_ts_col is None:
        raise RuntimeError("Mongo funding_ts_col not initialized")

    funding_rate = float(snapshot.get("funding_rate") or 0.0)
    timestamp = snapshot.get("timestamp") or now_utc()

    doc = {
        "exchange": snapshot.get("exchange"),
        "symbol": snapshot.get("symbol"),
        "canonical_symbol": snapshot.get("canonical_symbol"),
        "funding_rate": funding_rate,
        "funding_interval_hours": float(
            snapshot.get("funding_interval_hours") or FUNDING_INTERVAL_HOURS_DEFAULT
        ),
        "timestamp": timestamp,
        "mark_price": snapshot.get("mark_price"),
        "index_price": snapshot.get("index_price") or snapshot.get("oracle_price"),
        "open_interest": snapshot.get("open_interest"),
        "raw": snapshot.get("raw"),
        "inserted_at": now_utc(),
    }

    await funding_ts_col.insert_one(doc)


async def upsert_current_and_ts(col, snapshot: Dict[str, Any]) -> None:
    """
    Actualiza la colección *_current y añade entrada en funding_timeseries.
    """
    if col is None:
        raise RuntimeError("Mongo current_col not initialized")

    # canonical_symbol común
    canonical = snapshot.get("canonical_symbol") or canonical_symbol_from_snapshot(
        snapshot
    )

    normalized = {
        **snapshot,
        "canonical_symbol": canonical,
        "funding_rate": float(snapshot.get("funding_rate") or 0.0),
        "funding_interval_hours": float(
            snapshot.get("funding_interval_hours") or FUNDING_INTERVAL_HOURS_DEFAULT
        ),
        "timestamp": snapshot.get("timestamp") or now_utc(),
    }

    key = {
        "exchange": normalized["exchange"],
        "symbol": normalized["symbol"],
    }

    await col.update_one(key, {"$set": normalized}, upsert=True)
    await insert_funding_timeseries(normalized)


# -------------------------
# Eventos de arranque
# -------------------------

@app.on_event("startup")
async def startup_event():
    global mongo_client, db, hyper_current_col, backpack_current_col, funding_ts_col

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]

    hyper_current_col = db[COL_HYPER_CURRENT]
    backpack_current_col = db[COL_BACKPACK_CURRENT]
    funding_ts_col = db[COL_FUNDING_TS]

    # Índices básicos para histórico
    try:
        await funding_ts_col.create_index(
            [("canonical_symbol", 1), ("exchange", 1), ("timestamp", -1)]
        )
        await funding_ts_col.create_index([("timestamp", -1)])
    except Exception:
        pass


@app.on_event("shutdown")
async def shutdown_event():
    global mongo_client
    if mongo_client is not None:
        mongo_client.close()


# -------------------------
# Endpoints
# -------------------------

@app.get("/status", response_model=dict)
async def get_status():
    return {
        "status": "ok",
        "service": "data-ingestion",
        "version": "0.2.0",
        "timestamp": now_utc().isoformat(),
    }


@app.post("/backpack/refresh", response_model=dict)
async def refresh_backpack():
    """
    Refresca TODOS los mercados perp de Backpack y guarda:
      - funding_backpack_current
      - funding_timeseries
    """
    if backpack_current_col is None:
        raise HTTPException(500, "Mongo not initialized")

    try:
        snapshots: List[Dict[str, Any]] = await backpack_adapter.get_all_market_data()
    except Exception as e:
        raise HTTPException(502, f"Error calling Backpack API: {e}")

    ok = 0
    for snap in snapshots:
        try:
            snap["exchange"] = "Backpack"
            await upsert_current_and_ts(backpack_current_col, snap)
            ok += 1
        except Exception:
            # en producción: log
            continue

    return {
        "exchange": "Backpack",
        "markets_processed": len(snapshots),
        "successful": ok,
    }



@app.post("/hyperliquid/refresh", response_model=dict)
async def refresh_hyperliquid():
    """
    Refresca TODOS los mercados perp de Hyperliquid y guarda:
      - funding_hyperliquid_current
      - funding_timeseries
    """
    if hyper_current_col is None:
        raise HTTPException(500, "Mongo not initialized")

    try:
        snapshots: List[Dict[str, Any]] = await hyper_adapter.get_all_market_data()
    except Exception as e:
        raise HTTPException(502, f"Error calling Hyperliquid API: {e}")

    ok = 0
    for snap in snapshots:
        try:
            snap["exchange"] = "Hyperliquid"
            await upsert_current_and_ts(hyper_current_col, snap)
            ok += 1
        except Exception:
            continue

    return {
        "exchange": "Hyperliquid",
        "markets_processed": len(snapshots),
        "successful": ok,
    }

#############################
@app.post("/refresh/grouped-by-token", response_model=dict)
async def refresh_grouped_by_token() -> Dict[str, Dict[str, Any]]:
    """
    Llama a TODOS los exchanges soportados (por ahora Backpack + Hyperliquid),
    obtiene los snapshots de mercado y devuelve un JSON agrupado por token.

    Estructura de salida:
    {
      "BTC": {
        "Backpack": {
          "exchange": "Backpack",
          "symbol": "BTC_USDC_PERP",
          "funding_rate": ...,
          "timestamp": ...,
          "open_interest": ...,
          "volume_24h": ...,
          "price": ...
        },
        "Hyperliquid": {
          "exchange": "Hyperliquid",
          "symbol": "BTC-USD",
          ...
        }
      },
      "MERL": {
        "Hyperliquid": { ... }
      },
      ...
    }
    """

    grouped: Dict[str, Dict[str, Any]] = {}

    # -------- Backpack --------
    try:
        backpack_markets: List[Dict[str, Any]] = await backpack_adapter.get_all_market_data()
    except Exception as e:
        backpack_markets = []
        print(f"Error al leer Backpack: {e}")

    for snap in backpack_markets:
        token = canonical_symbol_from_snapshot(snap)
        if not token:
            continue

        exchange_name = "Backpack"

        price = (
            snap.get("mark_price")
            or snap.get("index_price")
            or snap.get("oracle_price")
        )

        payload = {
            "exchange": exchange_name,
            "symbol": snap.get("symbol"),
            "funding_rate": snap.get("funding_rate"),
            "timestamp": snap.get("timestamp"),
            "open_interest": snap.get("open_interest"),
            "volume_24h": snap.get("volume_24h"),
            "price": price,
        }

        if token not in grouped:
            grouped[token] = {}
        grouped[token][exchange_name] = payload

    # -------- Hyperliquid --------
    try:
        hyper_markets: List[Dict[str, Any]] = await hyper_adapter.get_all_market_data()
    except Exception as e:
        hyper_markets = []
        print(f"Error al leer Hyperliquid: {e}")

    for snap in hyper_markets:
        token = canonical_symbol_from_snapshot(snap)
        if not token:
            continue

        exchange_name = "Hyperliquid"

        price = (
            snap.get("mark_price")
            or snap.get("index_price")
            or snap.get("oracle_price")
        )

        payload = {
            "exchange": exchange_name,
            "symbol": snap.get("symbol"),
            "funding_rate": snap.get("funding_rate"),
            "timestamp": snap.get("timestamp"),
            "open_interest": snap.get("open_interest"),
            "volume_24h": snap.get("volume_24h"),
            "price": price,
        }

        if token not in grouped:
            grouped[token] = {}
        grouped[token][exchange_name] = payload

    return grouped





if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8001)),
    )
