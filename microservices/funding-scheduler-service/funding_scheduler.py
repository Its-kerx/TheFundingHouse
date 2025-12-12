import os
import time
from datetime import datetime

import requests


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


BACKPACK_DEFAULT = "http://data_ingestion:8001"
BACKPACK_BASE_URL = _env("BACKPACK_BASE_URL", BACKPACK_DEFAULT)
HYPERLIQUID_BASE_URL = _env("HYPERLIQUID_BASE_URL", "http://localhost:8001")
FUNDING_ANALYTICS_BASE_URL = _env("FUNDING_ANALYTICS_BASE_URL", "http://localhost:8002")

MARKETS_REFRESH_SECONDS = float(_env("MARKETS_REFRESH_SECONDS", "60"))
SNAPSHOT_REFRESH_SECONDS = float(_env("SNAPSHOT_REFRESH_SECONDS", "3600"))


def log(msg: str) -> None:
    ts = datetime.utcnow().isoformat()
    print(f"[{ts}] {msg}", flush=True)


def _safe_post(url: str) -> None:
    try:
        resp = requests.post(url, timeout=10)
        log(f"POST {url} -> {resp.status_code}")
    except requests.RequestException as exc:
        log(f"ERROR calling {url}: {exc}")


def _run_loop() -> None:
    next_markets = time.monotonic()
    next_snapshot = time.monotonic()

    while True:
        now = time.monotonic()

        if now >= next_markets:
            bp_url = f"{BACKPACK_BASE_URL}/backpack/refresh"
            log(f"Refreshing markets (Backpack + Hyperliquid) | backpack_url={bp_url}")
            _safe_post(bp_url)
            _safe_post(f"{HYPERLIQUID_BASE_URL}/hyperliquid/refresh")
            next_markets = now + MARKETS_REFRESH_SECONDS

        if now >= next_snapshot:
            log("Refreshing funding arbitrage snapshot")
            _safe_post(f"{FUNDING_ANALYTICS_BASE_URL}/funding/arbitrage/refresh")
            next_snapshot = now + SNAPSHOT_REFRESH_SECONDS

        time.sleep(1)


def main() -> None:
    log(
        "Starting funding scheduler "
        f"(markets every {MARKETS_REFRESH_SECONDS}s, snapshots every {SNAPSHOT_REFRESH_SECONDS}s)"
    )
    _run_loop()


if __name__ == "__main__":
    main()
