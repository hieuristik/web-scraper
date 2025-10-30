# src/playwright_utils.py
import os, re, asyncio, random
from typing import Optional
from datetime import datetime
from playwright.async_api import TimeoutError as PWTimeout, Page

# ---------- constants shared across modules ----------
BUSY_SEL   = ".aa-busy-module, .aa-busy-bg, .aa-busy-text"
BLOCK_SIGS = ("akamai-challenge-resubmit=true", "access denied", "edgesuite")

# ---------- small utilities ----------
async def human_pause(min_ms=200, max_ms=700):
    await asyncio.sleep(random.uniform(min_ms/1000, max_ms/1000))

async def warm_up(page: Page):
    # gentle human-ish actions; wrap to avoid errors if page closes
    await page.wait_for_load_state("domcontentloaded")
    await human_pause(400, 900)
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
    """
    Wait until AA's 'Loading...' overlay is not intercepting clicks.
    We avoid raising; just poll and proceed to reduce flakiness.
    """
    deadline = page.context._loop.time() + (timeout_ms / 1000)
    overlay = page.locator(BUSY_SEL)
    while page.context._loop.time() < deadline:
        try:
            if not await overlay.first.is_visible():
                return
        except Exception:
            return
        await asyncio.sleep(0.15)

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

def proxy_from_env():
    """
    Read a gateway/unblocker proxy from env for portability.
    Accepts HTTP_PROXY / HTTPS_PROXY / PROXY.
    Returns Playwright's proxy dict or None.
    """
    p = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("PROXY")
    return {"server": p} if p else None

# --- date picker helpers ---
async def _open_calendar(page: Page):
    """Open the depart date calendar using multiple fallback selectors."""
    await wait_busy_clear(page)
    tried = [
        "button[aria-label*='Depart']",
        "button:has-text('Depart')",
        "input[name='departDate']",
        "input[id*='depart']",
        "[aria-controls*='depart']",
    ]
    for sel in tried:
        try:
            await page.locator(sel).first.click(timeout=1500)
            return True
        except PWTimeout:
            continue
    # final fallback: click the visible label text
    try:
        await page.get_by_text("Depart", exact=False).first.click(timeout=1500)
        return True
    except PWTimeout:
        return False

async def _calendar_showing_month(page: Page, month_name: str, year: int) -> bool:
    # AA usually renders month headers inside a dialog
    header = page.locator("div[role='dialog'] >> text=/" + re.escape(month_name) + r"\s+" + str(year) + "/i")
    return (await header.count()) > 0

async def _next_month(page: Page):
    # Try a few different "next" controls
    tried = [
        page.get_by_role("button", name=re.compile(r"next", re.I)).first,
        page.locator("button:has([aria-label*='Next'])").first,
        page.locator("[aria-label*='Next month']").first,
    ]
    for loc in tried:
        try:
            await loc.click(timeout=1200)
            return
        except PWTimeout:
            continue
    # tiny wiggle if all failed (calendar sometimes needs a focus nudge)
    await page.keyboard.press("Tab")

async def select_depart_date(page: Page, date_iso: str):
    """
    Select a specific depart date from AA's calendar.
    date_iso: 'YYYY-MM-DD'
    """
    # Parse the target date
    dep = datetime.fromisoformat(date_iso)
    month_name = dep.strftime("%B")           # e.g., 'December'
    year = dep.year
    day = dep.day

    # Open the calendar
    opened = await _open_calendar(page)
    if not opened:
        raise RuntimeError("Could not open date picker")

    # Navigate months until target month/year visible (cap: 24 clicks)
    for _ in range(24):
        if await _calendar_showing_month(page, month_name, year):
            break
        await _next_month(page)
        await wait_busy_clear(page)

    # Prefer aria-label like "December 15, 2025"
    aria_exact = f"{month_name} {day}, {year}"
    # Some builds use zero-padded day in aria-label (e.g., 'December 05, 2025')
    aria_zero = f"{month_name} {day:02d}, {year}"

    # Try aria buttons first
    for label in [aria_exact, aria_zero]:
        try:
            await page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first.click(timeout=1200)
            await wait_busy_clear(page)
            return
        except PWTimeout:
            pass

    # Fallback: buttons with just the day number inside the desired month grid
    try:
        # Narrow to the visible dialog/month then click the day cell
        dialog = page.locator("div[role='dialog']").first
        await dialog.get_by_role("button", name=re.compile(rf"^{day}$")).first.click(timeout=1500)
        await wait_busy_clear(page)
        return
    except PWTimeout:
        pass

    # Last resort: generic day cell patterns sometimes use [data-day] or [data-date]
    try:
        sel = f"[data-day='{day}'], [data-date*='-{day:02d}'], [aria-label*='{month_name}'][aria-label*='{day}'][aria-label*='{year}']"
        await page.locator(sel).first.click(timeout=1500)
        await wait_busy_clear(page)
        return
    except PWTimeout:
        raise RuntimeError(f"Could not select date {date_iso} in calendar")
