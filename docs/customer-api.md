# PriceIntel API

**Version:** 1.0  
**Vendor:** Sadiq.ai  
**Interactive docs:** `GET /docs` (Swagger) and `GET /redoc`

PriceIntel tracks your catalog against competitor listings, records price
history, and alerts you when another marketplace is cheaper.

This is a **multi-tenant** API. Your API key scopes every request to your
account. You never see another customer's products, matches, or alerts.

---

## 1. Base URL

| Environment | URL |
|---|---|
| Production | `https://api.priceintel.sadiq.ai` |
| Sandbox / local | `http://localhost:8000` |

All customer endpoints are under `/v1`.

---

## 2. Authentication

Send your tenant API key on every request:

```http
Authorization: Bearer pi_live_your_key
```

or:

```http
X-API-Key: pi_live_your_key
```

Keys are issued when your account is created. PriceIntel stores only a hash
of the key. If a key is lost, ask your account admin to rotate it.

**Do not** use the platform admin key (`X-Admin-Key`). That key is for Sadiq
operators onboarding new customers, not for integrating the product.

### Error shape

Failed requests return:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Invalid API key."
  }
}
```

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `bad_request` | Invalid parameters |
| 401 | `unauthorized` | Missing or invalid API key |
| 403 | `forbidden` | Authenticated but not allowed |
| 404 | `not_found` | Resource does not exist in your tenant |
| 409 | `conflict` | Duplicate (e.g. tenant slug) |
| 429 | `rate_limited` | Slow down and retry |

---

## 3. Quick start

```bash
# 1. Confirm the key
curl -s http://localhost:8000/v1/me \
  -H "Authorization: Bearer $PRICEINTEL_API_KEY"

# 2. Pull your catalog into PriceIntel
curl -s -X POST http://localhost:8000/v1/catalog/sync \
  -H "Authorization: Bearer $PRICEINTEL_API_KEY"

# 3. Compute price gaps
curl -s -X POST http://localhost:8000/v1/comparisons/recompute \
  -H "Authorization: Bearer $PRICEINTEL_API_KEY"

# 4. Read the gaps (largest first)
curl -s "http://localhost:8000/v1/comparisons?min_gap_pct=5" \
  -H "Authorization: Bearer $PRICEINTEL_API_KEY"
