"""
TheFundingHouse API Gateway (versión simplificada)
- /health        -> estado del gateway
- /api/v1/data-ingestion/*  -> proxy a microservicio data-ingestion
"""

import logging

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

GATEWAY_PORT = 3000
DATA_INGESTION_URL = "http://localhost:8001"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [GW] %(message)s",
)
logger = logging.getLogger("tfh-gateway")

app = FastAPI(
    title="TheFundingHouse API Gateway (simple)",
    description="Gateway mínimo para enrutar a data-ingestion",
    version="0.1.0",
)

# CORS muy abierto para desarrollo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health básico del gateway"""
    return {"status": "ok", "service": "gateway"}


# Middleware de logging muy simple
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"{request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"← {request.method} {request.url.path} - {response.status_code}")
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

    logger.info(f"[PROXY] → {request.method} {target_url}")

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
