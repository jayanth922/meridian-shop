# RB-GW-01 — API gateway high latency

**Service:** api-gateway · **Severity:** SEV-2 · **Owner:** Platform/Edge on-call

## Trigger
api-gateway p95 latency for `/checkout` or `/inventory` exceeds SLO (e.g. > 1s
p95 over 5 minutes), or gateway timeouts (`error_type="timeout"`) rise.

## First signals to check
1. `latency_percentiles(service="api-gateway", window="5m")` — confirm p95/p99.
2. `service_error_breakdown(service="api-gateway")` — `timeout` vs
   `upstream_error` vs `connection_error`.
3. `latency_percentiles(service="checkout-service")` and
   `latency_percentiles(service="inventory-service")` — is the latency actually
   downstream? The gateway has a 5s client timeout to both.
4. `order_pipeline_health` — is this isolated to gateway or pipeline-wide?

## Diagnosis
- Gateway latency tracks a downstream service's latency → the downstream is the
  root cause (go to RB-INV-01 for inventory, RB-CHK-01/RB-PAY-01 for checkout).
- Gateway slow with healthy downstreams → gateway resource pressure (CPU/mem) or
  connection-pool exhaustion; check `http_active_requests` and pod resources.

## Remediation (reversible, guardrailed)
- Downstream root cause → remediate there; do not touch the gateway.
- Gateway saturation → `scale` api-gateway up (keep ≥1), or
  `patch_resource_limits` to raise CPU if throttled. `restart` if a pod is wedged.

## Verify
Gateway p95 back under SLO and `timeout` errors at baseline for 5+ minutes.

## Escalate
If downstream, hand off to that service's owner. If gateway capacity, review HPA
settings (`k8s/hpa.yaml`).
