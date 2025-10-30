import os, re, asyncio, random
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Page, Locator

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

async def blocked(page):
    """Detect Akamai challenge or Access Denied early."""
    u = (page.url or "").lower()
    if any(sig in u for sig in BLOCK_SIGS):
        return True
    try:
        html = (await page.content()).lower()
        return any(sig in html for sig in BLOCK_SIGS)
    except Exception:
        return False

def proxy_from_env():
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
