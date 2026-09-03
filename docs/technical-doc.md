# Sadiq.ai price intelligence tool — technical documentation

**Status:** Multi-tenant MVP — env + catalog mapping + customer API ready
**Owner:** sadiq.ai pricing/growth team
**Stack decided:** Python (FastAPI) + MongoDB + Celery/Redis + Playwright + Next.js

Sadiq.ai is tenant `sadiq`. Other marketplaces are onboarded with
`POST /v1/admin/tenants` and a field map — they do not share Sadiq's
catalog or API key. Customer-facing reference: `docs/customer-api.md`.

---

## 1. What this tool does

For every product on sadiq.ai, it finds the same (or closest equivalent)
product on competitor marketplaces, tracks both prices over time, and
tells you when a competitor is undercutting you — without anyone
manually checking competitor sites.

Three things make this different from a simple "scrape and compare"
script:

1. **It reads your catalog directly from your MongoDB**, not by
   scraping your own site — faster, free, always fresh, zero load on
   your storefront.
2. **It matches products with a confidence-tiered pipeline**, so
   branded Electronics (which have model numbers) get matched
   automatically and reliably, while unbranded Fashion/Home items
   (which don't) go through a lightweight human review queue instead
   of being auto-trusted and silently wrong.
3. **It's built to run on entirely free infrastructure today** and
   scale onto paid infrastructure later without a rewrite — same code,
   bigger instances.

---

## 2. Architecture

```
 Your MongoDB (sadiq.ai)          Competitor sites (Daraz, Telemart, ...)
   products, categories                    product pages
          |                                        |
          v                                        v
   Catalog sync service                    Price scraper service
 (direct DB read / change                  (Playwright, one scraper
  streams — no scraping                     class per competitor,
  needed for your own data)                 rate-limited)
          |                                        |
          +--------------------+-------------------+
                               |
                               v
                     Matching engine
        Tier A: brand+model / barcode  -> auto-approved
        Tier B: fuzzy text (+ optional embeddings)
                 -> human review queue
                               |
                               v
                  Price comparison database
              (MongoDB: matches, price history,
                     comparisons, alerts)
                               |
                 +-------------+-------------+
                 v                           v
          Dashboard / API              Alerts (email/Slack)
```

This mirrors the diagram shown earlier in chat. Every box above is a
real module in the delivered codebase (`sadiq-price-intel.zip`).

### Why MongoDB throughout, not Postgres

You already run MongoDB for the storefront and your team already knows
it. Rather than introduce a second database technology (extra ops
burden, extra thing to monitor, extra thing to pay for), this tool's
own data — competitor listings, matches, price history, alerts — lives
in a **separate database on the same cluster** (`price_intel`, next to
your existing `sadiq` database). Zero new infrastructure to provision.

`price_snapshots` uses a native MongoDB **time-series collection**
(available since MongoDB 5.0), which is exactly the right storage
primitive for "many timestamped price points per product" and comes
with automatic compression and efficient range queries built in.

---

## 3. Pulling your own catalog: direct MongoDB access

You said you already have a MongoDB database with all products and
categories. This is the best possible starting point — it means step 1
(getting your own prices into the system) requires **no scraping at
all**, which is both simpler and something scraping-your-own-site would
never have been the best answer to anyway.

Two options, both implemented in `app/services/catalog_sync.py`:

| Option | How it works | When to use |
|---|---|---|
| **Scheduled full sync** | Every N hours, read every document in `products` and cache a normalized copy | Start here. Simple, predictable, easy to debug. |
| **Change Streams** | A long-running process tails MongoDB's oplog and reacts the instant a product changes | Adopt once you want near-real-time re-comparison instead of waiting for the next scheduled sync |

**Security:** create a MongoDB user with **read-only** access scoped to
the `sadiq` database for this tool to connect with. That way a bug in
this codebase can never write to, or corrupt, your live product data.
In Atlas: Database Access → Add New Database User → Built-in Role →
`Read Only` on the `sadiq` database.

**One thing to do before running this for real:** `python scripts/inspect_catalog.py`
and confirm field names. Sadiq's live `products` documents use `name` (not
`title`), `group_id`, `marketplace`, `thumbnail`, `after_discount` / `price`.
The mapper in `app/catalog/mapper.py` already matches that schema.

---

## 4. Getting competitor prices: scraping

For every competitor (Daraz.pk, Telemart, iShopping.pk, etc.), the tool
runs a Playwright-driven headless browser that searches the site for
each of your product titles and extracts title, price, and URL from the
results.

