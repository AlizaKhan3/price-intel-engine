from __future__ import annotations

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

from app.services.scrape import compare_storefront_and_competitor
from app.services.tenants import find_tenant_by_slug

router = APIRouter(tags=["ui"])


def _page(result: dict | None = None, error: str | None = None, ours: str = "", theirs: str = "") -> str:
    banner = ""
    cards = ""
    if error:
        banner = f'<div class="banner err">{_esc(error)}</div>'
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
    article h3 {{ margin: 0 0 8px; font-size: 0.9rem; color: var(--muted); font-weight: 600; }}
    .price {{ font-size: 1.6rem; font-weight: 700; margin: 0 0 6px; }}
    .muted {{ color: var(--muted); margin: 0 0 8px; font-size: 0.85rem; }}
    .diff {{ text-align: center; color: var(--accent); font-size: 1.05rem; }}
    a.docs {{ color: var(--muted); font-size: 0.85rem; }}
    @media (max-width: 640px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <h1>Compare a product price</h1>
    <p class="lede">Paste your storefront product link and a competitor product link
    (Daraz, Telemart, or any other product page). We read both prices and tell you who is cheaper.</p>
    <form method="post" action="/compare" onsubmit="this.querySelector('button').disabled=true; this.querySelector('button').textContent='Comparing…';">
      <div>
        <label>Your product link
          <input name="storefront_url" required placeholder="https://www.sadiq.ai/product-details/..." value="{_esc(ours)}"/>
        </label>
      </div>
      <div>
        <label>Competitor product link
          <input name="competitor_url" required placeholder="https://www.daraz.pk/products/..." value="{_esc(theirs)}"/>
        </label>
      </div>
      <button type="submit">Compare prices</button>
    </form>
    {banner}
    {cards}
    <p><a class="docs" href="/docs">API docs</a></p>
  </main>
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


@router.get("/compare", response_class=HTMLResponse)
async def compare_form():
    return HTMLResponse(_page())


@router.post("/compare", response_class=HTMLResponse)
async def compare_submit(
    storefront_url: str = Form(...),
    competitor_url: str = Form(...),
):
    tenant = await find_tenant_by_slug("sadiq")
    if not tenant:
        return HTMLResponse(_page(error="No tenant is configured.", ours=storefront_url, theirs=competitor_url), status_code=500)
    try:
        result = await compare_storefront_and_competitor(
            tenant,
            storefront_url=storefront_url.strip(),
            competitor_url=competitor_url.strip(),
            auto_approve=True,
        )
        return HTMLResponse(_page(result=result, ours=storefront_url, theirs=competitor_url))
    except Exception as exc:
        return HTMLResponse(
            _page(error=str(exc), ours=storefront_url, theirs=competitor_url),
            status_code=400,
        )
