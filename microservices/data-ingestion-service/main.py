import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from adapters.backpack import BackpackAdapter
from adapters.hyperliquid import HyperliquidAdapter
from adapters.dex_extended import DexExtendedAdapter
from adapters.pacifica import PacificaAdapter
from funding_history import (
    BASE_INTERVAL_HOURS,
    aggregate_to_8h,
    fetch_raw_funding_history,
)

load_dotenv()

app = FastAPI(
    title="Data Ingestion Service",
    description="Microservicio para la ingestión de funding rates y su histórico.",
    version="0.3.0",
    openapi_url="/openapi.json",
)

# -------------------------
# Configuración Mongo
# -------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:example@mongo:27017")
MONGO_DB = os.getenv("MONGO_DB", "tfh")

COL_HYPER_CURRENT = "funding_hyperliquid_current"
COL_BACKPACK_CURRENT = "funding_backpack_current"
COL_DEX_EXTENDED_CURRENT = "funding_dex_extended_current"
COL_PACIFICA_CURRENT = "funding_pacifica_current"
COL_FUNDING_TS = "funding_timeseries"

mongo_client: Optional[AsyncIOMotorClient] = None
db = None
hyper_current_col = None
backpack_current_col = None
dex_extended_current_col = None
pacifica_current_col = None
funding_ts_col = None

hyper_adapter = HyperliquidAdapter()
backpack_adapter = BackpackAdapter()
dex_extended_adapter = DexExtendedAdapter()
pacifica_adapter = PacificaAdapter()

FUNDING_INTERVAL_HOURS_DEFAULT = 1.0


# -------------------------
# Helpers
# -------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _compute_funding_metrics(
    rate: Any,
    interval_hours: Any,
) -> Dict[str, float]:
    funding_rate = float(rate or 0.0)
    interval = float(interval_hours or FUNDING_INTERVAL_HOURS_DEFAULT)
    if interval <= 0:
        interval = FUNDING_INTERVAL_HOURS_DEFAULT
    funding_percent = funding_rate * 100.0
    periods_per_year = (365.0 * 24.0) / interval
    apr_percent = funding_rate * periods_per_year * 100.0
    return {
        "funding_rate": funding_rate,
        "funding_interval_hours": interval,
        "funding_percent": funding_percent,
        "apr_percent": apr_percent,
    }


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