**Why one scraper is written per competitor, not per category:** the
DOM structure is a property of the *site*, not the *product category*.
Daraz.pk's Electronics and Fashion listing pages use the same card
layout. This means "all categories at once" doesn't multiply your
scraping work — you still only write ~1 scraper per competitor site,
total, regardless of how many product categories you track.

**Delivered:** `app/scrapers/daraz.py` as a complete, correctly-shaped
template (search flow, selector strategy, pagination handling). Its
exact CSS selectors need a five-minute check against the live site
before first use — sites change their markup periodically and this was
built without live browser access to daraz.pk. The file has inline
instructions for verifying and updating them.

**Being a good citizen (and staying out of trouble):**
- Check each target site's `/robots.txt` and Terms of Service before scraping it.
- Rate-limit requests (`SCRAPER_REQUEST_DELAY_SECONDS`, default 2s) — don't hammer their servers.
- Identify your bot honestly in the User-Agent, with a contact email — see `config.py`. A polite bot header means a site owner emails you if there's a problem, instead of just blocking you.
- Never bypass a login wall, CAPTCHA-solving service abuse, or paywall to get data.
- If a site's anti-bot protection blocks headless Chromium, the next step (once you're past MVP) is a managed scraping API (ScraperAPI, ScrapingBee, Bright Data all have free trial tiers) rather than trying to defeat the protection yourself.

**Scaling note:** the MVP scraper searches once per product. Once your
tracked catalog grows past a few hundred products, switch to crawling
each competitor's category pages in bulk and matching against that
instead of issuing one search per product — same scraper classes,
different `search()` implementation.

---

## 5. Matching engine — the hardest and most important part

This is the part that decides whether "Rs. 5,990 on Daraz" is actually
telling you something true about your Rs. 6,296 earbuds, or comparing
two unrelated products.

### Why "all categories at once" needs a tiered strategy

You chose to launch across the whole catalog rather than starting with
one category. That's doable, but only because Electronics and Fashion
(for example) need fundamentally different matching approaches — the
tool has to know the difference, or it will silently produce wrong
comparisons on the categories where matching is hard.

| Tier | Applies to | Signal | Confidence | Human review? |
|---|---|---|---|---|
| **A — rule-based** | Electronics, branded Beauty/Health, packaged Groceries | Identical barcode, or matching brand + model number | 98–100 | No — auto-approved |
| **B — fuzzy text** | Fashion, Home Decor, unbranded accessories | Title similarity (rapidfuzz), same-category bonus | 60–90ish, noisy | **Yes** — goes to review queue unless it clears the auto-approve bar |
| **B+ — embeddings (optional, not enabled by default)** | Same as B, once fuzzy text's false-positive rate is too high | Semantic similarity (sentence-transformers), catches "abaya" ≈ "modest maxi dress" | Similar range to B, generally more accurate | Same as B |

Both tiers are tried for every product automatically — Tier A first,
falling through to Tier B only if no deterministic match is found. You
don't configure this per category; it falls out naturally from whether
the product happens to have a brand+model or not.

**The review queue (`GET /matches/pending`, `POST /matches/{id}/approve`)
is the safety net that makes "launch on everything" viable on day one.**
Electronics will barely touch this queue (Tier A handles almost
everything). Fashion and Home Decor will generate a steady stream of
candidate matches that a person clears in a few minutes a day — far
cheaper than either (a) blocking launch until fashion-matching is
perfect, or (b) auto-trusting fuzzy matches and showing customers wrong
comparisons.

### Turning on embeddings later

`app/services/matching/embeddings.py` is written and ready but not
wired into the default pipeline. Enable it once you've validated the
pipeline on structured categories and want to reduce the review queue
size for Fashion — see the comment block in `pipeline.py` for exactly
where to plug it in (re-ranking the top fuzzy-text candidates when the
fuzzy score lands in an ambiguous middle band).

---

## 6. Price comparison and alerts

Once matches are approved (auto or by a human), `comparison.py` runs on
a schedule:

1. For every approved match, fetch both current prices.
2. Record a price snapshot for both sides (powers trend charts).
3. Compute the gap: `(our_price - their_price) / our_price * 100`.
4. If the competitor is cheaper by more than `ALERT_PRICE_GAP_PCT`
   (default 5%), raise an alert — deduplicated so you don't get spammed
   for the same product every 6 hours.

Alerts can go to email or Slack (`app/services/alerts.py`) — wire in a
real provider (SES/Postmark/SendGrid) when you're ready; a Slack
webhook works out of the box.

---

## 7. API surface

