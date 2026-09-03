# Meridian Commerce

Meridian Commerce is a small e-commerce platform: an API gateway fronting
checkout, payment, and inventory services, driven by a continuous load
generator and instrumented with Prometheus, Loki, and Alertmanager. It runs on
Kubernetes in the `meridian` namespace.

This repository is the **client environment** — the application and its
infrastructure. It is deliberately independent of any monitoring or automation
platform: it emits standard telemetry (metrics, logs, alerts) and exposes a
Kubernetes API, and any SRE tooling observes it from the outside through those
interfaces. Sentinel connects to it exactly the way it would connect to any
real customer: through the cluster's Prometheus, Loki, GitHub, and Kubernetes
API — nothing here is aware of the platform.

## What runs here

- `services/` — the application: `api-gateway`, `checkout-service`, `payment-service`, and `inventory-service`.
- `k8s/` — Kubernetes manifests for the app and its monitoring stack (Prometheus, Loki, Promtail, Alertmanager, Grafana).
- `load-generator/` — continuously drives traffic through the gateway so the system has a live baseline.
- `testing/` — layer-based smoke tests for the environment.

Incidents are captured live by Prometheus/Alertmanager the same way they would
be in production — there's no separate chaos UI. Each service does expose a
small `/admin/config` diagnostic endpoint (the same one the `meridian-signals`
MCP tool reads) so a real incident can be told apart from an injected one, and
so any of the platform's recognized failure classes can be reproduced on
demand for training and runbook validation — see [FAULTS.md](FAULTS.md).

## Startup

```bash
./start.sh            # build images, apply manifests, wait for pods, print URLs
./start.sh --no-build # apply only (skip Docker builds)
./start.sh --down     # tear the whole namespace down
```

Requires a local Kubernetes cluster (OrbStack, Docker Desktop, or kind) and
Docker. Everything deploys into the `meridian` namespace.

## Service URLs

- API Gateway — http://localhost:8000
- Checkout Service — http://localhost:8001
- Inventory Service — http://localhost:8002
- Load Generator — http://localhost:8003

Observability: Prometheus 9090 · Grafana 3001 · Alertmanager 9093 · Loki 3100.

## How an SRE platform connects

An external platform reads this environment through:

- **Prometheus** — `http://prometheus.meridian.svc.cluster.local:9090` (golden-signal metrics)
- **Loki** — `http://loki.meridian.svc.cluster.local:3100` (logs)
- **Kubernetes API** — via a ServiceAccount with read (and, for remediation, scoped write) RBAC
- **Alertmanager** — posts firing/resolved alerts to the platform's webhook (bearer-authenticated with a per-cluster token; see `k8s/monitoring/alertmanager.yaml`)
- **GitHub** — this repo, for code context and revert PRs

## Related docs

- [services/README.md](services/README.md)
- [FAULTS.md](FAULTS.md) — fault injection playbook, one recipe per failure class
- [k8s/README.md](k8s/README.md)
- [load-generator/README.md](load-generator/README.md)
- [testing/README.md](testing/README.md)

<!-- sentinel e2e PR-write validation marker: test-18, safe to close -->
