# PriceIntel

Multi-tenant price intelligence: compare a marketplace catalog against
competitor listings, track history, and alert on undercuts.

Each customer is a **tenant** with their own API key, catalog connection,
field map, competitors, and alert settings.

- Customer API reference: [`docs/customer-api.md`](docs/customer-api.md)
- Architecture notes: [`docs/technical-doc.md`](docs/technical-doc.md)
- Interactive docs once the API is running: http://localhost:8000/docs

## Setup

### 1. Environment

```bash
cp .env.example .env
```

`.env` is gitignored. Fill in:

| Variable | What to put |
|---|---|
| `CATALOG_MONGO_URI` | Read-only URI for the tenant catalog (`authSource=admin`) |
| `CATALOG_DB_NAME` | Catalog database name (dev vs prod as you choose) |
| `PRICEINTEL_MONGO_URI` | Same cluster is fine |
| `PRICEINTEL_DB_NAME` | `price_intel` (new database, created automatically) |
| `TENANT_API_KEY` | Key your app / dashboard sends as `Authorization: Bearer` |
| `ADMIN_API_KEY` | Key **only operators** use to onboard other tenants |
| `SMTP_USER` / `SMTP_PASSWORD` | Alert mailbox + **app password** (not the inbox password) |

Leave `SLACK_WEBHOOK_URL` empty until you have a Slack incoming webhook.

Default collection names are `products`, `categories`, `marketplaces`, and
`product_groups`. Product fields (`name`, `group_id`, `marketplace`,
`thumbnail`, and so on) are mapped in `app/catalog/mapper.py` and stored on
the tenant record, so another customer can send a different `field_map`
without a code change.

### 2. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Python 3.9 is OK (eval_type_backport is in requirements). 3.11+ is preferred.
playwright install chromium   # only needed when you start scraping competitors
```

### 3. Redis

```bash
docker compose up -d
```

PriceIntel data lives in the `price_intel` database on your Mongo URI.
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
export KEY='<TENANT_API_KEY from .env>'
curl -s http://localhost:8000/v1/me -H "Authorization: Bearer $KEY"
curl -s -X POST http://localhost:8000/v1/catalog/sync -H "Authorization: Bearer $KEY"
curl -s -X POST http://localhost:8000/v1/comparisons/recompute -H "Authorization: Bearer $KEY"
curl -s 'http://localhost:8000/v1/comparisons?min_gap_pct=5' -H "Authorization: Bearer $KEY"
```

The first sync already produces comparisons for products that share
`group_id` across marketplaces in the catalog — no scraper required.

### 6. Automate thousands of products

Paste **your** product link. Competitor URLs are optional.

1. Open http://localhost:8000/compare
2. Paste a Sadiq product-details URL
3. Leave the competitor box empty → **Find matches and compare**

PriceIntel searches the web for the same title, keeps product pages
(Daraz `/products/…`, Telemart, PriceOye, and other shops), then compares
prices. Weak title matches and crazy price outliers are dropped.

For a batch, use `/automate` or:

```bash
curl -s -X POST http://localhost:8000/v1/automation/discover \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"storefront_url":"https://www.sadiq.ai/product-details/..."}'
```

DuckDuckGo is the default search backend (no API key). Add `SERPER_API_KEY`
or Google CSE keys in `.env` if results look thin. Searching 5,000 SKUs
overnight is a Celery job, not a single button — one product takes ~30–60s.

CSV import (`unmapped.csv`) is still available when search misses a shop.

```bash
curl -s http://localhost:8000/v1/automation/coverage -H "Authorization: Bearer $KEY"
curl -s http://localhost:8000/v1/automation/unmapped.csv -H "Authorization: Bearer $KEY" -o unmapped.csv
```

### 7. Background jobs (after the first sync works)

```bash
celery -A app.tasks.celery_app worker --loglevel=info
celery -A app.tasks.celery_app beat --loglevel=info
```

## Matching without a database

```bash
python scripts/demo_matching.py
```

## Adding another tenant

```bash
curl -s -X POST http://localhost:8000/v1/admin/tenants \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d @tenant.json
```

See the payload in [`docs/customer-api.md`](docs/customer-api.md#5-onboarding-a-new-marketplace-platform-operators).

## Adding another competitor site

Copy `app/scrapers/daraz.py`, adjust selectors, register it in
`app/scrapers/registry.py`, and add the slug to that tenant's `competitors`
list. Nothing else changes.

## Email alerts

1. Enable 2-Step Verification on the alert mailbox.
2. Create an **app password**.
3. Set `SMTP_USER` and `SMTP_PASSWORD` in `.env`.
4. Alerts fire when a competitor is cheaper by `ALERT_PRICE_GAP_PCT` (default 5%).