| Endpoint | Purpose |
|---|---|
| `GET /products` | Browse your synced catalog |
| `GET /matches/pending` | The human review queue |
| `POST /matches/{id}/approve` / `/reject` | Review a candidate match |
| `GET /comparisons` | Latest price comparisons, sorted by biggest gap |
| `POST /comparisons/recompute` | Force an immediate recompute |
| `GET /comparisons/{product_id}/history` | Price history for a trend chart |
| `GET /alerts` | Unacknowledged price-drop alerts |

Full interactive docs at `/docs` once the FastAPI app is running
(Swagger UI, generated automatically).

---

## 8. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend / API | Python 3.11, FastAPI | Best ecosystem for scraping (Playwright) and matching (rapidfuzz, sentence-transformers); async-native, fast to build on |
| Database | MongoDB (your existing Atlas cluster, new `price_intel` database) | No new infrastructure; your team already knows it |
| Scheduling / background jobs | Celery + Redis | Standard, battle-tested; free Redis via Upstash |
| Scraping | Playwright | Handles JS-rendered listing pages, which most PK e-commerce sites use |
| Rule-based matching | Plain Python | Deterministic, no dependency needed |
| Fuzzy matching | rapidfuzz | Fast, pure Python/C, no model download |
| Semantic matching (optional) | sentence-transformers | Free, local, small model (~90MB), no per-request cost |
| Dashboard | Next.js + React (recommended) | Matches the stack your storefront already uses ([`_next/image` asset paths confirm sadiq.ai runs on Next.js](https://www.sadiq.ai)), so your existing frontend team can maintain it without learning a new framework |

---

## 9. Deployment — free first, then production

### Phase 0: free tier (now)
| Component | Free option |
|---|---|
| API + Celery worker/beat | Render or Railway free web service + background worker |
| MongoDB | Your existing Atlas cluster, new database (or Atlas free M0 if you'd rather isolate it entirely) |
| Redis | Upstash free tier |
| Dashboard | Vercel free tier |
| Scheduling fallback | GitHub Actions cron (2,000 free minutes/month) if you'd rather not run Celery beat yet |

### Phase 1: production hardening (once it's proving value)
- Move off free-tier compute to a paid instance sized for your actual scrape volume (competitor scraping is the heaviest workload).
- Add a managed scraping proxy/API if any competitor site starts blocking you.
- Move Redis/Mongo to paid tiers sized for your data volume.
- Add monitoring (Sentry for errors, a simple uptime check on `/health`).
- Add authentication to the API and dashboard (currently open — fine for an internal MVP, not fine once it's "a tool anyone on the team can log into").
- Rate-limit and cache the public API if you ever expose it beyond your internal team.
- Add a proper CI/CD pipeline (GitHub Actions: lint, test, deploy on merge).

Nothing above requires rewriting application code — it's entirely
infrastructure and configuration changes, which is the point of
starting on free tiers with this architecture.

---

## 10. Suggested rollout plan

1. **Week 1** — Point `SADIQ_MONGO_URI` at a read-only user on your real
   cluster, fix the field mapping in `catalog_sync.py`, confirm
   `sync_full_catalog()` pulls your real products correctly.
2. **Week 1-2** — Verify and fix the Daraz selectors in `daraz.py`
   against the live site; confirm scraping returns real listings for a
   handful of test products across a couple of categories.
3. **Week 2** — Run the matching pipeline against real data. Check the
   `/matches/pending` queue by hand for a day or two — this is where
   you'll tune `MATCH_MIN_SCORE` / `MATCH_AUTO_APPROVE_SCORE` for your
   actual catalog.
4. **Week 3** — Turn on the comparison + alerting schedule. Get the
   first real "competitor is cheaper" alert into Slack.
5. **Week 4+** — Add a second competitor (Telemart, iShopping.pk, ...)
   by copying `daraz.py`. Build the Next.js dashboard on top of the
   existing API. Consider embeddings for Fashion if the fuzzy-match
   review queue is too large to keep up with by hand.

---

## 11. What's delivered vs. what needs your input

**Delivered and working** (`sadiq-price-intel.zip`):
config, MongoDB layer, data models, catalog sync (direct DB read +
change streams), full matching pipeline (tested — see
`scripts/demo_matching.py`), comparison + alerting logic, complete
FastAPI app with all routes, Celery scheduling, Docker Compose for
local dev, a scraper template for Daraz.pk.

**Needs your input before going live:**
- Real field names in your `products`/`categories` collections (`catalog_sync.py`)
- A read-only MongoDB user for this tool to connect with
- Verified CSS selectors for each competitor site you want to track
- A decision on where the free-tier services get provisioned (Render vs Railway, etc.)
