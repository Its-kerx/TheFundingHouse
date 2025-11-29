# 🚀 Guía de Lanzamiento Local - TheFundingHouse

## 📋 Componentes de tu Aplicación

Tu aplicación tiene 4 partes principales:
1. **Docker** – Bases de datos MongoDB y PostgreSQL (solo estas dos se levantan con Compose)
2. **Data Ingestion Service** – Microservicio Python/FastAPI (puerto 8001)
3. **API Gateway** – Gateway Python/FastAPI (puerto 3000)
4. **Frontend** – Interfaz web HTML/CSS/JS (puerto 8080)

---

## ⚡ Inicio Rápido (TL;DR)

```bash
# 1. Iniciar bases de datos (MongoDB + PostgreSQL)
docker compose up -d mongo postgres

# 2. Iniciar Data Ingestion Service
cd microservices/data-ingestion-service
source venv/bin/activate
python main.py &

# 3. Iniciar API Gateway
cd ../../api-gateway
source venv/bin/activate
python main.py &

# 4. Iniciar Frontend
cd ../frontend
python3 -m http.server 8080

# 5. Abrir en navegador
open http://localhost:8080
```

---

## 📝 Pasos Detallados

### 🔵 Paso 1: Iniciar Docker (Bases de Datos)

```bash
# Ir al directorio raíz del proyecto (ajusta la ruta si tu copia está en otro lugar)
cd /Users/ikerdiez/Documents/TheFundingHouse2

# Asegúrate de usar Docker Compose v2 (comando `docker compose`)
docker compose pull

# Iniciar únicamente las bases de datos
docker compose up -d mongo postgres

# Verificar que están corriendo
docker compose ps
```

**Deberías ver:**
- `tfh-mongo` en puerto 27017
- `tfh-postgres` en puerto 5432

Este Compose ha sido ajustado para ejecutar únicamente esos dos servicios; el resto de la aplicación se arranca manualmente en los próximos pasos.

---

### 🟢 Paso 2: Iniciar Data Ingestion Service (Puerto 8001)

```bash
# Ir al directorio del servicio (desde la raíz del proyecto)
cd microservices/data-ingestion-service

# Activar entorno virtual
source venv/bin/activate

# Iniciar el servicio
python main.py
```

**Deberías ver:**
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001
```

**Deja esta terminal abierta** (el servicio queda corriendo aquí).

---

### 🔵 Paso 3: Iniciar API Gateway (Puerto 3000)

**Abre una NUEVA terminal** y ejecuta:

```bash
# Ir al directorio del API Gateway (desde la raíz del proyecto)
cd api-gateway

# Activar entorno virtual
source venv/bin/activate

# Iniciar el gateway
python main.py
```

**Deberías ver:**
```
============================================================
🚀 API Gateway de TheFundingHouse
============================================================
📍 Puerto:       3000
🌍 URL:          http://localhost:3000
🔧 Environment:  development
💚 Health:       http://localhost:3000/health
📖 Docs:         http://localhost:3000/docs
============================================================
```

**Deja esta terminal abierta** (el gateway queda corriendo aquí).

---

### 🟣 Paso 4: Iniciar Frontend (Puerto 8080)

**Abre una TERCERA terminal** y ejecuta:

```bash
# Ir al directorio del frontend (desde la raíz del proyecto)
cd frontend

