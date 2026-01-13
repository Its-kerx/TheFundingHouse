# 🏦 The Funding House (TFH)

Plataforma de análisis de Funding Rates y arbitraje Delta Neutral en tiempo real para mercados de criptomonedas.

Este proyecto monitorea tasas de financiación (Funding Rates) en múltiples exchanges (Backpack, Hyperliquid, DexExtended, Pacifica), normaliza los datos y detecta oportunidades de arbitraje rentables entre posiciones Long y Short.

## 🚀 Arquitectura

El sistema utiliza una arquitectura de microservicios contenerizada con Docker:

* **Frontend**: HTML5/JS Vainilla servido a través de **Nginx** (Proxy Inverso).
* **API Gateway**: **FastAPI** que centraliza el enrutamiento de peticiones.
* **Data Ingestion Service**: Recolecta y normaliza datos de exchanges externos.
* **Funding Analytics Service**: Motor de cálculo de APR y Spreads de arbitraje.
* **Funding Scheduler**: Orquestador que sincroniza la ingesta y el análisis cada 60 segundos.
* **MongoDB**: Persistencia de series temporales y snapshots.

## 🛠️ Tecnologías

* **Lenguajes:** Python 3.12, JavaScript (ES6+).
* **Frameworks:** FastAPI, Motor (Async Mongo Driver).
* **Infraestructura:** Docker, Docker Compose, Nginx.
* **Base de Datos:** MongoDB 6.

## 📋 Prerrequisitos

* [Docker](https://www.docker.com/) instalado.
* [Docker Compose](https://docs.docker.com/compose/) instalado.

## ⚡ Instalación y Despliegue Rápido

1.  **Clonar el repositorio:**
    ```bash
    git clone <tu-repo-url>
    cd TheFundingHouse
    ```

2.  **Levantar los servicios:**
    El proyecto incluye una configuración lista para usar.
    ```bash
    docker-compose up --build -d
    ```

3.  **Verificar estado:**
    ```bash
    docker-compose ps
    ```

## 🔌 Servicios y Puertos

Una vez desplegado, puedes acceder a los siguientes endpoints:

| Servicio | URL Local | Descripción |
| :--- | :--- | :--- |
| **Frontend** | [http://localhost:8080](http://localhost:8080) | Dashboard principal (Usuario). |
| **API Gateway** | [http://localhost:3000/docs](http://localhost:3000/docs) | Punto de entrada unificado (Swagger). |
| **Data Ingestion** | [http://localhost:8001/docs](http://localhost:8001/docs) | API de recolección de datos. |
| **Funding Analytics**| [http://localhost:8002/docs](http://localhost:8002/docs) | API de análisis y cálculo. |
| **MongoDB** | `localhost:27017` | Base de datos (usuario: root). |

## 🔄 Flujo de Datos

1.  **Scheduler** activa la actualización de mercados cada 60 segundos.
2.  **Data Ingestion** descarga y normaliza los funding rates de los exchanges soportados y los guarda en MongoDB (`funding_timeseries` y colecciones `current`).
3.  **Funding Analytics** procesa los datos crudos, calcula el APR anualizado y genera pares de arbitraje (Long vs Short) guardando un snapshot en `funding_arbitrage_pairs_ts`.
4.  **Frontend** consulta al API Gateway, el cual redirige la petición al microservicio de Analytics para mostrar los datos en el dashboard.

## 📁 Estructura del Proyecto

```text
├── api-gateway/            # Enrutador principal (FastAPI)
├── frontend/               # Código cliente (HTML/JS/CSS) y configuración Nginx
├── microservices/
│   ├── data-ingestion/     # Conectores con Exchanges (Backpack, Hyperliquid, etc.)
│   ├── funding-analytics/  # Lógica de negocio y cálculo financiero
│   └── funding-scheduler/  # Cron jobs y disparadores
└── docker-compose.yml      # Orquestación de contenedores
