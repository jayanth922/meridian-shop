# Meridian Commerce — Runbooks

The on-call knowledge base for Meridian Commerce. Each runbook maps a specific
alert / symptom to the signals to check, a diagnosis path, and safe remediation
steps. They are written for **this** application: the services, metrics, and
failure modes that actually exist here.

These are the client's source-of-truth runbooks. In production they live in
**Notion**; this folder is the importable copy.

## Import into Notion

1. In Notion: **Settings → Import → Markdown & CSV**, and select this `runbooks/`
   folder (or zip it first). Each `.md` becomes a page.
2. Create an internal integration at <https://www.notion.so/my-integrations>,
   copy its `secret_...` token.
3. Share the imported runbooks database with that integration, and copy the
   database ID from its URL.
4. In the SRE console → your cluster → **Settings**, paste the Notion token +
   database ID. The agent will read these runbooks during investigations.

## Index

| ID | Service | Trigger |
|---|---|---|
| RB-CHK-01 | checkout-service | High checkout error rate |
| RB-PAY-01 | payment-service | Payment provider outage → checkout 502 cascade |
| RB-GW-01 | api-gateway | High gateway latency |
| RB-INV-01 | inventory-service | Slow inventory queries |
| RB-CHK-02 | checkout-service | Checkout memory growth |
| RB-INV-02 | inventory-service | SKU stock-out / low stock |

## How to read these

Every runbook lists the **meridian-signals** MCP tool(s) to call first — those
give app-aware context (e.g. whether checkout 502s are a real bug or a payment
cascade) faster than raw PromQL. Remediation steps are limited to reversible,
guardrailed actions the platform's executor can take (`restart`, `scale`,
`rollback`, `patch_resource_limits`) plus app-level config changes an engineer
approves.