# Iniciar servidor HTTP
python3 -m http.server 8080
```

**Deberías ver:**
```
Serving HTTP on :: port 8080 (http://[::]:8080/) ...
```

**Deja esta terminal abierta** (el servidor web queda corriendo aquí).

---

### 🌐 Paso 5: Abrir en el Navegador

Abre tu navegador en:
```
http://localhost:8080
```

O directamente desde la terminal:
```bash
open http://localhost:8080
```

---

## ✅ Verificación

### Verificar que todo funciona:

#### 1. Frontend
```bash
curl http://localhost:8080
# Debería retornar el HTML de la página
```

#### 2. API Gateway
```bash
curl http://localhost:3000/health
# Debería retornar: {"status":"ok",...}
```

#### 3. Data Ingestion Service
```bash
curl http://localhost:8001/status
# Debería retornar: {"status":"ok","service":"Data Ingestion Service"}
```

#### 4. Datos de funding rates
```bash
curl http://localhost:3000/api/v1/data-ingestion/funding-rates?limit=5
# Debería retornar un JSON con funding rates
```

---

## 🛑 Detener Todo

### Opción 1: Detener cada componente
En cada terminal donde tienes un servicio corriendo, presiona:
```
Ctrl + C
```

### Opción 2: Detener Docker
```bash
docker compose down
```

### Opción 3: Script para matar todos los procesos
```bash
# Matar Data Ingestion Service
pkill -f "python main.py"

# Matar API Gateway
pkill -f "uvicorn main:app"

# Matar Frontend
pkill -f "http.server 8080"

# Detener Docker
docker compose down
```

---

## 🔍 Troubleshooting

### "Puerto ya en uso"
```bash
# Ver qué está usando el puerto
lsof -i :8080   # Frontend
lsof -i :3000   # API Gateway
lsof -i :8001   # Data Ingestion

# Matar el proceso
kill -9 $(lsof -t -i:8080)  # Ejemplo para puerto 8080
```

### "No module named 'fastapi'"
```bash
# Instalar dependencias del Data Ingestion Service
cd microservices/data-ingestion-service
source venv/bin/activate
pip install fastapi uvicorn motor python-dotenv pydantic httpx

# O crear requirements.txt e instalar
pip install -r requirements.txt  # si existe
```

### "Docker no está corriendo"
```bash
# Iniciar Docker Desktop en macOS
open -a Docker

# Esperar unos segundos y luego
docker compose up -d
```

### "Connection refused a MongoDB"
Asegúrate de que Docker esté corriendo:
```bash
docker ps | grep mongo
# Debería mostrar tfh-mongo
```

---

## 📊 Arquitectura de Puertos

```
┌─────────────────────────────────────────────┐
│  Frontend       http://localhost:8080       │
└──────────────────────┬──────────────────────┘
                       │
                       ↓
┌──────────────────────────────────────────────┐
│  API Gateway    http://localhost:3000        │
└──────────────────────┬───────────────────────┘
                       │
       ┌───────────────┼──────────────┐
       │               │              │
       ↓               ↓              ↓
┌──────────┐    ┌──────────┐   ┌──────────┐
│ Data     │    │ Wallet   │   │ Strategy │
│ Ingest   │    │ User     │   │ Service  │
│ :8001    │    │ :8002    │   │ :8003    │
└────┬─────┘    └──────────┘   └──────────┘
     │
     ↓
┌─────────────────────────────────────────────┐
│  MongoDB        localhost:27017              │
│  PostgreSQL     localhost:5432               │
└──────────────────────────────────────────────┘
```

---

## 🎯 URLs Importantes

| Servicio | URL | Descripción |
|----------|-----|-------------|
| **Frontend** | http://localhost:8080 | Interfaz web principal |
| **Frontend Dashboard** | http://localhost:8080/dashboard.html | Dashboard |
| **API Gateway** | http://localhost:3000 | Gateway principal |
| **API Gateway Docs** | http://localhost:3000/docs | Swagger UI |
| **API Gateway Health** | http://localhost:3000/health | Health check |
| **Data Ingestion** | http://localhost:8001 | Microservicio directo |
| **Data Ingestion Status** | http://localhost:8001/status | Status del servicio |
| **MongoDB** | localhost:27017 | Base de datos |
| **PostgreSQL** | localhost:5432 | Base de datos |

---

## 💡 Tips

### Mantener servicios corriendo en background
En lugar de abrir 3 terminales, puedes usar `&` para ejecutarlos en background:

```bash
# Terminal 1
cd /Users/ikerdiez/Documents/TheFundingHouse2
docker compose up -d

# Terminal 2
cd microservices/data-ingestion-service
source venv/bin/activate
python main.py > logs/data-ingestion.log 2>&1 &

cd ../../api-gateway
source venv/bin/activate
python main.py > logs/api-gateway.log 2>&1 &

cd ../frontend
python3 -m http.server 8080 > logs/frontend.log 2>&1 &

# Ver logs
tail -f logs/*.log
```

### Auto-reload para desarrollo
Para que los cambios se reflejen automáticamente:

```bash
# API Gateway con hot-reload
cd api-gateway
source venv/bin/activate
uvicorn main:app --reload --port 3000

# Data Ingestion con hot-reload
cd microservices/data-ingestion-service
source venv/bin/activate
uvicorn main:app --reload --port 8001
```

---

## 🚀 Workflow Típico de Desarrollo

### Inicio del día:
```bash
# 1. Iniciar Docker
docker compose up -d

# 2. Iniciar backend (en background o en terminales separadas)
cd microservices/data-ingestion-service && source venv/bin/activate && python main.py &
cd ../../api-gateway && source venv/bin/activate && python main.py &
cd ../frontend && python3 -m http.server 8080 &

# 3. Abrir navegador
open http://localhost:8080
```

### Fin del día:
```bash
# Matar procesos Python
pkill -f "python main.py"
pkill -f "http.server"

# Detener Docker
docker compose down
```

---

**¿Todo claro?** 🎉

Si tienes algún problema, revisa la sección de Troubleshooting o avísame!
