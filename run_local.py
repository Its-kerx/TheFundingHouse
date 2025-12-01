#!/usr/bin/env python3
"""
Launcher sencillo para entorno local:

 1) Levanta Mongo y Postgres con Docker
 2) Arranca data-ingestion-service (puerto 8001) usando su venv
 3) Arranca funding-analytics-service (puerto 8002) usando su venv
 4) Arranca api-gateway (puerto 3000) usando su venv
 5) Sirve el frontend estático en el puerto 8080

Supone que:
 - Docker está instalado y en el PATH
 - Cada servicio tiene su venv con dependencias ya instaladas
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def compose_cmd() -> list[str]:
    """Devuelve el comando de docker compose disponible."""
    if shutil.which("docker"):
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    print("No se encontró Docker/Compose en el PATH.", file=sys.stderr)
    sys.exit(1)


def python_in_venv(service_dir: Path) -> str:
    """Devuelve el ejecutable de Python dentro del venv del servicio."""
    if sys.platform.startswith("win"):
        return str(service_dir / "venv" / "Scripts" / "python.exe")
    else:
        return str(service_dir / "venv" / "bin" / "python")


def run_checked(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"→ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def start_process(cmd: list[str], cwd: Path) -> subprocess.Popen:
    print(f"→ iniciando {' '.join(cmd)} (cwd={cwd})")
    return subprocess.Popen(cmd, cwd=cwd)


def main() -> None:
    compose = compose_cmd()

    # 1) Bases de datos
    print("1) Levantando bases de datos (Mongo + Postgres) con Docker...")
    run_checked([*compose, "up", "-d", "mongo", "postgres"], cwd=ROOT)

    procs: list[subprocess.Popen] = []

    # 2) Data Ingestion
    print("2) Iniciando Data Ingestion Service (puerto 8001)...")
    data_dir = ROOT / "microservices" / "data-ingestion-service"
    data_python = python_in_venv(data_dir)
    procs.append(start_process([data_python, "main.py"], cwd=data_dir))

    # 3) Funding Analytics
    print("3) Iniciando Funding Analytics Service (puerto 8002)...")
    analytics_dir = ROOT / "microservices" / "funding-analytics-service"
    analytics_python = python_in_venv(analytics_dir)
    procs.append(start_process([analytics_python, "main.py"], cwd=analytics_dir))

    # 4) API Gateway
    print("4) Iniciando API Gateway (puerto 3000)...")
    api_dir = ROOT / "api-gateway"
    api_python = python_in_venv(api_dir)
    procs.append(start_process([api_python, "main.py"], cwd=api_dir))

    # 5) Frontend estático
    print("5) Iniciando frontend (puerto 8080)...")
    frontend_dir = ROOT / "frontend"
    procs.append(
        start_process(
            [sys.executable, "-m", "http.server", "8080"],
            cwd=frontend_dir,
        )
    )

    print(
        "\nTodo iniciado. URLs:\n"
        "- Frontend:              http://localhost:8080\n"
        "- API Gateway:           http://localhost:3000\n"
        "- Gateway health:        http://localhost:3000/health\n"
        "- Data ingestion:        http://localhost:8001/status\n"
        "- Funding analytics:     http://localhost:8002/docs\n"
        "Deja esta ventana abierta; Ctrl+C para parar todo."
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo servicios...")
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            run_checked([*compose, "down"], cwd=ROOT)
        except Exception:
            pass
        print("Listo. Bases de datos detenidas.")


if __name__ == "__main__":
    main()
