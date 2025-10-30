# src/playwright_flow.py
import os, re, pathlib, asyncio
from typing import Any, Dict, List
from .playwright_utils import (
    human_pause,
    warm_up,
    accept_banners,
    wait_busy_clear,
    blocked,
    proxy_from_env,
    fill_airport
)
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Page, Locator

# ------------ paths / constants ------------
OUT = pathlib.Path("data/debug"); OUT.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = ".pw-user"  # persistent profile keeps cookies/consent between runs
BUSY_SEL = ".aa-busy-module, .aa-busy-bg, .aa-busy-text"
BLOCK_SIGS = ("akamai-challenge-resubmit=true", "access denied", "edgesuite")

# URLs worth keeping from network for later parsing
NETWORK_KEEP = re.compile(r"(award|points|miles|fare|price|itinerary|offers?)", re.I)

# ------------ main flow ------------
async def search_and_capture(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns:
      { "network_json": [ {url, json}, ... ], "page_html": "<html ...>" }
    Retries on Akamai challenge up to 3 times (rotate your gateway session/IP between runs).
    """
    attempts, max_attempts = 0, 3
    last_html = ""

    while attempts < max_attempts:
        attempts += 1
        async with async_playwright() as p:
            # Real Chrome channel + persistent context for stable fingerprint & cookies
            browser = await p.chromium.launch_persistent_context(
                PROFILE_DIR,
                channel="chrome",            # use your installed Chrome
                headless=False,              # dev; can switch to True later
                viewport={"width": 1366, "height": 900},
                locale="en-US",
                timezone_id=os.getenv("TZ", "America/Los_Angeles"),
                proxy=proxy_from_env(),      # <- enable gateway/unblocker
                # Keep UA default from channel for authenticity (don’t override unless needed)
            )
            # Hide webdriver flag
            await browser.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            page = await browser.new_page()

            captured: List[Dict[str, Any]] = []
            async def on_response(resp):
                try:
                    url = resp.url
                    ct = (resp.headers.get("content-type") or "").lower()
                    if "json" in ct or NETWORK_KEEP.search(url):
                        js = await resp.json()
                        captured.append({"url": url, "json": js})
                except Exception:
                    pass
            page.on("response", on_response)

            try:
                # 1) Home + warm up
                await page.goto("https://www.aa.com/", wait_until="domcontentloaded")
                if await blocked(page):
                    last_html = await page.content()
                    await browser.close()
                    await asyncio.sleep(1.2 * attempts)
                    continue

                await accept_banners(page)
                await warm_up(page)
                await wait_busy_clear(page)

                # 2) Fill origin/destination
                origin = page.locator(
                    "input[name='originAirport'], input#originAirport, input#reservationFlightSearchForm\\.originAirport"
                ).first
                await fill_airport(page, origin, params["origin"], city_hint="Los Angeles")
                await wait_busy_clear(page)

                dest = page.locator(
                    "input[name='destinationAirport'], input#destinationAirport, input#reservationFlightSearchForm\\.destinationAirport"
                ).first
                await fill_airport(page, dest, params["destination"], city_hint="New York")
                await wait_busy_clear(page)

                # 3) Date picker: open → go to Dec 2025 → select 15
                opened = False
                for sel in [
                    "button[aria-label*='Depart']",
                    "button:has-text('Depart')",
                    "input[name='departDate']",
                    "input[id*='depart']",
                ]:
                    try:
                        await wait_busy_clear(page)
                        await page.locator(sel).first.click(timeout=2000)
                        opened = True
                        break
                    except PWTimeout:
                        continue
                if not opened:
                    await page.get_by_text("Depart", exact=False).first.click()

                # advance months until header shows December 2025
                for _ in range(15):
                    headers = page.locator("div[role='dialog'] >> text=/December\\s+2025/i")
                    if await headers.count():
                        break
                    try:
                        await page.get_by_role("button", name=re.compile("next", re.I)).click(timeout=1200)
                    except PWTimeout:
                        await page.locator("button:has([aria-label*='Next'])").first.click(timeout=1200)

                await page.get_by_role("button", name=re.compile(r"^15$")).first.click()
                await human_pause(250, 600)
                await wait_busy_clear(page)

                # 4) Submit
                await page.get_by_role("button", name=re.compile("search", re.I)).first.click()

                # 5) Results or challenge?
                await page.wait_for_load_state("domcontentloaded")
                if await blocked(page):
                    last_html = await page.content()
                    await browser.close()
                    await asyncio.sleep(1.2 * attempts)
                    continue

                # Wait for visible results (list container) or network idle
                try:
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_selector(
                        "[data-test-id='resultsList'], [data-testid='resultsList'], [role='list']",
                        timeout=25000
                    )
                except PWTimeout:
                    # even if container not found, still capture html for fallback parser
                    pass

                # Save debug artifacts
                html = await page.content()
                (OUT / "results.html").write_text(html, encoding="utf-8")
                await page.screenshot(path=str(OUT / "results.png"), full_page=True)

                await browser.close()
                return {"network_json": captured, "page_html": html}

            except Exception:
                try:
                    last_html = await page.content()
                    (OUT / "last.html").write_text(last_html, encoding="utf-8")
                    await page.screenshot(path=str(OUT / "last.png"), full_page=True)
                except Exception:
                    pass
                await browser.close()
                raise

    # If we get here, all attempts saw a challenge or failure
    if last_html:
        (OUT / "akamai_last.html").write_text(last_html, encoding="utf-8")
    raise RuntimeError("Blocked or failed after multiple attempts. Provide a stronger U.S. PROXY and retry.")
