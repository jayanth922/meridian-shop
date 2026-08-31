# Fault Injection Playbook

Meridian Commerce's normal baseline is healthy (`ERROR_RATE`/`SLOW_RATE`/etc.
all default to `0` in [k8s/services.yaml](k8s/services.yaml)). Every service
also exposes a small `/admin/config` diagnostic endpoint — the same one the
`meridian-signals` MCP tool's `runtime_config` reads — so a real incident can
be told apart from an injected one, and so incidents of a given class can be
reproduced on demand for training, testing, and runbook validation.

This maps each of the platform's recognized failure classes
(`sre_agent/skill_store.py::_FAILURE_CLASS_KEYWORDS`) to a concrete way to
trigger it here, and how to revert it. Ports below assume `./start.sh` is
running and the services are reachable at `localhost` (see the root
[README.md](README.md#service-urls)).

Always revert a fault once you're done — a fault left engaged will keep
generating alerts.

## high_error_rate

Elevated 5xx from checkout or payment.

```bash
curl -X POST localhost:8001/admin/config -H 'Content-Type: application/json' -d '{"error_rate": 0.6}'   # checkout
curl -X POST localhost:8004/admin/config -H 'Content-Type: application/json' -d '{"error_rate": 0.6}'   # payment
```

Revert: same call with `"error_rate": 0`.

## latency

Elevated p95/p99 on checkout, payment, or inventory.

```bash
curl -X POST localhost:8001/admin/config -H 'Content-Type: application/json' -d '{"slow_rate": 0.6}'        # checkout
curl -X POST localhost:8004/admin/config -H 'Content-Type: application/json' -d '{"slow_rate": 0.6}'        # payment
curl -X POST localhost:8002/admin/config -H 'Content-Type: application/json' -d '{"slow_query_rate": 0.6}'  # inventory
```

Revert: same call with the rate back to `0`.

## dependency

Payment-provider outage cascading into checkout 502s, even though checkout
itself is healthy.

```bash
curl -X POST localhost:8004/admin/config -H 'Content-Type: application/json' -d '{"provider_down": true}'
```

Revert: `{"provider_down": false}`. Watch `payment_provider_up` drop to `0`
and checkout's `http_errors_total{error_type="payment_dependency_failure"}`
climb while payment's own health check keeps passing (it doesn't — payment
correctly reports itself degraded; the point is checkout's *own* logic is
fine, the failure is downstream).

## oom

Checkout's per-request memory leak is real but slow by default (~1KB/request
— hours to OOM at baseline traffic). Speed it up for a demo:

```bash
curl -X POST localhost:8001/admin/config -H 'Content-Type: application/json' -d '{"leak_kb_per_request": 512}'
```

With the load-generator's default traffic (~5 rps), this reaches the pod's
256Mi limit and gets OOMKilled within a couple of minutes. `CheckoutMemoryApproachingLimit`
fires once `process_memory_bytes_simulated{service="checkout-service"}` crosses
200MB, then `kubectl get pods -n meridian -l app=checkout-service` shows a
restart with `Last State: OOMKilled`.

Revert: `{"leak_kb_per_request": 1}` (a fresh pod after the restart also
starts its leak store empty).

## saturation

Sustained CPU pressure on inventory.

```bash
curl -X POST "localhost:8002/reindex?iterations=15000000"
# or repeatedly, or via the load-generator's burst mode:
curl -X POST localhost:8003/admin/trigger-burst
```

Watch CPU usage approach the pod's `500m` limit
(`kubectl top pod -n meridian -l app=inventory-service`). No revert needed —
`/reindex` is a one-shot call; stop calling it and CPU settles back down.

No Alertmanager rule covers this — the stack doesn't scrape cAdvisor/kube-state-metrics,
so container-level CPU isn't in Prometheus. Detection here is `kubectl top` /
Kubernetes-API-observed, the same as `crashloop`, `imagepull`, and `bad_deploy` below.

## crashloop

Genuine `CrashLoopBackOff`, not a simulated status. inventory-service reads
`CRASH_ON_STARTUP` once at process start (not live-settable via
`/admin/config` — it has to come back through an actual rollout, like a real
bad config value would):

```bash
kubectl patch configmap meridian-config -n meridian --type merge \
  -p '{"data":{"inventory_crash_on_startup":"true"}}'
kubectl rollout restart deployment/inventory-service -n meridian
kubectl get pods -n meridian -l app=inventory-service -w
```

Each new pod boots, serves for ~3s, then exits(1) and gets restarted by
Kubernetes — a real crash loop with real backoff, not a fake status. No
Alertmanager rule fires for this (see the `saturation` note above); detect it
via `kubectl get pods -n meridian -l app=inventory-service` (`STATUS
CrashLoopBackOff`, rising `RESTARTS`) or `kubectl describe pod ... | grep -A3
"Last State"`.

Revert:

```bash
kubectl patch configmap meridian-config -n meridian --type merge \
  -p '{"data":{"inventory_crash_on_startup":"false"}}'
kubectl rollout restart deployment/inventory-service -n meridian
```

## imagepull

```bash
kubectl set image deployment/inventory-service inventory-service=meridian-inventory-service:doesnotexist -n meridian
kubectl get pods -n meridian -l app=inventory-service -w   # -> ErrImagePull / ImagePullBackOff
```

No Alertmanager rule fires for this either — same reasoning as `saturation`.

Revert:

```bash
kubectl set image deployment/inventory-service inventory-service=meridian-inventory-service:latest -n meridian
```

## bad_deploy

`bad_deploy` isn't a distinct symptom in this app — it's any of the above
(most often crashloop or high_error_rate) whose onset lines up with a
rollout. The crashloop recipe above is also the cleanest `bad_deploy`
reproduction: the ConfigMap edit + `rollout restart` stands in for "a bad
change shipped," and `kubectl rollout history deployment/inventory-service -n
meridian` is what confirms the correlation during diagnosis.

To reproduce a non-crashing regression instead (e.g. a deploy that just makes
things slower), combine a `/admin/config` fault above with a rollout restart
so the change lines up with an actual revision, rather than a live in-place
toggle:

```bash
curl -X POST localhost:8001/admin/config -H 'Content-Type: application/json' -d '{"slow_rate": 0.6}'
kubectl rollout restart deployment/checkout-service -n meridian
```