def _group_markets_by_token(
    exchange_name: str,
    markets: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for snap in markets:
        token = canonical_symbol_from_snapshot(snap)
        if not token:
            continue

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

        grouped.setdefault(token, {})[exchange_name] = payload
    return grouped


async def insert_funding_timeseries(snapshot: Dict[str, Any]) -> None:
    """
    Inserta una fila en funding_timeseries con campos normalizados.
    """
    if funding_ts_col is None:
        raise RuntimeError("Mongo funding_ts_col not initialized")

    metrics = _compute_funding_metrics(
        snapshot.get("funding_rate"),
        snapshot.get("funding_interval_hours"),
    )
    funding_rate = metrics["funding_rate"]
    interval_hours = metrics["funding_interval_hours"]
    funding_percent = metrics["funding_percent"]
    apr_percent = metrics["apr_percent"]
    timestamp = snapshot.get("timestamp") or now_utc()

    doc = {
        "exchange": snapshot.get("exchange"),
        "symbol": snapshot.get("symbol"),
        "canonical_symbol": snapshot.get("canonical_symbol"),
        "funding_rate": funding_rate,
        "funding_percent": funding_percent,
        "funding_interval_hours": interval_hours,
        "apr_percent": apr_percent,
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

    metrics = _compute_funding_metrics(
        snapshot.get("funding_rate"),
        snapshot.get("funding_interval_hours"),
    )
    normalized = {
        **snapshot,
        "canonical_symbol": canonical,
        "funding_rate": metrics["funding_rate"],
        "funding_interval_hours": metrics["funding_interval_hours"],
        "funding_percent": metrics["funding_percent"],
        "apr_percent": metrics["apr_percent"],
        "timestamp": snapshot.get("timestamp") or now_utc(),
    }

    key = {
        "exchange": normalized["exchange"],
        "symbol": normalized["symbol"],
    }

    await col.update_one(key, {"$set": normalized}, upsert=True)
    await insert_funding_timeseries(normalized)


async def _list_symbols_for_exchange(exchange_name: str) -> List[str]:
    """
    Devuelve la lista de sИmbolos normalizados para un exchange usando el adapter o la colecciИn *_current.
    """
    exchange = exchange_name.lower()
    symbols: List[str] = []

    try:
        if exchange == "backpack":
            markets = await backpack_adapter.get_markets()
            symbols = [m.get("symbol") for m in markets if m.get("symbol")]
        elif exchange == "hyperliquid":
            markets = await hyper_adapter.get_markets()
            symbols = [m.get("symbol") for m in markets if m.get("symbol")]
        elif exchange in {"dexextended", "dex-extended", "dex_extended", "extended"}:
            markets = await dex_extended_adapter.get_markets()
            symbols = [m.get("symbol") for m in markets if m.get("symbol")]
        elif exchange == "pacifica":
            markets = await pacifica_adapter.get_markets()
            symbols = [m.get("symbol") for m in markets if m.get("symbol")]
    except Exception:
        symbols = []

    # Fallback a colecciones *_current si no hay markets
    if not symbols:
        try:
            if exchange == "backpack":
                col = backpack_current_col
            elif exchange == "hyperliquid":
                col = hyper_current_col
            elif exchange in {"dexextended", "dex-extended", "dex_extended", "extended"}:
                col = dex_extended_current_col
            else:
                col = pacifica_current_col
            if col is not None:
                symbols = await col.distinct("symbol")
        except Exception:
            symbols = []

    # Quitamos duplicados manteniendo orden
    seen = set()
    deduped = []
    for s in symbols:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped


async def _bootstrap_funding_history_internal(days: int = 30) -> Dict[str, Any]:
    """
    Descarga histИrico de funding para cada exchange/sИmbolo y lo guarda en funding_timeseries (normalizado a 8h).
    """
    if funding_ts_col is None:
        raise HTTPException(500, "Mongo funding_timeseries not initialized")

    to_ts = now_utc()
    from_ts = to_ts - timedelta(days=days)
    summary: List[Dict[str, Any]] = []

    exchanges = [
        ("Hyperliquid", hyper_adapter, hyper_current_col),
        ("Backpack", backpack_adapter, backpack_current_col),
        ("DexExtended", dex_extended_adapter, dex_extended_current_col),
        ("Pacifica", pacifica_adapter, pacifica_current_col),
    ]

    for exchange_name, _adapter, _col in exchanges:
        symbols = await _list_symbols_for_exchange(exchange_name)
        inserted = 0

        for symbol in symbols:
            try:
                raw_events = await fetch_raw_funding_history(
                    exchange_name, symbol, from_ts, to_ts
                )
            except Exception:
                continue

            snapshots = aggregate_to_8h(raw_events)
            docs: List[Dict[str, Any]] = []
            canonical = canonical_symbol_from_snapshot({"symbol": symbol})

            for snap in snapshots:
                metrics = _compute_funding_metrics(
                    snap.get("funding_rate"),
                    snap.get("funding_interval_hours"),
                )
                funding_rate = metrics["funding_rate"]
                interval_hours = metrics["funding_interval_hours"]
                funding_percent = metrics["funding_percent"]
                apr_percent = metrics["apr_percent"]

                docs.append(
                    {
                        "exchange": exchange_name,
                        "symbol": symbol,
                        "canonical_symbol": canonical,
                        "funding_rate": funding_rate,
                        "funding_percent": funding_percent,
                        "funding_interval_hours": interval_hours,
                        "apr_percent": apr_percent,
                        "mark_price": snap.get("mark_price"),
                        "open_interest": snap.get("open_interest"),
                        "volume_24h": snap.get("volume_24h"),
                        "timestamp": snap.get("timestamp") or to_ts,
                        "inserted_at": now_utc(),
                    }
                )

            if docs:
                try:
                    await funding_ts_col.insert_many(docs, ordered=False)
                    inserted += len(docs)
                except Exception:
                    # Si hay duplicados o errores, seguimos con el resto
                    pass

        summary.append(
            {
                "exchange": exchange_name,
                "symbols_processed": len(symbols),
                "snapshots_inserted": inserted,
            }
        )

    return {
        "status": "ok",
        "days": days,
        "run_at": to_ts.isoformat(),
        "exchanges": summary,
    }


async def _maybe_bootstrap_on_startup():
    """
    Si funding_timeseries esta vacio o desactualizado, lanza bootstrap automatico (30 dias).
    """
    if funding_ts_col is None:
        return

    try:
        last = await funding_ts_col.find_one(sort=[("timestamp", -1)])
    except Exception:
        last = None

    now = now_utc()
    last_ts = None
    if last:
        last_ts = last.get("timestamp")
        if isinstance(last_ts, datetime) and last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)

    if not last_ts or last_ts < now - timedelta(days=3):
        print("[startup] No recent funding_timeseries, bootstrapping 30 days...", flush=True)
        try:
            await _bootstrap_funding_history_internal(days=30)
            print("[startup] Bootstrap finished", flush=True)
        except Exception:
            # en produccion se loggearia
            pass
    else:
        print("[startup] funding_timeseries is recent, skipping bootstrap", flush=True)


async def _funding_hourly_refresh_internal() -> Dict[str, Any]:
    """
    Wrapper para refrescos horarios (cron).
    """
    results = []

    try:
        res_bp = await refresh_backpack()
        results.append({"exchange": "Backpack", **res_bp})
    except Exception as e:
        results.append({"exchange": "Backpack", "error": str(e)})

    try:
        res_hl = await refresh_hyperliquid()
        results.append({"exchange": "Hyperliquid", **res_hl})
    except Exception as e:
        results.append({"exchange": "Hyperliquid", "error": str(e)})

    try:
        res_dx = await refresh_dex_extended()
        results.append({"exchange": "DexExtended", **res_dx})
    except Exception as e:
        results.append({"exchange": "DexExtended", "error": str(e)})

    try:
        res_pc = await refresh_pacifica()
        results.append({"exchange": "Pacifica", **res_pc})
    except Exception as e:
        results.append({"exchange": "Pacifica", "error": str(e)})

    return {
        "status": "ok",
        "run_at": now_utc().isoformat(),
        "exchanges": results,
    }


async def _hourly_loop_task():
    """
    Tarea de fondo para refresco horario.
    """
    while True:
        try:
            await _funding_hourly_refresh_internal()
        except Exception:
            pass
        await asyncio.sleep(3600)


# -------------------------
# Eventos de arranque
# -------------------------

@app.on_event("startup")
async def startup_event():
    global mongo_client, db, hyper_current_col, backpack_current_col, dex_extended_current_col, pacifica_current_col, funding_ts_col

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB]

    hyper_current_col = db[COL_HYPER_CURRENT]
    backpack_current_col = db[COL_BACKPACK_CURRENT]
    dex_extended_current_col = db[COL_DEX_EXTENDED_CURRENT]
    pacifica_current_col = db[COL_PACIFICA_CURRENT]
    funding_ts_col = db[COL_FUNDING_TS]

    # Índices básicos para histórico
    try:
        await funding_ts_col.create_index(
            [("canonical_symbol", 1), ("exchange", 1), ("timestamp", -1)]
        )
        await funding_ts_col.create_index([("timestamp", -1)])
    except Exception:
        pass

    await _maybe_bootstrap_on_startup()
    asyncio.create_task(_hourly_loop_task())
    print("[startup] data-ingestion-service ready", flush=True)


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
        "version": "0.3.0",
        "timestamp": now_utc().isoformat(),
    }


