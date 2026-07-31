# RB-INV-01 — Slow inventory queries

**Service:** inventory-service · **Severity:** SEV-3 (degraded browse/lookup) · **Owner:** Catalog on-call

## Trigger
inventory-service p95 latency on `/items` or `/items/{id}` exceeds SLO, or the
gateway reports `timeout` errors on `/inventory`.

## First signals to check
1. `latency_percentiles(service="inventory-service", window="5m")` — p95/p99 on
   the read paths.
2. `service_error_breakdown(service="inventory-service")` — `not_found` (benign,
   bad SKU) vs `timeout`/`connection_error` (real).
3. `runtime_config(service="inventory-service")` if present — any slow-query
   flag / chaos setting enabled.
4. Logs: look for `simulate_db_query` slow entries and reindex operations.

## Diagnosis
- Latency spikes correlate with a `reindex` or a slow-query flag → transient or
  config-driven; clears on its own or by resetting config.
- Sustained high latency with rising CPU/mem → resource pressure.

## Remediation (reversible, guardrailed)
- **Config/experiment:** reset the slow-query flag via `/admin/config`.
- **Resource pressure:** `patch_resource_limits` to raise CPU/mem, or `scale`
  inventory-service up. `restart` if a pod is wedged after a reindex.

## Verify
p95 back under SLO on `/items` for 5+ minutes; gateway `/inventory` timeouts gone.

## Escalate
If the datastore itself is slow (not just this service), page the data owner.
