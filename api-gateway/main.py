"""
TheFundingHouse API Gateway
FastAPI-based API Gateway para enrutar requests a microservicios
"""

import os
import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Cargar variables de entorno
load_dotenv()

# Configuración de logging
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Variables de entorno
API_GATEWAY_PORT = int(os.getenv('API_GATEWAY_PORT', '3000'))
NODE_ENV = os.getenv('NODE_ENV', 'development')
DATA_INGESTION_SERVICE_URL = os.getenv('DATA_INGESTION_SERVICE_URL', 'http://localhost:8001')
WALLET_USER_SERVICE_URL = os.getenv('WALLET_USER_SERVICE_URL', 'http://localhost:8002')
STRATEGY_SERVICE_URL = os.getenv('ARBITRAGE_STRATEGY_SERVICE_URL', 'http://localhost:8003')
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*').split(',') if os.getenv('ALLOWED_ORIGINS') != '*' else ['*']

# Cliente HTTP global para reusar conexiones
http_client: Optional[httpx.AsyncClient] = None
start_time = time.time()


# Lifespan para gestión de recursos
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestiona el ciclo de vida de la aplicación"""
    global http_client
    
    # Startup
    logger.info("=" * 60)
    logger.info("🚀 API Gateway de TheFundingHouse")
    logger.info("=" * 60)
    logger.info(f"📍 Puerto:       {API_GATEWAY_PORT}")
    logger.info(f"🌍 URL:          http://localhost:{API_GATEWAY_PORT}")
    logger.info(f"🔧 Environment:  {NODE_ENV}")
    logger.info(f"💚 Health:       http://localhost:{API_GATEWAY_PORT}/health")
    logger.info(f"📖 Docs:         http://localhost:{API_GATEWAY_PORT}/docs")
    logger.info("=" * 60)
    logger.info("📡 Services:")
    logger.info(f"   - Data Ingestion: {DATA_INGESTION_SERVICE_URL}")
    logger.info(f"   - Wallet/User:    {WALLET_USER_SERVICE_URL}")
    logger.info(f"   - Strategy:       {STRATEGY_SERVICE_URL}")
    logger.info("=" * 60)
    
    # Crear cliente HTTP con timeout
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
    )
    
    yield
    
    # Shutdown
    logger.info("🛑 Cerrando API Gateway gracefully...")
    if http_client:
        await http_client.aclose()
    logger.info("✅ Servidor cerrado correctamente")


# Crear aplicación FastAPI
app = FastAPI(
    title="TheFundingHouse API Gateway",
    description="Punto de entrada común para los microservicios de TheFundingHouse",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ['*'] else ['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware de logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log de todas las requests"""
    timestamp = datetime.utcnow().isoformat()
    logger.info(f"[GW] {request.method} {request.url.path}")
    
    response = await call_next(request)
    
    logger.info(f"[GW] ← {request.method} {request.url.path} - {response.status_code}")
    return response


# Modelos Pydantic
class HealthResponse(BaseModel):
    status: str
    timestamp: str
    uptime: float
    environment: str
    services: dict = {}


class StatusResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: str


