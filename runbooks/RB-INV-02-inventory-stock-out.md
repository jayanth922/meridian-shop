# RB-INV-02 — SKU stock-out / low stock

**Service:** inventory-service (business signal) · **Severity:** SEV-3 (revenue/UX) · **Owner:** Catalog / Merchandising

## Trigger
`inventory_levels` reports SKUs in `out_of_stock`, or `low_stock` below threshold;
inventory-service logs `STOCK OUT` / `Low stock alert`.

## First signals to check
1. `inventory_levels(low_stock_threshold=10)` — the `out_of_stock` and
   `low_stock` lists, with per-SKU quantities.
2. Cross-check demand: `latency_percentiles` / request rate on `/items/{id}` for
   the affected SKUs to gauge how much traffic is hitting empty stock.

## Diagnosis
This is a **business** condition, not an infra fault — no restart/scale will fix
it. The job of the on-call/agent here is to surface it clearly and route it, not
to remediate infrastructure.

## Remediation (non-infra)
- Notify Merchandising/Catalog to replenish the `out_of_stock` SKUs.
- If a SKU should be hidden while empty, flag it for the storefront team.
- Do **not** restart or scale inventory-service for this alert.

## Verify
`inventory_levels` shows the SKUs back above threshold after restock.

## Escalate
Route to Merchandising with the SKU list and current quantities. Track until
restocked; this typically does not page infra on-call.
