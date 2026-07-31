# Meridian Signals — application-aware MCP server

A **client-owned** MCP tool server that ships with Meridian Commerce. It gives an
SRE agent the application context that generic infrastructure tools can't:
business/operations signals derived from the metrics these services actually
emit, plus a couple of direct reads of the app's own state.

It is deployed into the `meridian` namespace alongside the app (see
`../../k8s/mcp-signals.yaml`) and speaks MCP over SSE on port 3000 (`/sse`).

## Tools

| Tool | What it answers |
|---|---|
| `signals_health` | Is this server up and can it reach Prometheus? |
| `order_pipeline_health` | Is the gateway→checkout→payment pipeline healthy? Gives a cascade verdict (payment outage vs checkout bug vs pipeline-wide). |
| `checkout_success_rate` | What fraction of `/process` checkouts succeed (<500)? |
| `payment_provider_status` | Is the payment provider up? Charge error rate and failure reasons. |
| `inventory_levels` | Current stock per SKU; flags low/out-of-stock (reads inventory-service `/items`). |
| `service_error_breakdown` | Error rate per service grouped by `error_type` (timeout, upstream_error, payment_dependency_failure, …). |
| `latency_percentiles` | p50/p95/p99 latency for a service from the request-duration histogram. |
| `runtime_config` | A service's current `/admin/config` (chaos_mode, error_rate, provider_down) — tells a real incident from injected chaos. |

All signals come from real telemetry: `http_requests_total`,
`http_errors_total`, `http_request_duration_seconds`, `payment_provider_up`,
`payment_failures_total`, and inventory state.

## Run

Built and deployed automatically by `../../start.sh`. Standalone:

```bash
docker build -t meridian-signals:latest .
kubectl apply -f ../../k8s/mcp-signals.yaml     # into the meridian namespace
```

Local (outside k8s), pointing at a port-forwarded Prometheus:

```bash
pip install -r requirements.txt
PROMETHEUS_URL=http://localhost:9090 python server.py     # SSE on :3000/sse
```

## Register it with the SRE platform (bring-your-own MCP)

The platform merges extra MCP servers from its `MCP_SERVERS_JSON` config. Point it
at this server's in-cluster address:

```json
{
  "meridian-signals": {
    "url": "http://meridian-signals.meridian.svc.cluster.local:3000/sse",
    "transport": "sse"
  }
}
```

In Sentinel's `deploy/k8s/config.yaml` that becomes:

```yaml
MCP_SERVERS_JSON: '{"meridian-signals":{"url":"http://meridian-signals.meridian.svc.cluster.local:3000/sse","transport":"sse"}}'
```

The agent then has these app-aware tools next to the generic k8s / Prometheus /
Loki / GitHub tools — no platform code changes required.
