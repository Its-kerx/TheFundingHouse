FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python - <<'PY'
import requests, pymongo
PY

COPY . .

CMD ["python", "-m", "funding_scheduler"]
