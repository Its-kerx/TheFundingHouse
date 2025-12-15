import os
import time
import threading
from datetime import datetime, timedelta

import requests


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_float(primary: str, default_val: str, legacy: str | None = None) -> float:
    val = os.getenv(primary)
    if val is None and legacy:
        val = os.getenv(legacy)
    return float(val or default_val)


BACKPACK_DEFAULT = "http://data_ingestion:8001"
DATA_INGESTION_BASE_URL = _env("DATA_INGESTION_BASE_URL", BACKPACK_DEFAULT)
BACKPACK_BASE_URL = _env("BACKPACK_BASE_URL", DATA_INGESTION_BASE_URL)
HYPERLIQUID_BASE_URL = _env("HYPERLIQUID_BASE_URL", "http://localhost:8001")
FUNDING_ANALYTICS_BASE_URL = _env("FUNDING_ANALYTICS_BASE_URL", "http://localhost:8002")

# New envs with legacy fallback
MARKETS_EVERY_SECONDS = _env_float("MARKETS_EVERY_SECONDS", "60", legacy="MARKETS_REFRESH_SECONDS")
SNAPSHOTS_EVERY_SECONDS = _env_float("SNAPSHOTS_EVERY_SECONDS", "60", legacy="SNAPSHOT_REFRESH_SECONDS")

# Bootstrap config
BOOTSTRAP_MIN_POINTS = int(os.getenv("BOOTSTRAP_MIN_POINTS", "100"))
BOOTSTRAP_MIN_COVERAGE_HOURS = float(os.getenv("BOOTSTRAP_MIN_COVERAGE_HOURS", "168"))


def log(msg: str) -> None:
    ts = datetime.utcnow().isoformat()
    print(f"[{ts}] {msg}", flush=True)


def _safe_post(url: str) -> None:
    try:
        resp = requests.post(url, timeout=10)
        log(f"POST {url} -> {resp.status_code}")
    except requests.RequestException as exc:
        log(f"ERROR calling {url}: {exc}")


def _wait_for_health(urls: list[str], max_wait: float = 60.0) -> bool:
    start = time.monotonic()
    delay = 1.0
    last_error = ""
    while time.monotonic() - start < max_wait:
        for url in urls:
            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    log(f"[health] ready: {url}")
                    return True
                last_error = f"status {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)
    log(f"[health] not ready after {max_wait}s urls={urls} last_error={last_error}")
    return False


def _wait_for_analytics_ready(max_wait: float = 60.0) -> dict:
    start = time.monotonic()
    delay = 1.0
    last_error = ""
    while time.monotonic() - start < max_wait:
        try:
            resp = requests.get(
                f"{FUNDING_ANALYTICS_BASE_URL}/funding/snapshots/health",
                params={"days": 30},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json() if resp.content else {}
                log(f"[health] analytics ready: {data}")
                return data
            last_error = f"status {resp.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)
    log(f"[health] analytics not ready after {max_wait}s ({last_error})")
    return {}


_bootstrap_ran = False


def _maybe_bootstrap():
    global _bootstrap_ran
    if _bootstrap_ran:
        return
    health = _wait_for_analytics_ready()
    coverage_hours = float(health.get("coverage_hours") or 0.0)
    distinct_minutes = int(health.get("distinct_minutes_last_24h") or 0)
    newest_ts = health.get("newest_ts")
    oldest_ts = health.get("oldest_ts")
    need = (distinct_minutes < BOOTSTRAP_MIN_POINTS) or (coverage_hours > BOOTSTRAP_MIN_COVERAGE_HOURS)
    log(
        f"Bootstrap check: need={'yes' if need else 'no'}, "
        f"coverage_hours={coverage_hours:.1f}, distinct_minutes_last_24h={distinct_minutes}, "
        f"newest_ts={newest_ts}, oldest_ts={oldest_ts}"
    )
    if not need:
        _bootstrap_ran = True
        return
    _bootstrap_ran = True
    try:
        resp = requests.post(
            f"{FUNDING_ANALYTICS_BASE_URL}/funding/snapshots/bootstrap",
            params={"days": 30, "mode": "bootstrap"},
            timeout=10,
        )
        summary = {}
        try:
            summary = resp.json()
        except Exception:
            summary = {"status": resp.status_code, "text": resp.text[:200]}
        log(f"[bootstrap] status={resp.status_code} summary={summary}")
    except Exception as exc:
        log(f"[bootstrap] error: {exc}")


def _run_loop() -> None:
    next_markets = time.monotonic()
    next_snapshot = time.monotonic()

    # Bootstrap history in background at most once
    threading.Thread(target=_maybe_bootstrap, daemon=True).start()

    while True:
        now = time.monotonic()

        if now >= next_markets:
            bp_url = f"{DATA_INGESTION_BASE_URL}/backpack/refresh"
            hl_url = f"{HYPERLIQUID_BASE_URL}/hyperliquid/refresh"
            log(f"Refreshing markets | backpack_url={bp_url}, hyperliquid_url={hl_url}")
            _safe_post(bp_url)
            _safe_post(hl_url)
            next_markets = now + MARKETS_EVERY_SECONDS

        if now >= next_snapshot:
            log("Refreshing funding arbitrage snapshot")
            _safe_post(f"{FUNDING_ANALYTICS_BASE_URL}/funding/arbitrage/refresh")
            next_snapshot = now + SNAPSHOTS_EVERY_SECONDS

        time.sleep(1)


def main() -> None:
    log(
        "Starting funding scheduler "
        f"(markets every {MARKETS_EVERY_SECONDS}s, snapshots every {SNAPSHOTS_EVERY_SECONDS}s)"
    )
    # Wait for dependencies
    _wait_for_health([f"{FUNDING_ANALYTICS_BASE_URL}/status", f"{FUNDING_ANALYTICS_BASE_URL}/health"])
    _wait_for_health([f"{DATA_INGESTION_BASE_URL}/health", f"{DATA_INGESTION_BASE_URL}/status"])
    _wait_for_health([f"{HYPERLIQUID_BASE_URL}/health", f"{HYPERLIQUID_BASE_URL}/status"])
    _run_loop()


if __name__ == "__main__":
    main()
