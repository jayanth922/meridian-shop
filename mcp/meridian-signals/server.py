#!/usr/bin/env python3
"""
Meridian Signals — application-aware MCP tool server.

This is a CLIENT-owned MCP server. It ships with the Meridian Commerce app (not
with any monitoring platform) and exposes business/operations signals an on-call
engineer for THIS shop actually reasons about: order-pipeline health, checkout
success rate, payment-provider status, inventory levels, per-service error
breakdowns and latency percentiles, and the app's current runtime config.

Every signal is computed from telemetry the services genuinely emit
(http_requests_total, http_errors_total, http_request_duration_seconds,
payment_provider_up, payment_failures_total) via the cluster's Prometheus, plus
a couple of direct reads of the app's own state endpoints. An SRE agent connects
to this over MCP (SSE at /sse) alongside the generic k8s/Prometheus/Loki tools to
get context that only the application owner can provide.

Config (env):
  PROMETHEUS_URL   client's Prometheus base URL (e.g. http://prometheus.meridian.svc.cluster.local:9090)
  GATEWAY_URL      default http://api-gateway:8000
  CHECKOUT_URL     default http://checkout-service:8001
  PAYMENT_URL      default http://payment-service:8004   (payment listens on 8004 in-cluster)
  INVENTORY_URL    default http://inventory-service:8002
  NAMESPACE        default "meridian" (informational)
  HOST / HTTP_PORT bind address / port (default 0.0.0.0:3000)
"""

import json
import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("meridian-signals")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
NAMESPACE = os.getenv("NAMESPACE", "meridian")
SERVICE_URLS = {
    "api-gateway": os.getenv("GATEWAY_URL", "http://api-gateway:8000"),
    "checkout-service": os.getenv("CHECKOUT_URL", "http://checkout-service:8001"),
    "payment-service": os.getenv("PAYMENT_URL", "http://payment-service:8004"),
    "inventory-service": os.getenv("INVENTORY_URL", "http://inventory-service:8002"),
}

port = int(os.getenv("HTTP_PORT", "3000"))
host = os.getenv("HOST", "0.0.0.0")
mcp = FastMCP("meridian-signals", host=host, port=port)


