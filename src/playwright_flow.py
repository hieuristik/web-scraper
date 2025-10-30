# src/playwright_flow.py
import os, re, pathlib, asyncio, random
from typing import Any, Dict, List, Optional
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Page, Locator

# ------------ paths / constants ------------
OUT = pathlib.Path("data/debug"); OUT.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = ".pw-user"  # persistent profile keeps cookies/consent between runs
BUSY_SEL = ".aa-busy-module, .aa-busy-bg, .aa-busy-text"
BLOCK_SIGS = ("akamai-challenge-resubmit=true", "access denied", "edgesuite")

# URLs worth keeping from network for later parsing
NETWORK_KEEP = re.compile(r"(award|points|miles|fare|price|itinerary|offers?)", re.I)

# ------------ small utilities ------------
async def human_pause(min_ms=200, max_ms=700):
    await asyncio.sleep(random.uniform(min_ms/1000, max_ms/1000))

async def warm_up(page: Page):
    await page.wait_for_load_state("domcontentloaded")
    await human_pause(400, 900)
    # gentle human-ish movement; avoid doing this if page already flipped to challenge
    try:
        await page.mouse.move(200, 300)
        await human_pause()
        await page.mouse.wheel(0, 600)
        await human_pause(300, 800)
        await page.mouse.wheel(0, -400)
    except Exception:
        pass

async def accept_banners(page: Page):
    for label in ["Accept", "I Agree", "Agree", "Got it", "OK"]:
        try:
            await page.get_by_role("button", name=re.compile(label, re.I)).click(timeout=1200)
            break
        except PWTimeout:
            pass

async def wait_busy_clear(page: Page, timeout_ms: int = 8000):
    """Wait until AA's 'Loading...' overlay is not intercepting clicks."""
    deadline = page.context._loop.time() + (timeout_ms / 1000)
    overlay = page.locator(BUSY_SEL)
    while page.context._loop.time() < deadline:
        try:
            if not await overlay.first.is_visible():
                return
        except Exception:
            return
        await asyncio.sleep(0.15)
    # don't raise; just continue—site sometimes drops mask right after

async def blocked(page: Page) -> bool:
    """Detect Akamai challenge or Access Denied early."""
    u = (page.url or "").lower()
    if any(sig in u for sig in BLOCK_SIGS):
        return True
    try:
        html = (await page.content()).lower()
        return any(sig in html for sig in BLOCK_SIGS)
    except Exception:
        return False

def proxy_from_env() -> Optional[dict]:
    """Read a gateway/unblocker proxy from env for portability."""
    p = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("PROXY")
    return {"server": p} if p else None

# ------------ airport autocomplete ------------
async def fill_airport(page: Page, input_locator: Locator, code: str, city_hint: Optional[str] = None):
    await wait_busy_clear(page)
    await input_locator.click()
    try:
        await input_locator.fill("")
    except PWTimeout:
        pass
    await input_locator.type(code, delay=70)

    dropdown = page.locator("ul.ui-autocomplete").first
    listbox  = page.locator("[role='listbox']").first

    # give suggestions time to appear
    try:
        await asyncio.wait_for(asyncio.shield(dropdown.wait_for(state="visible", timeout=1500)), timeout=2.0)
    except Exception:
        try:
            await listbox.wait_for(state="visible", timeout=1500)
        except Exception:
            pass

    pats = []
    if city_hint:
        pats.append(re.compile(rf"\b{re.escape(code)}\b.*{re.escape(city_hint)}", re.I))
        pats.append(re.compile(rf"{re.escape(city_hint)}.*\b{re.escape(code)}\b", re.I))
    pats.append(re.compile(rf"\b{re.escape(code)}\b", re.I))

    # role=option first
    for pat in pats:
        opt = page.get_by_role("option", name=pat).first
        try:
            await wait_busy_clear(page)
            await opt.click(timeout=800)
            return
        except PWTimeout:
            pass

    # jQuery UI fallback
    for pat in pats:
        li = dropdown.locator("li a", has_text=pat).first
        try:
            await wait_busy_clear(page)
            await li.click(timeout=800)
            return
        except PWTimeout:
            pass

    # keyboard fallback
    for _ in range(2):
        try:
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            return
        except Exception:
            await asyncio.sleep(0.1)

    # last nudge
    await input_locator.type(" ")
    await page.keyboard.press("Backspace")
    await page.keyboard.press("Enter")

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
