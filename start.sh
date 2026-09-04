#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════
#  Meridian Commerce — One-Click Startup Script (OrbStack / Docker Desktop)
#
#  Usage (from Git Bash):
#    ./start.sh              Build images, inject to K8s, and deploy
#    ./start.sh --no-build   Deploy only (skip Docker builds & injection)
#    ./start.sh --down       Tear down the entire system
# ════════════════════════════════════════════════════════════════════════════

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
GRAY='\033[0;37m'
NC='\033[0m'

step()  { echo -e "\n${CYAN}▶ $1${NC}"; }
ok()    { echo -e "  ${GREEN}✓ $1${NC}"; }
warn()  { echo -e "  ${YELLOW}⚠ $1${NC}"; }
err()   { echo -e "  ${RED}✗ $1${NC}"; }

# ── Parse args ──────────────────────────────────────────────────────────────
NO_BUILD=false
DOWN=false
for arg in "$@"; do
    case "$arg" in
        --no-build) NO_BUILD=true ;;
        --down)     DOWN=true ;;
    esac
done

# ── Tear-down mode ──────────────────────────────────────────────────────────
if $DOWN; then
    step "Tearing down Meridian Commerce system"
    kubectl delete namespace meridian --ignore-not-found 2>/dev/null || true
    # Clean up temp tars if they exist
    rm -f "$ROOT/"*.tar
    ok "Namespace meridian deleted"
    echo -e "\n${GREEN}Done. All Meridian Commerce resources removed.${NC}\n"
    exit 0
fi

# ── Banner ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${MAGENTA}╔═══════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║       MERIDIAN COMMERCE — ONE-CLICK STARTUP SCRIPT       ║${NC}"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════════╝${NC}"

# ── Pre-flight checks ──────────────────────────────────────────────────────
step "Pre-flight checks"

if docker version --format '{{.Server.Version}}' > /dev/null 2>&1; then
    ok "Docker engine is running"
else
    err "No Docker engine. Start OrbStack (or Docker Desktop) and try again."
    exit 1
fi

if kubectl cluster-info > /dev/null 2>&1; then
    ok "Kubernetes cluster is reachable"
else
    err "Kubernetes is not reachable. Enable Kubernetes in OrbStack (or Docker Desktop)."
    exit 1
fi

# kind (used on GitHub Codespaces / any host without OrbStack or Docker
# Desktop) runs its own containerd and has no built-in LoadBalancer
# controller: freshly built images are invisible to it until explicitly
# `kind load`-ed, and `type: LoadBalancer` Services never get an EXTERNAL-IP.
# Both are handled automatically below when a kind context is detected.
CURRENT_CTX="$(kubectl config current-context 2>/dev/null || true)"
ON_KIND=false
if [[ "$CURRENT_CTX" == kind-* ]]; then
    ON_KIND=true
    KIND_CLUSTER="${CURRENT_CTX#kind-}"
    ok "Detected kind cluster '${KIND_CLUSTER}' — will load images directly and expose services via NodePort"

    # Alertmanager's webhook (k8s/monitoring/alertmanager.yaml) targets the
    # bare container name `sre-agent-api`, which OrbStack/Docker Desktop
    # resolve automatically across every container on the host. Plain Docker
    # (kind-in-Codespaces) only resolves that name for containers sharing a
    # network with it, so join the kind node to the platform's Docker network
    # and refresh CoreDNS so pods pick it up too — no manifest divergence
    # needed, `sre-agent-api` then resolves the same way it does on OrbStack.
    if docker network inspect sre-platform-network > /dev/null 2>&1; then
        docker network connect sre-platform-network "${KIND_CLUSTER}-control-plane" 2>/dev/null || true
        kubectl rollout restart deployment/coredns -n kube-system > /dev/null 2>&1 || true
        kubectl rollout status deployment/coredns -n kube-system --timeout=60s > /dev/null 2>&1 || true
        ok "Bridged kind to the platform's Docker network — sre-agent-api is resolvable from pods"
    else
        warn "Platform's 'sre-platform-network' not found yet — start platform/docker-compose.yaml too, then re-run ./start.sh so Alertmanager can reach it"
    fi
fi

