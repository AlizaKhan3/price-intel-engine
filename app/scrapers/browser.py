"""Container-safe Chromium launch shared by every scraper."""
from __future__ import annotations

# /dev/shm is tiny on Railway/Docker. These flags keep Chromium from
# crashing with "Page crashed" / "Target crashed" when memory is tight.
CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--disable-default-apps",
    "--no-first-run",
    "--mute-audio",
    "--hide-scrollbars",
    "--disable-hang-monitor",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-ipc-flooding-protection",
    "--renderer-process-limit=2",
    "--js-flags=--max-old-space-size=64",
    "--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints",
]

_HEAVY_TYPES = frozenset({"image", "media", "font"})


async def launch_chromium(playwright):
    return await playwright.chromium.launch(headless=True, args=CHROMIUM_ARGS)


async def block_heavy_resources(route) -> None:
    if route.request.resource_type in _HEAVY_TYPES:
        await route.abort()
        return
    await route.continue_()


def is_browser_crash(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "crashed",
            "target closed",
            "has been closed",
            "browser closed",
            "connection closed",
        )
    )
