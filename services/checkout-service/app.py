#!/usr/bin/env python3
"""
Checkout Service — processes orders and handles payments.

Intentionally flaky to generate realistic incidents:
  - 15% of requests fail with payment gateway errors
  - 20% of requests are slow (1.5-3s)
  - Memory grows every request (rate tunable via /admin/config leak_kb_per_request,
    for on-demand OOM demos)
  - DB connection errors spike when CHAOS_MODE=true
"""

import asyncio
import json
import logging
import os
import random
import time

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel


# ── Structured JSON logger ──────────────────────────────────────────────────
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "service": "checkout-service",
            "message": record.getMessage(),
            **({"exception": self.formatException(record.exc_info)} if record.exc_info else {}),
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("checkout")
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ── Prometheus metrics ───────────────────────────────────────────────────────
REQUEST_COUNT    = Counter("http_requests_total",             "Total requests",        ["service", "method", "endpoint", "status"])
REQUEST_LATENCY  = Histogram("http_request_duration_seconds", "Request latency",       ["service", "endpoint"],
                             buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0])
ERROR_COUNT      = Counter("http_errors_total",               "Total errors",          ["service", "endpoint", "error_type"])
PAYMENT_FAILURES = Counter("payment_failures_total",          "Payment failures",      ["reason"])
MEMORY_BYTES     = Gauge("process_memory_bytes_simulated",    "Simulated memory usage", ["service"])

# Simulate slow memory growth
_leak_store: list = []
_leak_bytes = {"total": 0}

# ── Mutable runtime config ──────────────────────────────────────────────────
config = {
    "chaos_mode": os.getenv("CHAOS_MODE", "false").lower() == "true",
    "error_rate": float(os.getenv("ERROR_RATE", "0.15")),
    "slow_rate":  float(os.getenv("SLOW_RATE",  "0.20")),
    "leak_kb_per_request": float(os.getenv("LEAK_KB_PER_REQUEST", "1")),
}

app = FastAPI(title="checkout-service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Admin config endpoints ───────────────────────────────────────────────────
class ConfigUpdate(BaseModel):
    error_rate: float | None = None
    slow_rate: float | None = None
    chaos_mode: bool | None = None
    leak_kb_per_request: float | None = None

@app.get("/admin/config")
def get_config():
    return config

@app.post("/admin/config")
def set_config(update: ConfigUpdate):
    if update.error_rate is not None:
        config["error_rate"] = max(0.0, min(1.0, update.error_rate))
    if update.slow_rate is not None:
        config["slow_rate"] = max(0.0, min(1.0, update.slow_rate))
    if update.chaos_mode is not None:
        config["chaos_mode"] = update.chaos_mode
    if update.leak_kb_per_request is not None:
        # Bounded at 4MB/request so a fat-fingered value can't instantly OOM
        # a pod on the very next call.
        config["leak_kb_per_request"] = max(0.0, min(4096.0, update.leak_kb_per_request))
    logger.info(f"Config updated: {config}")
    return config

# ── Service endpoints ────────────────────────────────────────────────────────
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/health")
def health():
    return {"status": "ok", "service": "checkout-service", "chaos_mode": config["chaos_mode"]}

@app.post("/process")
async def process_checkout(order_id: str = "unknown"):
    start = time.time()

    # Simulate memory leak — rate tunable via /admin/config leak_kb_per_request
    chunk = b"x" * int(config["leak_kb_per_request"] * 1024)
    _leak_store.append(chunk)
    _leak_bytes["total"] += len(chunk)
    MEMORY_BYTES.labels(service="checkout-service").set(_leak_bytes["total"])

    # Downstream dependency: call payment-service when PAYMENT_URL is configured.
    # A payment outage cascades into checkout 502s even though checkout is healthy
    # — this is the "downstream dependency failure" incident class.
    payment_url = os.getenv("PAYMENT_URL")
    if payment_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{payment_url}/charge", params={"order_id": order_id})
            if resp.status_code >= 500:
                ERROR_COUNT.labels(service="checkout-service", endpoint="/process", error_type="payment_dependency_failure").inc()
                REQUEST_COUNT.labels(service="checkout-service", method="POST", endpoint="/process", status="502").inc()
                logger.error(f"Downstream payment dependency failed order={order_id} status={resp.status_code}")
                raise HTTPException(status_code=502, detail="Downstream payment dependency failed")
        except httpx.RequestError as e:
            ERROR_COUNT.labels(service="checkout-service", endpoint="/process", error_type="payment_unreachable").inc()
            REQUEST_COUNT.labels(service="checkout-service", method="POST", endpoint="/process", status="502").inc()
            logger.error(f"Payment service unreachable order={order_id} error={e}")
            raise HTTPException(status_code=502, detail="Payment service unreachable")

    # Chaos mode: DB connection errors (higher failure rate)
    if config["chaos_mode"] and random.random() < 0.50:
        reason = random.choice(["db_connection_refused", "db_timeout", "db_pool_exhausted"])
        ERROR_COUNT.labels(service="checkout-service", endpoint="/process", error_type=reason).inc()
        PAYMENT_FAILURES.labels(reason=reason).inc()
        REQUEST_COUNT.labels(service="checkout-service", method="POST", endpoint="/process", status="500").inc()
        logger.error(f"Database error processing order={order_id} reason={reason} chaos=true")
        raise HTTPException(status_code=500, detail=f"Database error: {reason}")

    # Normal mode: payment gateway failures
    if random.random() < config["error_rate"]:
        reason = random.choice([
            "payment_gateway_timeout",
            "card_declined",
            "fraud_detected",
            "gateway_unavailable",
        ])
        ERROR_COUNT.labels(service="checkout-service", endpoint="/process", error_type=reason).inc()
        PAYMENT_FAILURES.labels(reason=reason).inc()
        REQUEST_COUNT.labels(service="checkout-service", method="POST", endpoint="/process", status="500").inc()
        logger.error(f"Payment failed order={order_id} reason={reason}")
        raise HTTPException(status_code=500, detail=f"Payment failed: {reason}")

    # Slow processing
    if random.random() < config["slow_rate"]:
        delay = random.uniform(1.5, 3.5)
        logger.warning(f"Slow payment processing order={order_id} delay_seconds={delay:.2f}")
        await asyncio.sleep(delay)

    duration = time.time() - start
    REQUEST_LATENCY.labels(service="checkout-service", endpoint="/process").observe(duration)
    REQUEST_COUNT.labels(service="checkout-service", method="POST", endpoint="/process", status="200").inc()
    logger.info(f"Order processed successfully order={order_id} duration_ms={round(duration * 1000)}")
    return {"order_id": order_id, "status": "processed", "duration_ms": round(duration * 1000)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