# ── Prometheus helpers ───────────────────────────────────────────────────────
async def _prom_query(query: str) -> list | None:
    """Run an instant PromQL query. Returns the raw result vector or None on error."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "success":
            logger.warning("Prometheus query non-success: %s", body)
            return None
        return body["data"]["result"]
    except Exception as e:
        logger.error("Prometheus query failed (%s): %s", query, e)
        return None


async def _scalar(query: str) -> float | None:
    """First sample value of an instant query as a float, or None."""
    result = await _prom_query(query)
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


async def _by_label(query: str, label: str) -> dict[str, float]:
    """Instant query grouped by a label → {label_value: float}."""
    result = await _prom_query(query)
    out: dict[str, float] = {}
    if not result:
        return out
    for row in result:
        key = row.get("metric", {}).get(label, "unknown")
        try:
            out[key] = float(row["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return out


def _round(v: float | None, dp: int = 4) -> float | None:
    return None if v is None else round(v, dp)


async def _service_get(service: str, path: str) -> Any | None:
    base = SERVICE_URLS.get(service)
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base.rstrip('/')}{path}")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("Service GET %s%s failed: %s", service, path, e)
        return None


# ── Tools ────────────────────────────────────────────────────────────────────
@mcp.tool()
async def signals_health() -> str:
    """Health of this signals server and its Prometheus connection. Call first if
    other tools return empty data."""
    up = await _scalar("vector(1)")
    return json.dumps({
        "server": "meridian-signals",
        "namespace": NAMESPACE,
        "prometheus_url": PROMETHEUS_URL,
        "prometheus_reachable": up is not None,
        "service_urls": SERVICE_URLS,
    }, indent=2)


@mcp.tool()
async def order_pipeline_health(window: str = "5m") -> str:
    """End-to-end health of the order pipeline (gateway → checkout → payment).

    Returns request rate, error rate and a cascade verdict so the agent can tell
    an app-level outage from a single-service blip. This is the signal to check
    first for any checkout/payment incident.

    Args:
        window: PromQL rate window (e.g. "1m", "5m", "15m").
    """
    w = window
    gw_total = await _scalar(f'sum(rate(http_requests_total{{service="api-gateway",endpoint="/checkout"}}[{w}]))')
    gw_5xx = await _scalar(f'sum(rate(http_requests_total{{service="api-gateway",endpoint="/checkout",status=~"5.."}}[{w}]))')
    co_total = await _scalar(f'sum(rate(http_requests_total{{service="checkout-service",endpoint="/process"}}[{w}]))')
    co_5xx = await _scalar(f'sum(rate(http_requests_total{{service="checkout-service",endpoint="/process",status=~"5.."}}[{w}]))')
    pay_up = await _scalar('max(payment_provider_up)')

    def _err(part: float | None, total: float | None) -> float | None:
        if part is None or total is None or total == 0:
            return None
        return part / total

    gw_err = _err(gw_5xx, gw_total)
    co_err = _err(co_5xx, co_total)

    # Cascade verdict
    verdict = "healthy"
    reason = "error rates within normal range"
    if pay_up == 0:
        verdict, reason = "payment_outage", "payment_provider_up=0 — provider is down; checkout 502s are a cascade, not a checkout bug"
    elif (co_err or 0) >= 0.1 and (gw_err or 0) >= 0.1:
        verdict, reason = "pipeline_degraded", "elevated errors at both gateway and checkout — customer-facing order failures"
    elif (co_err or 0) >= 0.1:
        verdict, reason = "checkout_degraded", "checkout error rate elevated; gateway still absorbing some traffic"

    return json.dumps({
        "window": w,
        "gateway_checkout": {"rps": _round(gw_total), "error_rate": _round(gw_err)},
        "checkout_process": {"rps": _round(co_total), "error_rate": _round(co_err)},
        "payment_provider_up": None if pay_up is None else bool(pay_up),
        "verdict": verdict,
        "reason": reason,
    }, indent=2)


@mcp.tool()
async def checkout_success_rate(window: str = "5m") -> str:
    """Checkout success rate: fraction of /process requests returning < 500.

    Args:
        window: PromQL rate window.
    """
    total = await _scalar(f'sum(rate(http_requests_total{{service="checkout-service",endpoint="/process"}}[{window}]))')
    err = await _scalar(f'sum(rate(http_requests_total{{service="checkout-service",endpoint="/process",status=~"5.."}}[{window}]))')
    success = None if (total in (None, 0) or err is None) else (total - err) / total
    return json.dumps({
        "window": window,
        "requests_per_sec": _round(total),
        "error_per_sec": _round(err),
        "success_rate": _round(success),
        "status": "ok" if (success or 1) >= 0.95 else "degraded",
    }, indent=2)


@mcp.tool()
async def payment_provider_status(window: str = "5m") -> str:
    """Payment provider health: up/down gauge, charge error rate, and failure
    reasons (from payment_failures_total).

    Args:
        window: PromQL rate window.
    """
    up = await _scalar('max(payment_provider_up)')
    total = await _scalar(f'sum(rate(http_requests_total{{service="payment-service"}}[{window}]))')
    err = await _scalar(f'sum(rate(http_requests_total{{service="payment-service",status=~"5.."}}[{window}]))')
    err_rate = None if (total in (None, 0) or err is None) else err / total
    reasons = await _by_label(f'sum by (reason) (rate(payment_failures_total[{window}]))', "reason")
    return json.dumps({
        "window": window,
        "provider_up": None if up is None else bool(up),
        "charge_error_rate": _round(err_rate),
        "failures_per_sec_by_reason": {k: _round(v) for k, v in reasons.items()},
    }, indent=2)


@mcp.tool()
async def inventory_levels(low_stock_threshold: int = 10) -> str:
    """Current stock levels, read from inventory-service's own state (/items).
    Flags SKUs at or below the low-stock threshold and any that are out of stock.

    Args:
        low_stock_threshold: quantity at/below which a SKU is flagged low.
    """
    items = await _service_get("inventory-service", "/items")
    if items is None:
        return json.dumps({"error": "inventory-service /items unreachable", "url": SERVICE_URLS.get("inventory-service")})

    # inventory-service returns {"items": {"item-001": {"name":..,"stock":142,"price":..}, ...}}.
    # Also tolerate a list shape or a flat {sku: qty} mapping.
    rows: list[dict[str, Any]] = []
    raw = items.get("items", items) if isinstance(items, dict) else items

    def _qty(v: Any) -> Any:
        if isinstance(v, dict):
            return v.get("stock", v.get("quantity", v.get("count")))
        return v

    def _name(v: Any) -> Any:
        return v.get("name") if isinstance(v, dict) else None

    if isinstance(raw, dict):
        for sku, v in raw.items():
            rows.append({"sku": sku, "name": _name(v), "quantity": _qty(v)})
    elif isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                sku = it.get("id") or it.get("sku") or it.get("name") or "unknown"
                rows.append({"sku": sku, "name": it.get("name"), "quantity": _qty(it)})

    low, out = [], []
    for r in rows:
        q = r.get("quantity")
        if isinstance(q, (int, float)):
            if q <= 0:
                out.append(r["sku"])
            elif q <= low_stock_threshold:
                low.append(r["sku"])

    return json.dumps({
        "sku_count": len(rows),
        "out_of_stock": out,
        "low_stock": low,
        "low_stock_threshold": low_stock_threshold,
        "items": rows,
    }, indent=2)


@mcp.tool()
async def service_error_breakdown(service: str, window: str = "5m") -> str:
    """Error rate for a service grouped by error_type (from http_errors_total) —
    e.g. timeout vs upstream_error vs payment_dependency_failure.

    Args:
        service: one of api-gateway, checkout-service, payment-service, inventory-service.
        window: PromQL rate window.
    """
    by_type = await _by_label(
        f'sum by (error_type) (rate(http_errors_total{{service="{service}"}}[{window}]))', "error_type"
    )
    return json.dumps({
        "service": service,
        "window": window,
        "errors_per_sec_by_type": {k: _round(v) for k, v in by_type.items()},
        "total_errors_per_sec": _round(sum(by_type.values()) if by_type else 0.0),
    }, indent=2)


@mcp.tool()
async def latency_percentiles(service: str, window: str = "5m") -> str:
    """p50/p95/p99 request latency for a service (seconds), from the
    http_request_duration_seconds histogram.

    Args:
        service: service name label.
        window: PromQL rate window.
    """
    async def q(p: float) -> float | None:
        return await _scalar(
            f'histogram_quantile({p}, sum by (le) (rate(http_request_duration_seconds_bucket{{service="{service}"}}[{window}])))'
        )
    return json.dumps({
        "service": service,
        "window": window,
        "p50_seconds": _round(await q(0.50), 3),
        "p95_seconds": _round(await q(0.95), 3),
        "p99_seconds": _round(await q(0.99), 3),
    }, indent=2)


@mcp.tool()
async def runtime_config(service: str) -> str:
    """The service's current runtime config (chaos_mode, error_rate, slow_rate,
    leak_kb_per_request, provider_down, slow_query_rate, crash_on_startup) from
    its /admin/config endpoint. Use this to tell a real incident from injected
    chaos or a bad config value.

    Args:
        service: checkout-service, payment-service, or inventory-service (the
            ones with /admin/config).
    """
    cfg = await _service_get(service, "/admin/config")
    if cfg is None:
        return json.dumps({"error": f"{service} /admin/config unreachable", "url": SERVICE_URLS.get(service)})
    return json.dumps({"service": service, "config": cfg}, indent=2)


if __name__ == "__main__":
    logger.info("Starting meridian-signals MCP on %s:%s (SSE at /sse), Prometheus=%s", host, port, PROMETHEUS_URL)
    mcp.run(transport="sse")
