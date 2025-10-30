# src/playwright_flow.py
import os, re, pathlib, asyncio
from typing import Any, Dict, List, Optional
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Page, Locator

from src.playwright_utils import (
    human_pause, warm_up, accept_banners, wait_busy_clear,
    blocked, proxy_from_env,
    # NEW robust helpers you added:
    wait_akamai_clear, force_one_way, set_depart_date_quick,
    # keep your original date helper as primary
    select_depart_date,
)

OUT = pathlib.Path("data/debug"); OUT.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = ".pw-user"

# Feature flags
PREWARM = os.getenv("AA_PREWARM", "0").strip().lower() in ("1", "true", "yes")
ROTATE  = os.getenv("AA_ROTATE",  "0").strip().lower() in ("1", "true", "yes")

NETWORK_KEEP = re.compile(r"(award|points|miles|fare|price|itinerary|offers?)", re.I)


async def debug_step(page: Page, name: str):
    try:
        (OUT / f"{name}.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    except Exception:
        pass


# ---------------- airport autocomplete ----------------
async def fill_airport(page: Page, input_locator: Locator, code: str, city_hint: Optional[str] = None):
    # Akamai bounce can happen between keystrokes—guard first
    await wait_akamai_clear(page)
    await wait_busy_clear(page)

    await input_locator.click()
    try:
        await input_locator.fill("")
    except PWTimeout:
        pass

    # Faster typing; AA tolerates this
    await input_locator.type(code, delay=50)

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

    # ARIA options first
    for pat in pats:
        opt = page.get_by_role("option", name=pat).first
        try:
            await wait_busy_clear(page)
            await opt.click(timeout=1200)
            return
        except PWTimeout:
            pass

    # jQuery UI fallback
    for pat in pats:
        li = dropdown.locator("li a", has_text=pat).first
        try:
            await wait_busy_clear(page)
            await li.click(timeout=1200)
            return
        except PWTimeout:
            pass

    # keyboard fallback
    for _ in range(3):
        try:
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            return
        except Exception:
            await asyncio.sleep(0.15)

    # last nudge
    await input_locator.type(" ")
    await page.keyboard.press("Backspace")
    await page.keyboard.press("Enter")


# ---------------- helpers to clear overlays ----------------
async def clear_overlays(page: Page):
    # Cookie banner: “Dismiss” etc.
    try:
        await page.get_by_role("button", name=re.compile(r"(dismiss|accept|agree|got it|ok)", re.I)).first.click(timeout=1200)
    except Exception:
        pass
    # “Have an AAdvantage account?” promo close (X)
    try:
        await page.locator("button[aria-label*=close i]").first.click(timeout=800)
    except Exception:
        pass

    await accept_banners(page)  # your helper also tries common labels
    await wait_busy_clear(page)


# ---------------- optional prewarm ----------------
async def prewarm_and_open_search(page: Page):
    await page.goto("https://www.aa.com/", wait_until="domcontentloaded")
    await wait_akamai_clear(page)
    if await blocked(page): raise RuntimeError("blocked_home_1")
    await clear_overlays(page)
    await warm_up(page)
    await debug_step(page, "01_home")

    # Touch a couple of safe internal pages to build session entropy
    for url in [
        "https://www.aa.com/i18n/customer-service/support/contact-american/american-customer-service.jsp",
        "https://www.aa.com/i18n/travel-info/experience/dining/main-cabin.jsp",
    ]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=9000)
            await wait_akamai_clear(page)
            if await blocked(page): raise RuntimeError("blocked_mid")
            await human_pause(250, 500)
        except Exception:
            pass

    await page.goto("https://www.aa.com/", wait_until="domcontentloaded")
    await wait_akamai_clear(page)
    if await blocked(page): raise RuntimeError("blocked_home_2")
    await clear_overlays(page)
    try:
        await page.get_by_role("tab", name=re.compile("Flights|Book", re.I)).first.click(timeout=1500)
    except Exception:
        pass
    await human_pause(150, 300)
    await debug_step(page, "02_back_home_ready")