```

---

## 4. Endpoints

### Account

#### `GET /v1/me`

Returns the authenticated tenant: slug, name, competitors, matching thresholds.

#### `GET /health`

Unauthenticated liveness probe for load balancers.

---

### Catalog

PriceIntel **does not scrape your storefront**. It reads your product database
(MongoDB today; other sources can be added per tenant) and keeps a normalized
cache used for matching and comparison.

Your original catalog is never written to.

#### `POST /v1/catalog/sync`

Copies the current state of your products into PriceIntel.

**Response**

```json
{
  "tenant": "sadiq",
  "products_synced": 14322,
  "categories_resolved": 48,
  "marketplaces_resolved": 12,
  "group_matches_upserted": 3104
}
```

If your catalog already groups the same item across marketplaces
(a `group_id` or equivalent), PriceIntel turns those groups into
auto-approved matches. Comparisons work **before** any external scraper runs.

#### `GET /v1/products`

Query parameters:

| Param | Type | Description |
|---|---|---|
| `q` | string | Case-insensitive title search |
| `category` | string | Normalized category name |
| `marketplace` | string | Marketplace / seller name |
| `group_id` | string | Canonical product group |
| `active` | bool | Filter by availability flag |
| `limit` | int | 1–200, default 50 |
| `skip` | int | Offset |

**Response**

```json
{
  "total": 14322,
  "items": [
    {
      "id": "64e4a35038023b2b950bf30c",
      "title": "Perfume",
      "price": 2875,
      "original_price": 2500,
      "currency": "PKR",
      "category": "Beauty & Personal Care",
      "marketplace": "Alfatah",
      "group_id": "65bcdfda10e5fea83eac4339",
      "active": false,
      "in_stock": false,
      "image_url": "https://...",
      "url": "https://www.sadiq.ai/product/64e4a35038023b2b950bf30c"
    }
  ]
}
```

#### `GET /v1/products/{product_id}`

One normalized product.

#### `GET /v1/groups/{group_id}`

Every listing PriceIntel knows for the same canonical product, with
`min_price` / `max_price` across marketplaces.

---

### Matches

External competitor pages (Daraz, Telemart, …) are linked to your products
with a confidence score.

| Tier | When | Typical confidence | Human review? |
|---|---|---|---|
| `catalog_group` | Same group already in your DB | 100 | No |
| `rule_based` | Same brand + model / barcode | 98–100 | No |
| `fuzzy_text` | Title similarity | 60–92 | Yes, unless above auto-approve |
| `manual` | Linked by a reviewer | — | Already reviewed |

#### `GET /v1/matches/pending`

Review queue. Each item includes your product and the competitor listing.

#### `POST /v1/matches/{match_id}/approve`

#### `POST /v1/matches/{match_id}/reject`

Optional query param: `reviewed_by` (string, who clicked).

---

### Comparisons

`gap_pct` is `(your_price - their_price) / your_price * 100`.  
Positive means **you are more expensive**.

#### `GET /v1/comparisons`

| Param | Type | Description |
|---|---|---|
| `min_gap_pct` | float | Only gaps at least this large |
| `competitor` | string | Filter by competitor / marketplace name |
| `limit` | int | 1–500, default 100 |

#### `POST /v1/comparisons/recompute`

Re-run comparisons from the latest synced prices. Also writes price
snapshots used by history charts.

#### `GET /v1/comparisons/{product_id}/history?days=30`

Time series of recorded prices for charts.

---

### Alerts

An alert is created when a competitor is cheaper by at least your
configured gap (default 5%). Duplicate unacknowledged alerts for the
same product + competitor are suppressed.

#### `GET /v1/alerts?acknowledged=false`

#### `POST /v1/alerts/{alert_id}/acknowledge`

Email (SMTP) and Slack delivery can be enabled per tenant.

---

## 5. Onboarding a new marketplace (platform operators)

This section is for Sadiq, not for end customers.

`POST /v1/admin/tenants` with header `X-Admin-Key`.

```json
{
  "slug": "acme-market",
  "name": "Acme Market",
  "storefront_base_url": "https://www.acme.example",
  "competitors": ["daraz"],
  "catalog": {
    "source": "mongodb",
    "mongo_uri": "mongodb://readonly:…@host:27017/?authSource=admin",
    "db_name": "acme",
    "products_collection": "products",
    "categories_collection": "categories",
    "marketplaces_collection": "marketplaces",
    "product_groups_collection": "product_groups",
    "product_url_template": "https://www.acme.example/p/{id}",
    "field_map": {
      "title": ["title", "name"],
      "price": "price",
      "image_url": "image",
      "category_id": "categoryId",
      "group_id": "canonicalId"
    }
  },
  "matching": { "min_score": 60, "auto_approve_score": 92 },
  "alerts": {
    "email_to": "pricing@acme.example",
    "price_gap_pct": 5.0
  }
}
```

The response includes `api_key` **once**. Give that key to the customer.
Their schema differences are handled by `field_map` — no code change and
no shared database with Sadiq.

`GET /v1/admin/tenants` lists accounts (admin key required).

---

## 6. Data isolation

| Layer | How |
|---|---|
| Auth | API key → tenant |
| Reads / writes | Every query includes `tenant_id` |
| Catalog source | Each tenant has its own Mongo URI (read-only recommended) |
| PriceIntel DB | Shared cluster, tenant-prefixed documents |

Sadiq's live `products` collection is **read-only** from this service.

---

## 7. Rate limits and scraping

- Customer API: treat **120 requests/minute/key** as the contract. Bursting
  above that may be throttled in production.
- Competitor scraping is server-side, rate-limited per site, and is not
  exposed as a "scrape this URL" endpoint.
- Check each target site's `robots.txt` and terms before enabling a scraper
  for that competitor.

---

## 8. Changelog

| Version | Notes |
|---|---|
| 1.0.0 | Tenant API keys, catalog sync, group comparisons, match review, alerts |
