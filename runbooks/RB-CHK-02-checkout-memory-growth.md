# RB-CHK-02 — Checkout memory growth

**Service:** checkout-service · **Severity:** SEV-3 → SEV-2 if OOM imminent · **Owner:** Checkout on-call

## Trigger
`process_memory_bytes_simulated{service="checkout-service"}` (or container memory)
trends steadily upward with request volume, approaching the pod memory limit;
possible OOMKill / restarts.

## First signals to check
1. Memory trend: `process_memory_bytes_simulated{service="checkout-service"}` over
   1–6h — a steady linear climb indicates a leak, not normal load.
2. Container memory vs limit (k8s tools) and any prior `OOMKilled` restarts.
3. `checkout_success_rate` — confirm whether errors are already appearing as the
   pod nears its limit.

## Diagnosis
- Monotonic memory growth tied to request count → leak (each `/process` allocates
  and never frees). Restart reclaims memory but the leak recurs until fixed in code.
- Growth flat when traffic is flat → not a leak; re-evaluate.

## Remediation (reversible, guardrailed)
- **Immediate relief:** `restart` checkout-service to reclaim memory (buys time).
- **Headroom:** `patch_resource_limits` to raise the memory limit if restarts are
  frequent, and/or `scale` out to spread load.
- **Durable fix:** open a code issue/PR for the leak (unbounded allocation in the
  `/process` path); the platform can open a revert PR if a recent change
  introduced it.

## Verify
Memory plateaus after restart and grows slower after the fix; no OOM restarts.

## Escalate
If OOM restarts are ongoing and impacting checkout success, raise to SEV-2 and
notify the service owner for an expedited code fix.