@app.post("/funding/bootstrap-history", response_model=dict)
async def bootstrap_funding_history(days: int = 30):
    """
    Descarga histИrico de funding (days dУas hacia atrаs), lo normaliza a 8h y lo guarda en funding_timeseries.
    Pensado para llamarse al arrancar la plataforma.
    """
    try:
        return await _bootstrap_funding_history_internal(days=days)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error en bootstrap de histИrico: {e}")


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

@app.post("/dex-extended/refresh", response_model=dict)
async def refresh_dex_extended():
    """
    Refresca TODOS los mercados perp de DEX Extended y guarda:
      - funding_dex_extended_current
      - funding_timeseries
    """
    if dex_extended_current_col is None:
        raise HTTPException(500, "Mongo not initialized")

    try:
        snapshots: List[Dict[str, Any]] = await dex_extended_adapter.get_all_market_data()
    except Exception as e:
        raise HTTPException(502, f"Error calling DEX Extended API: {e}")

    ok = 0
    for snap in snapshots:
        try:
            snap["exchange"] = "DexExtended"
            await upsert_current_and_ts(dex_extended_current_col, snap)
            ok += 1
        except Exception:
            continue

    return {
        "exchange": "DexExtended",
        "markets_processed": len(snapshots),
        "successful": ok,
    }


