# RB-CHK-01 — Checkout high error rate

**Service:** checkout-service · **Severity:** SEV-2 (customer-facing order failures) · **Owner:** Payments/Checkout on-call

## Trigger
Alert fires when checkout `/process` 5xx rate exceeds ~10% over 5 minutes, or the
`meridian-signals` verdict is `checkout_degraded` / `pipeline_degraded`.

## First signals to check
1. `order_pipeline_health(window="5m")` — read the **verdict**. If it says
   `payment_outage`, stop here and follow **RB-PAY-01** — the checkout errors are
   a downstream cascade, not a checkout bug.
2. `checkout_success_rate(window="5m")` — quantify the drop.
3. `service_error_breakdown(service="checkout-service")` — which `error_type`
   dominates: `payment_dependency_failure` / `payment_unreachable` (→ RB-PAY-01),
   or something local.
4. `runtime_config(service="checkout-service")` — is `chaos_mode` true or
   `error_rate` set high? A config/experiment left on is a common false alarm.

## Diagnosis
- Errors are mostly `payment_*` → the payment provider is the root cause → RB-PAY-01.
- `error_rate` in runtime config is elevated → a chaos/experiment setting is on;
  reset it (see remediation) rather than touching infra.
- Errors are local (500s without payment reason) with rising latency → likely
  resource pressure or a bad deploy; check recent rollouts and pod restarts.

## Remediation (reversible, guardrailed)
- **Bad config:** `POST /admin/config {"error_rate":0.15,"chaos_mode":false}` to
  restore defaults (payments engineer approves).
- **Bad deploy:** `rollback` the `checkout-service` deployment to the previous
  revision.
- **Resource pressure / stuck pods:** `restart` the deployment, or `scale` up if
  request rate is genuinely high (keep ≥1 replica).

## Verify
`checkout_success_rate` back ≥ 0.95 and `order_pipeline_health` verdict `healthy`
for 5+ minutes.

## Escalate
If payment-driven, page the payments provider owner. If a deploy regression,
notify the last deployer and open a revert PR.
