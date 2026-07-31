# RB-PAY-01 — Payment provider outage → checkout 502 cascade

**Service:** payment-service (downstream of checkout) · **Severity:** SEV-1 (orders cannot complete) · **Owner:** Payments on-call

## Trigger
`payment_provider_up = 0`, or a spike in checkout 502s whose
`service_error_breakdown` is dominated by `payment_dependency_failure` /
`payment_unreachable`. `order_pipeline_health` verdict = `payment_outage`.

## Why this matters
checkout-service calls payment-service on every `/process`. When payment
degrades or goes down, **checkout returns 502 even though checkout itself is
healthy.** Do not restart or roll back checkout — treat the payment provider as
root cause.

## First signals to check
1. `payment_provider_status(window="5m")` — `provider_up`, `charge_error_rate`,
   and `failures_per_sec_by_reason`.
2. `runtime_config(service="payment-service")` — is `provider_down: true` (an
   injected/hard outage) or `error_rate` elevated?
3. `latency_percentiles(service="payment-service")` — degraded (slow) vs hard down.

## Diagnosis
- `provider_up = 0` and `provider_down: true` in config → hard outage (or a
  deliberately toggled outage). Root cause is the provider/config, not checkout.
- `provider_up = 1` but high `charge_error_rate` / p95 → provider degraded;
  expect elevated but not total checkout failures.

## Remediation (reversible, guardrailed)
- **Config-induced outage:** `POST /admin/config {"provider_down":false,"error_rate":0.05}`
  on payment-service (payments engineer approves) — restores the provider.
- **Real provider degradation:** `restart` payment-service if a pod is wedged;
  `scale` payment-service up if it is saturated. Consider enabling a payment
  retry/circuit-breaker if one exists.
- While payment is down, checkout should **fail fast** (it already 502s) — do not
  scale checkout.

## Verify
`payment_provider_status.provider_up = true`, `charge_error_rate` back to
baseline, and `order_pipeline_health` verdict `healthy`.

## Escalate
Page the external payment provider contact; post status in the on-call channel.
Do not close until checkout success rate recovers (RB-CHK-01 verify step).