@app.post("/pacifica/refresh", response_model=dict)
async def refresh_pacifica():
    """
    Refresca TODOS los mercados perp de Pacifica y guarda:
      - funding_pacifica_current
      - funding_timeseries
    """
    if pacifica_current_col is None:
        raise HTTPException(500, "Mongo not initialized")

    try:
        snapshots: List[Dict[str, Any]] = await pacifica_adapter.get_all_market_data()
    except Exception as e:
        raise HTTPException(502, f"Error calling Pacifica API: {e}")

    ok = 0
    for snap in snapshots:
        try:
            snap["exchange"] = "Pacifica"
            await upsert_current_and_ts(pacifica_current_col, snap)
            ok += 1
        except Exception:
            continue

    return {
        "exchange": "Pacifica",
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

    backpack_grouped = _group_markets_by_token("Backpack", backpack_markets)
    for token, payloads in backpack_grouped.items():
        grouped.setdefault(token, {}).update(payloads)

    # -------- Hyperliquid --------
    try:
        hyper_markets: List[Dict[str, Any]] = await hyper_adapter.get_all_market_data()
    except Exception as e:
        hyper_markets = []
        print(f"Error al leer Hyperliquid: {e}")

    hyper_grouped = _group_markets_by_token("Hyperliquid", hyper_markets)
    for token, payloads in hyper_grouped.items():
        grouped.setdefault(token, {}).update(payloads)

    # -------- DEX Extended --------
    try:
        dex_markets: List[Dict[str, Any]] = await dex_extended_adapter.get_all_market_data()
    except Exception as e:
        dex_markets = []
        print(f"Error al leer DEX Extended: {e}")

    dex_grouped = _group_markets_by_token("DexExtended", dex_markets)
    for token, payloads in dex_grouped.items():
        grouped.setdefault(token, {}).update(payloads)

    # -------- Pacifica --------
    try:
        pacifica_markets: List[Dict[str, Any]] = await pacifica_adapter.get_all_market_data()
    except Exception as e:
        pacifica_markets = []
        print(f"Error al leer Pacifica: {e}")

    pacifica_grouped = _group_markets_by_token("Pacifica", pacifica_markets)
    for token, payloads in pacifica_grouped.items():
        grouped.setdefault(token, {}).update(payloads)

    return grouped





if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8001)),
    )