# ---------------- main flow ----------------
async def search_and_capture(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns:
      { "network_json": [ {url, json}, ... ], "page_html": "<html ...>" }
    """
    attempts, max_attempts = 0, 5
    last_html = ""

    while attempts < max_attempts:
        attempts += 1
        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                PROFILE_DIR,
                channel="chrome",
                headless=False,  # keep visible while stabilizing
                viewport={"width": 1366, "height": 900},
                locale=os.getenv("ACCEPT_LANGUAGE", "en-US,en;q=0.9"),
                timezone_id=os.getenv("TZ", "America/Los_Angeles"),
                proxy=proxy_from_env(attempts if ROTATE else 1),
            )
            await browser.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page = await browser.new_page()

            # Abort heavy resources to speed up interactivity
            async def route_filter(route):
                rtype = route.request.resource_type
                if rtype in ("image", "media", "font"):
                    try:
                        await route.abort()
                    except Exception:
                        pass
                else:
                    try:
                        await route.continue_()
                    except Exception:
                        pass
            await page.route("**/*", route_filter)

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
                # A) Either prewarm or go direct
                if PREWARM:
                    try:
                        await prewarm_and_open_search(page)
                    except Exception:
                        last_html = await page.content()
                        await browser.close()
                        await asyncio.sleep(0.9 + attempts * 0.6)
                        continue
                else:
                    await page.goto("https://www.aa.com/", wait_until="domcontentloaded")
                    await wait_akamai_clear(page)
                    if await blocked(page): raise RuntimeError("blocked_home_direct")
                    await clear_overlays(page)
                    await warm_up(page)
                    await debug_step(page, "01_home")

                # B) One-way FIRST (prevents return date logic from interfering)
                await wait_akamai_clear(page)
                try:
                    await force_one_way(page)  # robust triple-path setter + verify
                except Exception:
                    # Last resort: role or label
                    picked = False
                    for sel in [("radio", re.compile(r"one\s*way", re.I))]:
                        try:
                            await page.get_by_role(sel[0], name=sel[1]).check(timeout=1200)
                            picked = True
                            break
                        except Exception:
                            pass
                    if not picked:
                        try:
                            await page.locator("label[for='flightSearchForm.tripType.oneWay'], label:has-text('One way')").first.click(timeout=1200)
                        except Exception:
                            pass
                # Assert it stuck (fail early if not)
                assert await page.locator("#flightSearchForm\\.tripType\\.oneWay").is_checked(), "One-way did not toggle"
                await debug_step(page, "02_oneway")

                # C) Fill origin / destination
                await wait_akamai_clear(page)
                origin = page.locator(
                    "input[name='originAirport'], input#originAirport, input#reservationFlightSearchForm\\.originAirport"
                ).first
                await fill_airport(page, origin, params["origin"], city_hint="Los Angeles")
                await debug_step(page, "03_origin")

                await wait_akamai_clear(page)
                dest = page.locator(
                    "input[name='destinationAirport'], input#destinationAirport, input#reservationFlightSearchForm\\.destinationAirport"
                ).first
                await fill_airport(page, dest, params["destination"], city_hint="New York")
                await debug_step(page, "04_destination")

                # D) Set the depart date: try your normal helper, then fall back to no-click fast setter
                await wait_akamai_clear(page)
                try:
                    await select_depart_date(page, params["date"])
                except Exception:
                    await set_depart_date_quick(page, params["date"])

                # Quick assert that a MM/DD/YYYY value is present
                val = await page.locator("#aa-leavingOn, input[name='departDate']").first.input_value()
                if not re.match(r"^\d{2}/\d{2}/\d{4}$", val or ""):
                    raise RuntimeError(f"Depart date not set correctly (saw: {val!r})")
                await debug_step(page, "05_date_set")

                # E) Submit
                await wait_akamai_clear(page)
                await page.get_by_role("button", name=re.compile("search", re.I)).first.click()
                await page.wait_for_load_state("domcontentloaded")
                await debug_step(page, "06_after_submit")

                if await blocked(page):
                    last_html = await page.content()
                    await browser.close()
                    await asyncio.sleep(0.9 + attempts * 0.7)
                    continue

                # F) Results (or at least capture HTML)
                try:
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_selector(
                        "[data-test-id='resultsList'], [data-testid='resultsList'], [role='list']",
                        timeout=25000
                    )
                except PWTimeout:
                    pass

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

    if last_html:
        (OUT / "akamai_last.html").write_text(last_html, encoding="utf-8")
    raise RuntimeError("Blocked or failed after multiple attempts. Provide a stronger US residential proxy (sticky session) and retry.")
