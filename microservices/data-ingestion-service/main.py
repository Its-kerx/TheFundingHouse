import os
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from adapters.hyperliquid import HyperliquidAdapter
from adapters.backpack import BackpackAdapter

load_dotenv()

app = FastAPI(
    title="Data Ingestion Service",
    description="Microservicio para la ingestión de funding rates y su histórico.",
    version="0.3.0",
)

# --- Config Mongo ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")

COL_HYPER_CURRENT = "funding_hyperliquid_current"
COL_BACKPACK_CURRENT = "funding_backpack_current"
COL_FUNDING_TS = "funding_timeseries"

# --- Config cron de refresco ---
REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "300"))

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
hyper_current_col = None
backpack_current_col = None
funding_ts_col = None

background_task: Optional[asyncio.Task] = None

# --- Adapters ---
hyper_adapter = HyperliquidAdapter()
backpack_adapter = BackpackAdapter()


# ---------- HELPERS DE TIEMPO / APR ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def apr_from_funding(funding_rate_8h: float) -> float:
    """
    funding_rate_8h en tanto por uno (ej: 0.0001 == 0.01% por periodo de 8h).
    APR = rate * 3 * 365 * 100
    """
    return funding_rate_8h * 3 * 365 * 100.0


# ---------- MONGO STARTUP / SHUTDOWN ----------

@app.on_event("startup")
async def startup_event():
    """
    Inicializa cliente de Mongo y colecciones + arranca el loop periódico.
    """
    global mongo_client, db, hyper_current_col, backpack_current_col, funding_ts_col, background_task

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]

    hyper_current_col = db[COL_HYPER_CURRENT]
    backpack_current_col = db[COL_BACKPACK_CURRENT]
    funding_ts_col = db[COL_FUNDING_TS]

    # Índices recomendados para la colección histórica
    try:
        await funding_ts_col.create_index(
            [("canonical_symbol", 1), ("exchange", 1), ("timestamp", -1)]
        )
        await funding_ts_col.create_index([("timestamp", -1)])
    except Exception:
        # Si ya existen / no podemos crearlos, no rompemos el servicio
        pass

    # Arrancar el loop periódico
    background_task = asyncio.create_task(periodic_refresh_loop())
    print(
        f"[data-ingestion] Started periodic refresh loop "
        f"every {REFRESH_INTERVAL_SECONDS} seconds"
    )


@app.on_event("shutdown")
async def shutdown_event():
    """
    Cierra cliente de Mongo y para el loop periódico.
    """
    global mongo_client, background_task

    if background_task is not None:
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass

    if mongo_client is not None:
        mongo_client.close()


@app.get("/status", response_model=dict)
async def get_status():
    """
    Estado simple del microservicio.
    """
    return {
        "status": "ok",
        "service": "data-ingestion",
        "version": "0.3.0",
        "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
    }


# ---------- INSERCIÓN EN HISTÓRICO ----------

async def insert_funding_timeseries(snapshot: Dict[str, Any]) -> None:
    """
    Inserta un documento en funding_timeseries a partir de un snapshot normalizado.

    Campos esperados en snapshot:
      - exchange
      - symbol
      - canonical_symbol (opcional)
      - funding_rate
      - timestamp (datetime)
      - mark_price (opcional)
      - index_price / oracle_price (opcional)
      - open_interest (opcional)
    """
    if funding_ts_col is None:
        raise RuntimeError("Mongo funding_ts_col not initialized")

    doc = {
        "exchange": snapshot["exchange"],
        "symbol": snapshot["symbol"],
        "canonical_symbol": snapshot.get("canonical_symbol"),

        "funding_rate": float(snapshot["funding_rate"]),
        "funding_interval_hours": 8,  # tanto Hyperliquid como Backpack usan 8h

        "mark_price": snapshot.get("mark_price"),
        # preferimos index_price si viene, si no oracle_price
        "index_price": snapshot.get("index_price") or snapshot.get("oracle_price"),
        "open_interest": snapshot.get("open_interest"),

        "timestamp": snapshot["timestamp"],
        "inserted_at": now_utc(),
    }

    await funding_ts_col.insert_one(doc)


