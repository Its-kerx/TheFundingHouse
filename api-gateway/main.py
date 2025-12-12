"""
TheFundingHouse API Gateway (simplified)
- /health                        -> gateway status
- /api/v1/data-ingestion/*       -> proxy to data-ingestion microservice
- /api/funding/live              -> proxy to funding-analytics live endpoint
"""

import logging
import os

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

GATEWAY_PORT = 3000
DATA_INGESTION_URL = os.getenv("DATA_INGESTION_URL", "http://localhost:8001")
FUNDING_ANALYTICS_BASE_URL = os.getenv(
    "FUNDING_ANALYTICS_BASE_URL",
    "http://funding_analytics:8002",
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [GW] %(message)s",
)
logger = logging.getLogger("tfh-gateway")

app = FastAPI(
    title="TheFundingHouse API Gateway (simple)",
    description="Gateway minimo para enrutar a servicios internos",
    version="0.1.0",
)

# CORS abierto para desarrollo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health basico del gateway"""
    return {"status": "ok", "service": "gateway"}


# Middleware de logging muy simple
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"{request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"[DONE] {request.method} {request.url.path} - {response.status_code}")
    return response


@app.api_route(
    "/api/v1/data-ingestion/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_data_ingestion(request: Request, path: str) -> Response:
    """
    Proxy sencillo hacia data-ingestion.
    /api/v1/data-ingestion/status  ->  http://localhost:8001/status
    """
    target_url = f"{DATA_INGESTION_URL}/{path}".rstrip("/")
    if request.url.query:
        target_url += f"?{request.url.query}"

    logger.info(f"[PROXY] -> {request.method} {target_url}")

    try:
        body = await request.body()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                content=body or None,
                headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            )
    except httpx.RequestError as e:
        logger.error(f"[PROXY ERROR] {e}")
        raise HTTPException(status_code=502, detail="Error contactando data-ingestion")

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={
            k: v
            for k, v in resp.headers.items()
            if k.lower() not in {"content-length", "transfer-encoding", "connection"}
        },
        media_type=resp.headers.get("content-type"),
    )


@app.get("/api/funding/live")
async def proxy_funding_live(
    min_spread_apr_percent: float = 0.0,
    limit: int = 100,
):
    """
    Proxy to the funding-analytics /funding/live endpoint.
    """
    upstream_url = FUNDING_ANALYTICS_BASE_URL.rstrip("/") + "/funding/live"
    params = {
        "min_spread_apr_percent": min_spread_apr_percent,
        "limit": limit,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(upstream_url, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Error calling funding-analytics: {exc}",
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Funding live service returned error",
                "status": resp.status_code,
                "body": resp.text,
            },
        )

    data = resp.json()
    return JSONResponse(content=data)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"[ERROR] {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "message": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn

    logger.info(f"Iniciando API Gateway en http://localhost:{GATEWAY_PORT}")
    uvicorn.run("main:app", host="0.0.0.0", port=GATEWAY_PORT, reload=True)