# Endpoints básicos
@app.get("/")
async def root():
    """Endpoint raíz"""
    return {"message": "API Gateway de TheFundingHouse operativo"}


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check completo del gateway"""
    return HealthResponse(
        status="ok",
        timestamp=datetime.utcnow().isoformat(),
        uptime=round(time.time() - start_time, 2),
        environment=NODE_ENV,
        services={}  # Opcional: agregar checks de servicios downstream
    )


@app.get("/api/v1/status", response_model=StatusResponse)
async def status():
    """Status del API Gateway"""
    return StatusResponse(
        status="ok",
        service="API Gateway",
        version="1.0.0",
        timestamp=datetime.utcnow().isoformat()
    )


# Función auxiliar para proxy
async def proxy_request(
    request: Request,
    target_url: str,
    service_name: str
) -> Response:
    """
    Proxy genérico para redirigir requests a microservicios
    
    Args:
        request: Request original de FastAPI
        target_url: URL del servicio destino
        service_name: Nombre del servicio para logging
    
    Returns:
        Response del microservicio
    """
    if not http_client:
        raise HTTPException(status_code=503, detail="HTTP client not initialized")
    
    # Construir la URL completa
    path = request.url.path
    query_string = str(request.url.query)
    full_url = f"{target_url}{path}"
    if query_string:
        full_url = f"{full_url}?{query_string}"
    
    logger.info(f"[PROXY] → {request.method} {full_url}")
    
    try:
        # Obtener el body si existe
        body = await request.body()
        
        # Hacer la request al microservicio
        response = await http_client.request(
            method=request.method,
            url=full_url,
            headers=dict(request.headers),
            content=body if body else None,
        )
        
        logger.info(f"[PROXY] ← {request.method} {path} - {response.status_code}")
        
        # Devolver la respuesta
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.headers.get("content-type")
        )
        
    except httpx.TimeoutException:
        logger.error(f"[PROXY ERROR] Timeout calling {service_name}: {full_url}")
        raise HTTPException(
            status_code=504,
            detail=f"Gateway timeout: {service_name} no respondió a tiempo"
        )
    except httpx.ConnectError:
        logger.error(f"[PROXY ERROR] Connection error to {service_name}: {full_url}")
        raise HTTPException(
            status_code=502,
            detail=f"Bad Gateway: No se pudo conectar a {service_name}"
        )
    except Exception as e:
        logger.error(f"[PROXY ERROR] {service_name}: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Bad Gateway: Error al contactar {service_name}"
        )


# Proxy a Data Ingestion Service
@app.api_route(
    "/api/v1/data-ingestion/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
)
async def proxy_data_ingestion(request: Request, path: str):
    """Proxy a Data Ingestion Service"""
    if not http_client:
        raise HTTPException(status_code=503, detail="HTTP client not initialized")
    
    # Construir la URL limpia (sin el prefijo /api/v1/data-ingestion)
    clean_path = f"/{path}" if path else "/"
    query_string = str(request.url.query)
    full_url = f"{DATA_INGESTION_SERVICE_URL}{clean_path}"
    if query_string:
        full_url = f"{full_url}?{query_string}"
    
    logger.info(f"[PROXY] → {request.method} {full_url}")
    
    try:
        body = await request.body()
        
        response = await http_client.request(
            method=request.method,
            url=full_url,
            headers=dict(request.headers),
            content=body if body else None,
        )
        
        logger.info(f"[PROXY] ← {request.method} {clean_path} - {response.status_code}")
        
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.headers.get("content-type")
        )
        
    except httpx.TimeoutException:
        logger.error(f"[PROXY ERROR] Timeout calling Data Ingestion: {full_url}")
        raise HTTPException(status_code=504, detail="Gateway timeout")
    except httpx.ConnectError:
        logger.error(f"[PROXY ERROR] Connection error to Data Ingestion: {full_url}")
        raise HTTPException(status_code=502, detail="Service not available")
    except Exception as e:
        logger.error(f"[PROXY ERROR] Data Ingestion: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Error: {str(e)}")



# Proxy a Wallet User Service
@app.api_route(
    "/api/v1/wallet-user/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
)
async def proxy_wallet_user(request: Request, path: str):
    """Proxy a Wallet User Service"""
    return await proxy_request(request, WALLET_USER_SERVICE_URL, "Wallet User")


# Proxy a Arbitrage Strategy Service
@app.api_route(
    "/api/v1/strategy/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
)
async def proxy_strategy(request: Request, path: str):
    """Proxy a Arbitrage Strategy Service"""
    return await proxy_request(request, STRATEGY_SERVICE_URL, "Strategy")


# Manejador global de excepciones
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Manejador global de excepciones"""
    logger.error(f"[ERROR] {request.method} {request.url.path}: {str(exc)}")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": str(exc) if NODE_ENV == "development" else "Ha ocurrido un error",
            "timestamp": datetime.utcnow().isoformat()
        }
    )


# Manejador de 404
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Manejador de rutas no encontradas"""
    logger.warning(f"[404] {request.method} {request.url.path}")
    
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not Found",
            "message": "La ruta solicitada no existe",
            "path": str(request.url.path),
            "timestamp": datetime.utcnow().isoformat()
        }
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=API_GATEWAY_PORT,
        reload=NODE_ENV == "development",
        log_level="info"
    )