# ── Build & Inject Docker images ───────────────────────────────────────────
if ! $NO_BUILD; then
    step "Building Docker images (OrbStack/Docker share the daemon — no injection needed)"

    # Parallel arrays — bash 3.2 compatible, handles spaces in paths
    NAMES=("meridian-api-gateway" "meridian-checkout-service" "meridian-payment-service" "meridian-inventory-service" "meridian-load-generator" "meridian-signals")
    PATHS=("$ROOT/services/api-gateway" "$ROOT/services/checkout-service" "$ROOT/services/payment-service" "$ROOT/services/inventory-service" "$ROOT/load-generator" "$ROOT/mcp/meridian-signals")

    i=0
    while [ $i -lt ${#NAMES[@]} ]; do
        name="${NAMES[$i]}"
        path="${PATHS[$i]}"
        echo -ne "  ${GRAY}Building ${name}...${NC}"
        docker build -t "${name}:latest" "$path" > /dev/null 2>&1
        echo -e " ${GREEN}done!${NC}"
        i=$((i + 1))
    done
    ok "All images built (used directly via imagePullPolicy=IfNotPresent)"

    if $ON_KIND; then
        step "Loading images into kind cluster '${CURRENT_CTX#kind-}'"
        i=0
        while [ $i -lt ${#NAMES[@]} ]; do
            name="${NAMES[$i]}"
            echo -ne "  ${GRAY}Loading ${name}...${NC}"
            kind load docker-image "${name}:latest" --name "${CURRENT_CTX#kind-}" > /dev/null 2>&1
            echo -e " ${GREEN}done!${NC}"
            i=$((i + 1))
        done
        ok "All images loaded into kind"
    fi
else
    warn "Skipping image builds and injection (--no-build flag)"
fi

# ── Deploy infrastructure ──────────────────────────────────────────────────
step "Deploying infrastructure"
kubectl apply -f "$ROOT/k8s/namespace.yaml" > /dev/null 2>&1

# The app itself, and the rest of the monitoring stack (Prometheus/Loki/Grafana),
# have no dependency on Sentinel. Only Alertmanager's webhook to Sentinel needs
# the cluster token, and that token can only exist once this cluster has already
# been connected in Sentinel — which requires the app to be reachable first. So
# a missing token must never block startup; it only leaves Alertmanager itself
# unready until the Secret is added and it's rolled.
SENTINEL_TOKEN_MISSING=false
if ! kubectl get secret meridian-alertmanager-secret -n meridian > /dev/null 2>&1; then
    SENTINEL_TOKEN_MISSING=true
    warn "No Secret 'meridian-alertmanager-secret' yet — deploying everything except Sentinel alert delivery."
    echo "  Prometheus/Loki/Grafana and the app will come up normally; Alertmanager will"
    echo "  stay unready until you connect this cluster in Sentinel and run:"
    echo "    cp k8s/monitoring/alertmanager-secret.example.yaml k8s/monitoring/alertmanager-secret.yaml"
    echo "    # paste the token from Sentinel (Clusters → Connect) into it, then:"
    echo "    kubectl apply -f k8s/monitoring/alertmanager-secret.yaml"
    echo "    kubectl rollout restart deployment/alertmanager -n meridian"
fi

kubectl apply -f "$ROOT/k8s/monitoring/" > /dev/null 2>&1
ok "Monitoring stack applied"

# ── Deploy core services ───────────────────────────────────────────────────
kubectl apply -f "$ROOT/k8s/services.yaml" > /dev/null 2>&1
kubectl apply -f "$ROOT/k8s/mcp-signals.yaml" > /dev/null 2>&1
ok "Core Application & Signals MCP updated"

# ── kind-only: expose LoadBalancer Services via NodePort ───────────────────
# Plain kind has no LoadBalancer controller, so these Services would sit at
# <pending> forever. Patch them to NodePort at the fixed ports that
# k8s/kind-config.yaml's extraPortMappings forward from localhost.
if $ON_KIND; then
    step "Patching Services to NodePort (kind has no LoadBalancer controller)"
    kubectl patch svc api-gateway       -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":8000,"targetPort":8000,"nodePort":30800}]}}' > /dev/null 2>&1
    kubectl patch svc checkout-service  -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":8001,"targetPort":8001,"nodePort":30801}]}}' > /dev/null 2>&1
    kubectl patch svc inventory-service -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":8002,"targetPort":8002,"nodePort":30802}]}}' > /dev/null 2>&1
    kubectl patch svc load-generator    -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":8003,"targetPort":8003,"nodePort":30803}]}}' > /dev/null 2>&1
    kubectl patch svc payment-service   -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":8004,"targetPort":8004,"nodePort":30804}]}}' > /dev/null 2>&1
    kubectl patch svc prometheus        -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":9090,"targetPort":9090,"nodePort":30909}]}}' > /dev/null 2>&1
    kubectl patch svc alertmanager      -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":9093,"targetPort":9093,"nodePort":30913}]}}' > /dev/null 2>&1
    kubectl patch svc grafana           -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":3001,"targetPort":3000,"nodePort":30301}]}}' > /dev/null 2>&1
    kubectl patch svc loki              -n meridian -p '{"spec":{"type":"NodePort","ports":[{"port":3100,"targetPort":3100,"nodePort":30310}]}}' > /dev/null 2>&1
    ok "Services patched — reachable at localhost via kind's extraPortMappings"
fi

# ── Force code synchronization ─────────────────────────────────────────────
# If the pods are already running, Kubernetes won't automatically restart them
# just because the image store changed. We force a rollout restart so they grab the new code.
kubectl rollout restart deployment -n meridian api-gateway checkout-service payment-service inventory-service load-generator meridian-signals > /dev/null 2>&1 || true
ok "Forced pods to restart with newest code!"


# ── Wait for pods to be ready ──────────────────────────────────────────────
step "Waiting for all pods to become ready (timeout: 180s)"

TIMEOUT=180
ELAPSED=0
ALL_READY=false

while ! $ALL_READY && [ $ELAPSED -lt $TIMEOUT ]; do
    sleep 3
    ELAPSED=$((ELAPSED + 3))

    PODS=$(kubectl get pods -n meridian --no-headers 2>/dev/null || true)
    if [ -z "$PODS" ]; then continue; fi

    TOTAL=$(echo "$PODS" | wc -l | tr -d ' ')
    READY=$(echo "$PODS" | grep "Running" | grep "1/1" | wc -l | tr -d ' ')

    if [ "$TOTAL" -gt 0 ]; then
        PCT=$((READY * 100 / TOTAL))
    else
        PCT=0
    fi

    echo -ne "\r  ${GRAY}Pods: ${READY}/${TOTAL} ready (${PCT}%) — ${ELAPSED}s elapsed${NC}  "

    if [ "$READY" -eq "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
        ALL_READY=true
    fi
done

echo ""

if $ALL_READY; then
    ok "All pods are running!"
else
    warn "Some pods are not ready yet. Current status:"
    echo ""
    kubectl get pods -n meridian 2>/dev/null
    echo ""
fi

# ── Print access summary ───────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              MERIDIAN COMMERCE IS RUNNING                    ║${NC}"
echo -e "${GREEN}╠═══════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║                                                           ║${NC}"
echo -e "${GREEN}║  📡  API Gateway       ${NC}http://localhost:8000${GREEN}              ║${NC}"
echo -e "${GREEN}║  💳  Checkout Service  ${NC}http://localhost:8001${GREEN}              ║${NC}"
echo -e "${GREEN}║  📦  Inventory Service ${NC}http://localhost:8002${GREEN}              ║${NC}"
echo -e "${GREEN}║  🔄  Load Generator    ${NC}http://localhost:8003${GREEN}              ║${NC}"
echo -e "${GREEN}║  💰  Payment Service   ${NC}http://localhost:8004${GREEN}              ║${NC}"
echo -e "${GREEN}║                                                           ║${NC}"
echo -e "${GREEN}║  📈  Prometheus        ${NC}http://localhost:9090${GREEN}              ║${NC}"
echo -e "${GREEN}║  📊  Grafana           ${NC}http://localhost:3001${GREEN}              ║${NC}"
echo -e "${GREEN}║  🔔  Alertmanager      ${NC}http://localhost:9093${GREEN}              ║${NC}"
echo -e "${GREEN}║  📝  Loki              ${NC}http://localhost:3100${GREEN}              ║${NC}"
echo -e "${GREEN}║                                                           ║${NC}"
echo -e "${GREEN}╠═══════════════════════════════════════════════════════════╣${NC}"
echo -e "${YELLOW}║  Tear down:   ./start.sh --down                          ║${NC}"
echo -e "${YELLOW}║  Rebuild:     ./start.sh                                 ║${NC}"
echo -e "${YELLOW}║  No rebuild:  ./start.sh --no-build                      ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

if $SENTINEL_TOKEN_MISSING; then
    warn "Alertmanager is up but not sending to Sentinel yet — no cluster token."
    echo "  The app and its own telemetry (Prometheus/Loki/Grafana) are fully functional."
    echo "  Connect this cluster in Sentinel (Clusters → Connect), then see k8s/README.md#secrets."
    echo ""
fi
