# 🌐 API Gateway - TheFundingHouse

API Gateway basado en **FastAPI** que actúa como punto de entrada único para todos los microservicios de TheFundingHouse.

## 🚀 Características

- ✅ **Proxy transparente** a microservicios
- ✅ **CORS** configurado
- ✅ **Logging** de todas las requests
- ✅ **Health checks** completos
- ✅ **Documentación automática** (Swagger/OpenAPI)
- ✅ **Manejo de errores** robusto
- ✅ **Timeouts** configurables (30s)
- ✅ **Shutdown graceful**
- ✅ **Validación de datos** con Pydantic

## 📋 Requisitos

- Python 3.9+
- pip

## 🔧 Instalación

```bash
# Crear entorno virtual
python3 -m venv venv

# Activar entorno virtual
source venv/bin/activate  # En macOS/Linux
# venv\Scripts\activate   # En Windows

# Instalar dependencias
pip install -r requirements.txt

# Copiar archivo de configuración
cp .env.example .env
```

## ⚙️ Configuración

Edita el archivo `.env`:

```bash
API_GATEWAY_PORT=3000
NODE_ENV=development
DATA_INGESTION_SERVICE_URL=http://localhost:8001
WALLET_USER_SERVICE_URL=http://localhost:8002
ARBITRAGE_STRATEGY_SERVICE_URL=http://localhost:8003
ALLOWED_ORIGINS=http://localhost:8080
LOG_LEVEL=INFO
```

## 🏃 Ejecutar

### Modo Desarrollo (con hot-reload)
```bash
uvicorn main:app --reload --port 3000
```

### Modo Producción
```bash
uvicorn main:app --host 0.0.0.0 --port 3000 --workers 4
```

### Con el script Python
```bash
python main.py
```

## 📡 Endpoints

### Básicos
- `GET /` - Endpoint raíz
- `GET /health` - Health check del gateway
- `GET /api/v1/status` - Status del servicio
- `GET /docs` - Documentación Swagger automática
- `GET /redoc` - Documentación ReDoc automática

### Proxies a Microservicios
- `/api/v1/data-ingestion/*` → Data Ingestion Service (puerto 8001)
- `/api/v1/wallet-user/*` → Wallet User Service (puerto 8002)
- `/api/v1/strategy/*` → Arbitrage Strategy Service (puerto 8003)

## 🧪 Testing

```bash
# Health check
curl http://localhost:3000/health

# Status
curl http://localhost:3000/api/v1/status

# Ejemplo de proxy
curl http://localhost:3000/api/v1/data-ingestion/funding-rates?limit=20
```

## 📖 Documentación Automática

Una vez iniciado el servidor, visita:
- **Swagger UI**: http://localhost:3000/docs
- **ReDoc**: http://localhost:3000/redoc

## 🏗️ Arquitectura

```
Frontend (8080)
      ↓
API Gateway (3000) ← Este servicio
      ↓
      ├── Data Ingestion Service (8001)
      ├── Wallet User Service (8002)
      └── Arbitrage Strategy Service (8003)
```

## 🔒 Características de Seguridad

- CORS configurable por ambiente
- Timeouts en requests (30s)
- Límites de conexión
- Validación de tipos con Pydantic
- Manejo robusto de errores

## 📝 Logging

Todos los logs incluyen:
- Timestamp
- Método HTTP
- Path
- Status code
- Errores detallados

Formato:
```
[2025-11-22 01:35:46] [INFO] [GW] GET /api/v1/data-ingestion/funding-rates
[2025-11-22 01:35:46] [INFO] [PROXY] → GET http://localhost:8001/funding-rates
[2025-11-22 01:35:46] [INFO] [PROXY] ← GET /funding-rates - 200
```

## 🚦 Health Check Response

```json
{
  "status": "ok",
  "timestamp": "2025-11-22T00:35:46.123Z",
  "uptime": 123.45,
  "environment": "development",
  "services": {}
}
```

## ⚠️ Manejo de Errores

- `502 Bad Gateway` - Servicio downstream no disponible
- `504 Gateway Timeout` - Servicio no respondió a tiempo
- `404 Not Found` - Ruta no existe
- `500 Internal Server Error` - Error interno

## 🔄 Shutdown Graceful

El servidor cierra todas las conexiones activas correctamente al recibir `SIGTERM` o `SIGINT` (Ctrl+C).

## 📦 Dependencias Principales

- **FastAPI** - Framework web moderno
- **Uvicorn** - ASGI server de alto rendimiento
- **httpx** - Cliente HTTP async para proxying
- **Pydantic** - Validación de datos y settings

## 🤝 Desarrollo

El servidor en modo desarrollo (`NODE_ENV=development`) incluye:
- Hot-reload automático
- Logs más verbosos
- Stack traces en errores
- CORS sin restricciones

## 📄 Licencia

TheFundingHouse © 2025
