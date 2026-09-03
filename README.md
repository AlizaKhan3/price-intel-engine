# PriceIntel

Multi-tenant price intelligence: compare a marketplace catalog against
competitor listings, track history, and alert on undercuts.

Sadiq.ai is the first tenant. The same API is what you sell to the next
marketplace — they get their own API key, catalog connection, and field map.

- Customer API reference: [`docs/customer-api.md`](docs/customer-api.md)
- Architecture notes: [`docs/technical-doc.md`](docs/technical-doc.md)
- Interactive docs once the API is running: http://localhost:8000/docs

## Setup

### 1. Environment

```bash
cp .env.example .env
```

`.env` is gitignored. For local Sadiq development it should contain:

| Variable | What to put |
|---|---|
| `CATALOG_MONGO_URI` | Your existing cluster URI (`authSource=admin`) |
| `CATALOG_DB_NAME` | `Sadiq-DB` for development, `Sadiq-DB-prod` only for production |
| `PRICEINTEL_MONGO_URI` | Same cluster is fine |
| `PRICEINTEL_DB_NAME` | `price_intel` (new database, created automatically) |
| `TENANT_API_KEY` | Key your app / dashboard will send as `Authorization: Bearer` |
| `ADMIN_API_KEY` | Key **only you** use to onboard other marketplaces |
| `SMTP_USER` / `SMTP_PASSWORD` | `dev@sadiq.ai` + a **Gmail App Password** (not the inbox password) |

Leave `SLACK_WEBHOOK_URL` empty until you have a Slack incoming webhook.

Collection names already match the live database (`products`, `categories`).
Sadiq product fields (`name`, `group_id`, `marketplace`, `thumbnail`, …)
are mapped in `app/catalog/mapper.py` and stored on the tenant record so
the next customer can send a different `field_map` without a code change.

### 2. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Python 3.9 is OK (eval_type_backport is in requirements). 3.11+ is preferred.
playwright install chromium   # only needed when you start scraping Daraz
```

### 3. Redis

```bash
docker compose up -d
```

Mongo for PriceIntel uses your existing remote cluster (`price_intel` DB).
A local Mongo is optional: `docker compose --profile local-mongo up -d`.

### 4. Confirm the catalog schema

```bash
python scripts/inspect_catalog.py
```

You should see `products` plus categories / marketplaces / groups. If a
lookup collection name differs, change it in `.env` — do not hardcode it.

### 5. Run the API

```bash
uvicorn app.main:app --reload
```

- Health: http://localhost:8000/health
- Swagger: http://localhost:8000/docs  
  Click **Authorize** and paste `TENANT_API_KEY`.

```bash
export KEY=pi_live_sadiq_…
curl -s http://localhost:8000/v1/me -H "Authorization: Bearer $KEY"
curl -s -X POST http://localhost:8000/v1/catalog/sync -H "Authorization: Bearer $KEY"
curl -s -X POST http://localhost:8000/v1/comparisons/recompute -H "Authorization: Bearer $KEY"
curl -s 'http://localhost:8000/v1/comparisons?min_gap_pct=5' -H "Authorization: Bearer $KEY"
```

The first sync already produces comparisons for products that share
`group_id` across marketplaces in Sadiq's own database — no scraper required.

### 6. Background jobs (after the first sync works)

```bash
celery -A app.tasks.celery_app worker --loglevel=info
celery -A app.tasks.celery_app beat --loglevel=info
```

## Matching without a database

```bash
python scripts/demo_matching.py
```

## Adding another customer

```bash
curl -s -X POST http://localhost:8000/v1/admin/tenants \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d @tenant-acme.json
```

See the payload in [`docs/customer-api.md`](docs/customer-api.md#5-onboarding-a-new-marketplace-platform-operators).

## Adding another competitor site

Copy `app/scrapers/daraz.py`, adjust selectors, register it in
`app/scrapers/registry.py`, and add the slug to that tenant's `competitors`
list. Nothing else changes.

## Gmail alerts

1. In the `dev@sadiq.ai` Google account, enable 2-Step Verification.
2. Create an **App password**.
3. Set `SMTP_PASSWORD` in `.env`.
4. Alerts fire when a competitor is cheaper by `ALERT_PRICE_GAP_PCT` (default 5%).
# price-intel-engine
