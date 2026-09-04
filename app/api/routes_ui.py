from __future__ import annotations

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

from app.services import automation, discovery
from app.services.scrape import compare_storefront_and_competitor
from app.services.tenants import find_tenant_by_slug
from app.services.urls import competitor_label

router = APIRouter(tags=["ui"])


def _page(
    result: dict | None = None,
    error: str | None = None,
    ours: str = "",
    theirs: str = "",
) -> str:
    banner = ""
    cards = ""
    if error:
        banner = f'<div class="banner err">{_esc(error)}</div>'
    elif result and result.get("matches") is not None:
        cheaper = result.get("cheaper")
        tone = {"competitor": "warn", "us": "ok", "tie": "ok"}.get(cheaper, "ok")
        if not result.get("matches"):
            tone = "err"
        banner = (
            f'<div class="banner {tone}">'
            f'<p class="head">{_esc(result.get("headline") or "")}</p>'
            f'<p>{_esc(result.get("detail") or "")}</p>'
            f"</div>"
        )
        ours_p = result.get("our_product") or {}
        cards = f"""
        <article class="solo">
          <h3>Your listing</h3>
          <p class="price">Rs. {_fmt(ours_p.get("price"))}</p>
          <p class="muted">{_esc(ours_p.get("marketplace") or "Your store")}</p>
          <p>{_esc(ours_p.get("title") or "")}</p>
        </article>
        """
        rows = ""
        ours_url = ours_p.get("url") or ""
        rows += (
            "<tr class='you'>"
            f"<td>{_esc(ours_p.get('marketplace') or 'Your store')}</td>"
            f"<td>Rs. {_fmt(ours_p.get('price'))}</td>"
            "<td>Your listing</td>"
            f"<td><a href='{_esc(ours_url)}' target='_blank' rel='noreferrer'>Open</a></td>"
            "</tr>"
        )
        for row in result.get("matches") or []:
            listing = row.get("competitor_listing") or {}
            rows += (
                "<tr>"
            "<td>"
            f"{_esc(competitor_label(listing.get('competitor') or ''))}"
            f"<div class='muted'>{_esc((listing.get('title') or '')[:90])}</div>"
            "</td>"
            f"<td>Rs. {_fmt(listing.get('price'))}</td>"
                f"<td>{_esc(row.get('headline'))}</td>"
                f"<td><a href='{_esc(listing.get('url'))}' target='_blank' rel='noreferrer'>Open</a></td>"
                "</tr>"
            )
        cards += (
            "<table><thead><tr><th>Shop</th><th>Price</th><th>vs you</th><th></th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
        cards += _leaderboard_html(ours_p, result.get("matches") or [])
        skipped = result.get("skipped") or []
        looked = result.get("searched_urls") or []
        if looked:
            cards += f"<p class='muted'>Looked at {len(looked)} product page(s).</p>"
        if skipped:
            cards += "<p class='muted'>Skipped " + str(len(skipped)) + " weak or unreadable pages.</p>"
            cards += "<ul class='errs'>" + "".join(
                f"<li>{_esc(s.get('reason'))}: {_esc((s.get('url') or '')[:80])}</li>"
                for s in skipped[:6]
            ) + "</ul>"
    elif result:
        cheaper = result.get("cheaper")
        tone = {"competitor": "warn", "us": "ok", "tie": "ok"}.get(cheaper, "ok")
        banner = (
            f'<div class="banner {tone}">'
            f'<p class="head">{_esc(result.get("headline") or "")}</p>'
            f'<p>{_esc(result.get("detail") or "")}</p>'
            f"</div>"
        )
        ours_p = result.get("our_product") or {}
        theirs_p = result.get("competitor_listing") or {}
        cards = f"""
        <div class="grid">
          <article>
            <h3>Your listing</h3>
            <p class="price">Rs. {_fmt(ours_p.get("price"))}</p>
            <p class="muted">{_esc(ours_p.get("marketplace") or "Your store")}</p>
            <p>{_esc(ours_p.get("title") or "")}</p>
          </article>
          <article>
            <h3>{_esc((theirs_p.get("competitor") or "competitor").title())}</h3>
            <p class="price">Rs. {_fmt(theirs_p.get("price"))}</p>
            <p class="muted">Competitor</p>
            <p>{_esc(theirs_p.get("title") or "")}</p>
          </article>
        </div>
        """
        if result.get("difference_rs"):
            cards += (
                f'<p class="diff">Difference: <strong>Rs. {_fmt(result.get("difference_rs"))}</strong></p>'
            )
        if theirs_p.get("price"):
            cards += _leaderboard_html(
                ours_p,
                [
                    {
                        "competitor_listing": theirs_p,
                        "headline": result.get("headline"),
                    }
                ],
            )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Price compare</title>
  <style>
    :root {{
      --bg: #0f1419; --card: #1a222c; --ink: #f4f1ea; --muted: #9aa7b5;
      --accent: #e8c547; --ok: #3dd68c; --warn: #ff8a4c; --err: #ff6b6b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #243044, var(--bg));
      color: var(--ink); min-height: 100vh;
    }}
    main {{ max-width: 760px; margin: 0 auto; padding: 48px 20px 80px; }}
    h1 {{ font-size: 1.8rem; font-weight: 650; margin: 0 0 8px; }}
    .lede {{ color: var(--muted); margin: 0 0 28px; line-height: 1.5; }}
    form {{
      background: var(--card); border: 1px solid #2c3947; border-radius: 16px;
      padding: 22px; display: grid; gap: 14px;
    }}
    label {{ font-size: 0.85rem; color: var(--muted); }}
    input {{
      width: 100%; margin-top: 6px; padding: 12px 14px; border-radius: 10px;
      border: 1px solid #334155; background: #0f1720; color: var(--ink); font-size: 0.95rem;
    }}
    button {{
      margin-top: 6px; padding: 12px 16px; border: 0; border-radius: 10px;
      background: var(--accent); color: #1a1403; font-weight: 700; cursor: pointer;
    }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    .banner {{
      margin: 22px 0; padding: 18px 20px; border-radius: 14px; line-height: 1.45;
    }}
    .banner .head {{ font-size: 1.2rem; font-weight: 700; margin: 0 0 6px; }}
    .banner p {{ margin: 0; }}
    .ok {{ background: #123528; border: 1px solid #1f6a45; }}
    .warn {{ background: #3a2416; border: 1px solid #a85a2a; }}
    .err {{ background: #3a1518; border: 1px solid #a33; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    article {{ background: var(--card); border: 1px solid #2c3947; border-radius: 14px; padding: 16px; }}
    article.solo {{ margin: 18px 0; }}
    article h3 {{ margin: 0 0 8px; font-size: 0.9rem; color: var(--muted); font-weight: 600; }}
    .price {{ font-size: 1.6rem; font-weight: 700; margin: 0 0 6px; }}
    .muted {{ color: var(--muted); margin: 0 0 8px; font-size: 0.85rem; }}
    .diff {{ text-align: center; color: var(--accent); font-size: 1.05rem; }}
    a.docs {{ color: var(--muted); font-size: 0.85rem; }}
    nav {{ margin: 0 0 18px; font-size: 0.9rem; }}
    nav a {{ color: var(--accent); text-decoration: none; margin-right: 14px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; margin: 16px 0; }}
    th, td {{ text-align: left; padding: 8px 6px; border-bottom: 1px solid #2c3947; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    table a {{ color: var(--accent); }}
    table tr.you td {{ color: var(--accent); }}
    #results.hidden, #loading.hidden {{ display: none; }}
    #loading {{
      margin: 22px 0; padding: 28px 22px; border-radius: 16px;
      background: var(--card); border: 1px solid #2c3947; text-align: center;
    }}
    .spinner {{
      width: 42px; height: 42px; margin: 0 auto 16px; border-radius: 50%;
      border: 3px solid #2c3947; border-top-color: var(--accent);
      animation: spin 0.8s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    #loading h2 {{ margin: 0 0 8px; font-size: 1.15rem; }}
    #loading p {{ color: var(--muted); margin: 0 0 18px; }}
    .pulse {{
      display: grid; gap: 8px; max-width: 420px; margin: 0 auto;
    }}
    .pulse i {{
      display: block; height: 12px; border-radius: 8px;
      background: linear-gradient(90deg, #243044 25%, #3a4a5c 50%, #243044 75%);
      background-size: 200% 100%;
      animation: shimmer 1.2s ease-in-out infinite;
    }}
    .pulse i:nth-child(1) {{ width: 100%; }}
    .pulse i:nth-child(2) {{ width: 86%; animation-delay: 0.1s; }}
    .pulse i:nth-child(3) {{ width: 72%; animation-delay: 0.2s; }}
    .pulse i:nth-child(4) {{ width: 92%; animation-delay: 0.3s; }}
    @keyframes shimmer {{
      0% {{ background-position: 100% 0; }}
      100% {{ background-position: -100% 0; }}
    }}
    #loading-step {{ color: var(--accent); font-weight: 600; min-height: 1.4em; }}
    .board {{
      margin: 28px 0 8px; padding: 22px 18px 18px; border-radius: 16px;
      background: var(--card); border: 1px solid #2c3947;
    }}
    .board h2 {{
      margin: 0 0 4px; font-size: 1.15rem; font-weight: 700;
    }}
    .board .sub {{
      margin: 0 0 20px; color: var(--muted); font-size: 0.88rem;
    }}
    .podium {{
      display: grid; grid-template-columns: 1fr 1.15fr 1fr; gap: 10px;
      align-items: end; margin-bottom: 16px;
    }}
    .stage {{
      text-align: center; border-radius: 14px 14px 10px 10px;
      padding: 14px 10px 12px; border: 1px solid #2c3947;
      background: #121a24; position: relative;
      animation: rise 0.55s ease-out both;
    }}
    .stage.you {{
      border-color: #6b5a1a; box-shadow: inset 0 0 0 1px rgba(232,197,71,0.25);
    }}
    .stage .place {{
      display: inline-block; font-size: 0.72rem; font-weight: 800;
      letter-spacing: 0.04em; padding: 3px 8px; border-radius: 999px;
      margin-bottom: 8px; color: #0f1419;
    }}
    .stage.first .place {{ background: #e8c547; }}
    .stage.second .place {{ background: #c0c7d1; }}
    .stage.third .place {{ background: #c9855a; }}
    .stage .shop {{
      font-weight: 700; font-size: 0.95rem; margin: 0 0 4px;
      word-break: break-word;
    }}
    .stage .amt {{
      font-size: 1.25rem; font-weight: 800; margin: 0 0 4px; color: var(--accent);
    }}
    .stage.first .amt {{ font-size: 1.4rem; }}
    .stage .hint {{ margin: 0; font-size: 0.75rem; color: var(--muted); }}
    .stage .hint a {{ color: var(--accent); }}
    .rank a {{ color: var(--accent); font-size: 0.78rem; }}
    .stage .bar {{
      margin: 12px -10px -12px; border-radius: 0 0 9px 9px;
      background: linear-gradient(180deg, #2a3646, #1a2430);
    }}
    .stage.first .bar {{ height: 72px; background: linear-gradient(180deg, #3d3414, #241e0c); }}
    .stage.second .bar {{ height: 48px; }}
    .stage.third .bar {{ height: 32px; }}
    .stage.first {{ order: 2; padding-top: 18px; }}
    .stage.second {{ order: 1; }}
    .stage.third {{ order: 3; }}
    .ranks {{ display: grid; gap: 8px; }}
    .rank {{
      display: grid; grid-template-columns: 48px 1fr auto; gap: 10px;
      align-items: center; padding: 10px 12px; border-radius: 12px;
      background: #121a24; border: 1px solid #2c3947;
      animation: rise 0.45s ease-out both;
    }}
    .rank.you {{ border-color: #6b5a1a; }}
    .rank .badge {{
      width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center;
      font-weight: 800; font-size: 0.8rem; background: #243044; color: var(--ink);
    }}
    .rank .name {{ font-weight: 650; margin: 0; }}
    .rank .meta {{ margin: 2px 0 0; font-size: 0.78rem; color: var(--muted); }}
    .rank .amt {{ font-weight: 750; color: var(--accent); white-space: nowrap; }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @media (max-width: 640px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .podium {{ grid-template-columns: 1fr; }}
      .stage.first, .stage.second, .stage.third {{ order: 0; }}
      .stage .bar {{ height: 10px !important; }}
    }}
  </style>
</head>
<body>
  <main>
    <nav><a href="/compare">Compare one</a><a href="/automate">Automate catalog</a></nav>
    <h1>Compare a product price</h1>
    <p class="lede">Paste your storefront product link and leave the competitor box empty.
    We search the web and compare the top shops (Daraz, Smart Accessories, Apricot, ShoppersPk, and others) — one listing per shop, cheapest first.</p>
    <form method="post" action="/compare" onsubmit="return startCompare(this);">
      <div>
        <label>Your product link
          <input name="storefront_url" required placeholder="https://www.sadiq.ai/product-details/..." value="{_esc(ours)}"/>
        </label>
      </div>
      <div>
        <label>Competitor product link (optional)
          <input name="competitor_url" placeholder="Leave empty to search automatically" value="{_esc(theirs)}"/>
        </label>
      </div>
      <button type="submit">Find matches and compare</button>
    </form>
    <div id="loading" class="hidden" aria-live="polite">
      <div class="spinner" aria-hidden="true"></div>
      <h2>Comparing prices across shops</h2>
      <p id="loading-step">Searching the web for the same product…</p>
      <div class="pulse" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
    </div>
    <div id="results">
    {banner}
    {cards}
    </div>
    <p><a class="docs" href="/automate">Need to do 5,000 products?</a>
    · <a class="docs" href="/docs">API docs</a></p>
  </main>
  <script>
    function startCompare(form) {{
      var btn = form.querySelector("button");
      btn.disabled = true;
      btn.textContent = "Comparing…";
      var results = document.getElementById("results");
      var loading = document.getElementById("loading");
      if (results) results.classList.add("hidden");
      if (loading) loading.classList.remove("hidden");
      var steps = [
        "Searching the web for the same product…",
        "Checking Daraz…",
        "Checking Smart Accessories…",
        "Checking Apricot and ShoppersPk…",
        "Reading prices and matching titles…",
        "Building your comparison…"
      ];
      var i = 0;
      var el = document.getElementById("loading-step");
      setInterval(function () {{
        i = (i + 1) % steps.length;
        if (el) el.textContent = steps[i];
      }}, 2800);
      return true;
    }}
  </script>
</body>
</html>"""


def _esc(value) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt(value) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _leaderboard_html(ours_p: dict, matches: list[dict]) -> str:
    """Podium + ranked list: cheapest shop is 1st (Sadiq included)."""
    entries = []
    our_price = ours_p.get("price")
    try:
        our_num = float(our_price)
    except (TypeError, ValueError):
        our_num = None
    if our_num is not None and our_num > 0:
        entries.append(
            {
                "name": "Sadiq.ai",
                "seller": ours_p.get("marketplace") or "Your store",
                "price": our_num,
                "url": ours_p.get("url") or "",
                "you": True,
            }
        )
    for row in matches:
        listing = row.get("competitor_listing") or {}
        try:
            price = float(listing.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        entries.append(
            {
                "name": competitor_label(listing.get("competitor") or "Shop"),
                "seller": (listing.get("title") or "")[:70],
                "price": price,
                "url": listing.get("url") or "",
                "you": False,
            }
        )
    if len(entries) < 2:
        return ""

    entries.sort(key=lambda item: (item["price"], 0 if item["you"] else 1))
    top = entries[:5]
    cheapest = top[0]["price"]

    podium_slots = []
    # Visual order on desktop: 2nd | 1st | 3rd
    for rank, css in ((2, "second"), (1, "first"), (3, "third")):
        if rank > len(top):
            continue
        item = top[rank - 1]
        you = " you" if item["you"] else ""
        gap = item["price"] - cheapest
        hint = "Cheapest" if gap < 1 else f"+ Rs. {_fmt(gap)} vs 1st"
        if item["you"]:
            hint = "You're #1 — cheapest" if gap < 1 else f"{hint} · you"
        open_link = (
            f" · <a href='{_esc(item['url'])}' target='_blank' rel='noreferrer'>Open</a>"
            if item["url"]
            else ""
        )
        podium_slots.append(
            f"<div class='stage {css}{you}'>"
            f"<span class='place'>{_ordinal(rank)}</span>"
            f"<p class='shop'>{_esc(item['name'])}</p>"
            f"<p class='amt'>Rs. {_fmt(item['price'])}</p>"
            f"<p class='hint'>{_esc(item['seller'])}</p>"
            f"<p class='hint'>{_esc(hint)}{open_link}</p>"
            f"<div class='bar' aria-hidden='true'></div>"
            f"</div>"
        )

    rest = ""
    for idx, item in enumerate(top[3:], start=4):
        you = " you" if item["you"] else ""
        gap = item["price"] - cheapest
        meta = "Your listing" if item["you"] else (item["seller"] or "Competitor")
        if gap >= 1:
            meta = f"{meta} · + Rs. {_fmt(gap)} vs 1st"
        rest += (
            f"<div class='rank{you}' style='animation-delay:{0.05 * idx}s'>"
            f"<div class='badge'>{_ordinal(idx)}</div>"
            f"<div><p class='name'>{_esc(item['name'])}</p>"
            f"<p class='meta'>{_esc(meta)}</p></div>"
            f"<div class='amt'>Rs. {_fmt(item['price'])}</div>"
            f"</div>"
        )

    winner = top[0]["name"]
    sub = (
        f"{_esc(winner)} is 1st at Rs. {_fmt(cheapest)}. "
        "Ranked by lowest price (same product matches only)."
    )
    return (
        "<section class='board' aria-label='Price leaderboard'>"
        "<h2>Price leaderboard</h2>"
        f"<p class='sub'>{sub}</p>"
        f"<div class='podium'>{''.join(podium_slots)}</div>"
        + (f"<div class='ranks'>{rest}</div>" if rest else "")
        + "</section>"
    )


@router.get("/compare", response_class=HTMLResponse)
async def compare_form():
    return HTMLResponse(_page())


@router.post("/compare", response_class=HTMLResponse)
async def compare_submit(
    storefront_url: str = Form(...),
    competitor_url: str = Form(default=""),
):
    tenant = await find_tenant_by_slug("sadiq")
    ours = storefront_url.strip()
    theirs = (competitor_url or "").strip()
    if not tenant:
        return HTMLResponse(_page(error="No tenant is configured.", ours=ours, theirs=theirs), status_code=500)
    try:
        if theirs:
            result = await compare_storefront_and_competitor(
                tenant,
                storefront_url=ours,
                competitor_url=theirs,
                auto_approve=True,
            )
        else:
            result = await discovery.discover_from_storefront(tenant, ours)
        return HTMLResponse(_page(result=result, ours=ours, theirs=theirs))
    except Exception as exc:
        return HTMLResponse(
            _page(error=str(exc), ours=ours, theirs=theirs),
            status_code=400,
        )


def _automate_page(
    *,
    coverage: dict | None = None,
    unmapped: dict | None = None,
    import_result: dict | None = None,
    refresh_result: dict | None = None,
    error: str | None = None,
    mappings: str = "",
) -> str:
    coverage = coverage or {}
    unmapped = unmapped or {"items": []}
    banner = ""
    if error:
        banner = f'<div class="banner err">{_esc(error)}</div>'
    elif import_result:
        banner = (
            f'<div class="banner ok"><p class="head">Imported {import_result.get("imported", 0)} '
            f'mapping(s)</p><p>{import_result.get("failed", 0)} failed. '
            f'Saved URLs will refresh automatically on the schedule.</p></div>'
        )
        if import_result.get("errors"):
            banner += "<ul class='errs'>" + "".join(
                f"<li>{_esc(e.get('input'))}: {_esc(e.get('error'))}</li>"
                for e in import_result["errors"][:8]
            ) + "</ul>"
    elif refresh_result:
        if refresh_result.get("status") == "started":
            banner = f'<div class="banner ok"><p class="head">Refresh started</p><p>{_esc(refresh_result.get("message"))}</p></div>'
        else:
            banner = (
                f'<div class="banner ok"><p class="head">Refreshed {refresh_result.get("refreshed", 0)} '
                f'prices</p><p>{refresh_result.get("failed", 0)} failed.</p></div>'
            )

    rows = ""
    for item in (unmapped.get("items") or [])[:25]:
        pid = _esc(item.get("id"))
        rows += (
            "<tr>"
            f"<td>{_esc(item.get('title'))}</td>"
            f"<td>Rs. {_fmt(item.get('price'))}</td>"
            f"<td><form method='post' action='/automate/discover' style='margin:0;padding:0;border:0;background:none;'>"
            f"<input type='hidden' name='product_id' value='{pid}'/>"
            f"<button class='ghost tiny' type='submit'>Search web</button></form></td>"
            "</tr>"
        )
    if not rows:
        rows = "<tr><td colspan='3'>No unmapped active products in the cache. Sync the catalog first.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Automate catalog</title>
  <style>
    :root {{
      --bg: #0f1419; --card: #1a222c; --ink: #f4f1ea; --muted: #9aa7b5;
      --accent: #e8c547; --ok: #3dd68c; --warn: #ff8a4c; --err: #ff6b6b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #243044, var(--bg));
      color: var(--ink); min-height: 100vh;
    }}
    main {{ max-width: 860px; margin: 0 auto; padding: 48px 20px 80px; }}
    h1 {{ font-size: 1.8rem; font-weight: 650; margin: 0 0 8px; }}
    h2 {{ font-size: 1.15rem; margin: 28px 0 10px; }}
    .lede {{ color: var(--muted); margin: 0 0 22px; line-height: 1.55; }}
    nav {{ margin: 0 0 18px; font-size: 0.9rem; }}
    nav a {{ color: var(--accent); text-decoration: none; margin-right: 14px; }}
    .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 22px; }}
    .stat {{ background: var(--card); border: 1px solid #2c3947; border-radius: 14px; padding: 16px; }}
    .stat b {{ display: block; font-size: 1.6rem; }}
    .stat span {{ color: var(--muted); font-size: 0.85rem; }}
    ol.steps {{ color: var(--muted); line-height: 1.55; padding-left: 20px; }}
    form, .panel {{
      background: var(--card); border: 1px solid #2c3947; border-radius: 16px;
      padding: 22px; display: grid; gap: 12px; margin-bottom: 16px;
    }}
    label {{ font-size: 0.85rem; color: var(--muted); }}
    textarea {{
      width: 100%; min-height: 140px; margin-top: 6px; padding: 12px 14px; border-radius: 10px;
      border: 1px solid #334155; background: #0f1720; color: var(--ink); font-size: 0.85rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    button, .btn {{
      margin-top: 6px; padding: 12px 16px; border: 0; border-radius: 10px;
      background: var(--accent); color: #1a1403; font-weight: 700; cursor: pointer;
      text-decoration: none; display: inline-block; text-align: center;
    }}
    button.ghost, a.ghost {{ background: #2c3947; color: var(--ink); }}
    button.tiny {{ margin: 0; padding: 6px 10px; font-size: 0.8rem; }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    input[type=text], input[type=url] {{
      width: 100%; margin-top: 6px; padding: 12px 14px; border-radius: 10px;
      border: 1px solid #334155; background: #0f1720; color: var(--ink); font-size: 0.95rem;
    }}
    .banner {{ margin: 0 0 18px; padding: 18px 20px; border-radius: 14px; line-height: 1.45; }}
    .banner .head {{ font-size: 1.15rem; font-weight: 700; margin: 0 0 6px; }}
    .banner p {{ margin: 0; }}
    .ok {{ background: #123528; border: 1px solid #1f6a45; }}
    .err {{ background: #3a1518; border: 1px solid #a33; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    th, td {{ text-align: left; padding: 8px 6px; border-bottom: 1px solid #2c3947; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .mono {{ font-family: ui-monospace, Menlo, monospace; font-size: 0.75rem; color: var(--muted); word-break: break-all; }}
    ul.errs {{ color: #ffb4b4; font-size: 0.85rem; }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    @media (max-width: 640px) {{ .stats {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <nav><a href="/compare">Compare one</a><a href="/automate">Automate catalog</a></nav>
    <h1>Automate the catalog</h1>
    <p class="lede">Paste a Sadiq product link (or pick a row below). We search the web
    for the same item on other shops, compare prices, and save the matches.
    After that, refresh is automatic. Searching 5,000 SKUs at once is slow — do them
    one at a time here, or a few via the API.</p>
    {banner}
    <div class="stats">
      <div class="stat"><b>{coverage.get("active_products", "—")}</b><span>Active products</span></div>
      <div class="stat"><b>{coverage.get("mapped_to_external_competitor", "—")}</b><span>Mapped to a competitor URL</span></div>
      <div class="stat"><b>{coverage.get("coverage_pct", "—")}%</b><span>Coverage ({coverage.get("unmapped", "—")} still need a URL)</span></div>
    </div>
    <form method="post" action="/automate/discover" onsubmit="this.querySelector('button').disabled=true; this.querySelector('button').textContent='Searching the web…';">
      <label>Your product link or product_id
        <input type="text" name="storefront_url" placeholder="https://www.sadiq.ai/product-details/... or a product_id"/>
      </label>
      <button type="submit">Search the web and compare</button>
    </form>
    <h2>How this works</h2>
    <ol class="steps">
      <li>We search the web for your product title (DuckDuckGo by default; add a Serper/Google key for better results).</li>
      <li>We keep only product <em>pages</em> — not Daraz catalog/search, not social posts.</li>
      <li>We read title + price, drop weak title matches and crazy price outliers, then compare.</li>
      <li>Saved URLs refresh on a schedule. CSV import is still there if search misses a shop.</li>
    </ol>
    <div class="panel">
      <div class="row">
        <a class="btn ghost" href="/automate/unmapped.csv">Download unmapped CSV</a>
        <form method="post" action="/automate/refresh" style="margin:0;padding:0;border:0;background:none;">
          <button class="ghost" type="submit">Refresh mapped prices now</button>
        </form>
      </div>
    </div>
    <form method="post" action="/automate/bulk" onsubmit="this.querySelector('button[type=submit]').disabled=true; this.querySelector('button[type=submit]').textContent='Importing…';">
      <label>Filled mappings (CSV or product_id,competitor_url)
        <textarea name="mappings" placeholder="product_id,competitor_url&#10;6a822de15d1f9b7f071f2cfa,https://www.daraz.pk/products/spin-mop-...">{_esc(mappings)}</textarea>
      </label>
      <input type="file" accept=".csv,.txt" onchange="const r=new FileReader(); r.onload=()=>this.form.mappings.value=r.result; r.readAsText(this.files[0]);"/>
      <button type="submit">Import mappings and compare</button>
    </form>
    <h2>Still unmapped</h2>
    <table>
      <thead><tr><th>Product</th><th>Your price</th><th></th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>"""


async def _sadiq_tenant():
    tenant = await find_tenant_by_slug("sadiq")
    if not tenant:
        raise RuntimeError("No tenant is configured.")
    return tenant


@router.get("/automate", response_class=HTMLResponse)
async def automate_home():
    try:
        tenant = await _sadiq_tenant()
        return HTMLResponse(
            _automate_page(
                coverage=await automation.coverage(tenant),
                unmapped=await automation.list_unmapped(tenant, limit=25),
            )
        )
    except Exception as exc:
        return HTMLResponse(_automate_page(error=str(exc)), status_code=500)


@router.get("/automate/unmapped.csv")
async def automate_unmapped_csv():
    from fastapi.responses import Response

    tenant = await _sadiq_tenant()
    data = await automation.list_unmapped(tenant, limit=5000)
    return Response(
        content=automation.unmapped_csv(data["items"]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="unmapped-products.csv"'},
    )


@router.post("/automate/bulk", response_class=HTMLResponse)
async def automate_bulk(mappings: str = Form(...)):
    try:
        tenant = await _sadiq_tenant()
        result = await automation.import_mappings(tenant, mappings)
        return HTMLResponse(
            _automate_page(
                coverage=await automation.coverage(tenant),
                unmapped=await automation.list_unmapped(tenant, limit=25),
                import_result=result,
                mappings=mappings,
            )
        )
    except Exception as exc:
        return HTMLResponse(_automate_page(error=str(exc), mappings=mappings), status_code=400)


@router.post("/automate/discover", response_class=HTMLResponse)
async def automate_discover(
    storefront_url: str = Form(default=""),
    product_id: str = Form(default=""),
):
    try:
        tenant = await _sadiq_tenant()
        text = (storefront_url or "").strip()
        pid = (product_id or "").strip()
        if text.startswith("http"):
            result = await discovery.discover_from_storefront(tenant, text)
        elif text:
            result = await discovery.discover_product(tenant, text)
        elif pid:
            result = await discovery.discover_product(tenant, pid)
        else:
            raise ValueError("Paste a storefront URL or product_id.")
        # Reuse the compare page so multi-site results are easy to read.
        return HTMLResponse(_page(result=result, ours=result.get("our_product", {}).get("url") or text))
    except Exception as exc:
        return HTMLResponse(_automate_page(error=str(exc)), status_code=400)


@router.post("/automate/refresh", response_class=HTMLResponse)
async def automate_refresh():
    try:
        tenant = await _sadiq_tenant()
        started = {
            "status": "started",
            "message": "Refreshing saved competitor URLs in the background.",
        }
        # Run a small batch inline so the page shows a real result quickly.
        result = await automation.refresh_mapped_prices(tenant, limit=10)
        return HTMLResponse(
            _automate_page(
                coverage=await automation.coverage(tenant),
                unmapped=await automation.list_unmapped(tenant, limit=25),
                refresh_result=result or started,
            )
        )
    except Exception as exc:
        return HTMLResponse(_automate_page(error=str(exc)), status_code=400)