async def upsert_current_and_ts(
    current_col,
    snapshot: Dict[str, Any],
) -> None:
    """
    Actualiza la colección *_current y añade entrada en funding_timeseries.
    """
    if current_col is None:
        raise RuntimeError("Mongo current_col not initialized")

    key = {
        "exchange": snapshot["exchange"],
        "symbol": snapshot["symbol"],
    }

    # Normalizamos algunos campos mínimos
    normalized = {
        **snapshot,
        "funding_rate": float(snapshot["funding_rate"]),
    }

    await current_col.update_one(
        key,
        {"$set": normalized},
        upsert=True,
    )

    await insert_funding_timeseries(normalized)


# ---------- LÓGICA INTERNA DE REFRESCO ----------

async def _refresh_backpack_internal() -> Dict[str, Any]:
    """
    Lógica común para Backpack: usada por el endpoint y por el cron.
    """
    if backpack_current_col is None:
        raise RuntimeError("Mongo backpack_current_col not initialized")

    try:
        snapshots: List[Dict[str, Any]] = await backpack_adapter.get_all_market_data()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error calling Backpack API: {e}")

    inserted = 0
    for snap in snapshots:
        try:
            await upsert_current_and_ts(backpack_current_col, snap)
            inserted += 1
        except Exception:
            # en producción, loguear símbolo concreto
            continue

    return {
        "exchange": "Backpack",
        "markets_processed": len(snapshots),
        "successful": inserted,
    }


async def _refresh_hyperliquid_internal() -> Dict[str, Any]:
    """
    Lógica común para Hyperliquid: usada por el endpoint y por el cron.
    """
    if hyper_current_col is None:
        raise RuntimeError("Mongo hyper_current_col not initialized")

    try:
        snapshots: List[Dict[str, Any]] = await hyper_adapter.get_all_market_data()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error calling Hyperliquid API: {e}")

    inserted = 0
    for snap in snapshots:
        try:
            await upsert_current_and_ts(hyper_current_col, snap)
            inserted += 1
        except Exception:
            continue

    return {
        "exchange": "Hyperliquid",
        "markets_processed": len(snapshots),
        "successful": inserted,
    }


# ---------- LOOP PERIÓDICO ----------

async def periodic_refresh_loop() -> None:
    """
    Loop en background que refresca periódicamente Hyperliquid + Backpack.
    """
    while True:
        try:
            start = now_utc()
            print(f"[data-ingestion] Periodic refresh tick at {start.isoformat()}")

            # Backpack
            try:
                backpack_summary = await _refresh_backpack_internal()
                print(f"[data-ingestion] Backpack refresh: {backpack_summary}")
            except HTTPException as e:
                print(f"[data-ingestion] Backpack refresh error: {e.detail}")
            except Exception as e:
                print(f"[data-ingestion] Backpack refresh error: {e}")

            # Hyperliquid
            try:
                hyper_summary = await _refresh_hyperliquid_internal()
                print(f"[data-ingestion] Hyperliquid refresh: {hyper_summary}")
            except HTTPException as e:
                print(f"[data-ingestion] Hyperliquid refresh error: {e.detail}")
            except Exception as e:
                print(f"[data-ingestion] Hyperliquid refresh error: {e}")

        except Exception as e:
            # Protección extra por si algo raro burbujea
            print(f"[data-ingestion] Unexpected error in periodic loop: {e}")

        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


# ---------- ENDPOINTS DE REFRESH MANUAL ----------

@app.post("/backpack/refresh", response_model=dict)
async def refresh_backpack():
    """
    Refresca TODOS los mercados perp de Backpack de forma manual.
    """
    summary = await _refresh_backpack_internal()
    return summary


@app.post("/hyperliquid/refresh", response_model=dict)
async def refresh_hyperliquid():
    """
    Refresca TODOS los mercados perp de Hyperliquid de forma manual.
    """
    summary = await _refresh_hyperliquid_internal()
    return summary


# ---------- ARRANQUE LOCAL ----------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8001)))
