FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias del sistema si hicieran falta (ejemplo: build-essential)
# RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# PORT se pasa por env; por defecto usamos 8001
ENV PORT=8001

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
