# src/playwright_utils.py
import os, re, asyncio, random
from datetime import datetime
from typing import Optional
from playwright.async_api import TimeoutError as PWTimeout, Page

BUSY_SEL   = ".aa-busy-module, .aa-busy-bg, .aa-busy-text"
BLOCK_SIGS = ("akamai-challenge-resubmit=true", "access denied", "edgesuite")
CALENDAR_DIALOG = "div[role='dialog'], [role='dialog']"

# ---------- small utilities ----------
async def human_pause(min_ms=200, max_ms=700):
    await asyncio.sleep(random.uniform(min_ms/1000, max_ms/1000))

async def warm_up(page):
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

async def accept_banners(page):
    for label in ["Accept", "I Agree", "Agree", "Got it", "OK"]:
        try:
            await page.get_by_role("button", name=re.compile(label, re.I)).click(timeout=1200)
            break
        except PWTimeout:
            pass

async def wait_busy_clear(page, timeout_ms=8000):
    deadline = page.context._loop.time() + (timeout_ms / 1000)
    overlay = page.locator(BUSY_SEL)
    while page.context._loop.time() < deadline:
        try:
            if not await overlay.first.is_visible():
                return
        except Exception:
            return
        await asyncio.sleep(0.15)

async def blocked(page):
    u = (page.url or "").lower()
    if any(sig in u for sig in BLOCK_SIGS):
        return True
    try:
        html = (await page.content()).lower()
        return any(sig in html for sig in BLOCK_SIGS)
    except Exception:
        return False

def proxy_from_env(index=0):
    """
    Optionally rotate proxies if the user provides a list.
    Example:
      export PROXIES="us1:port,us2:port,us3:port"
    """
    proxies = os.getenv("PROXIES")
    if proxies:
        lst = [p.strip() for p in proxies.split(",") if p.strip()]
        if lst:
            p = lst[index % len(lst)]
            return {"server": p}

    # fallback to single proxy envs
    p = (
        os.getenv("HTTP_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("PROXY")
        or os.getenv("http_proxy")
        or os.getenv("https_proxy")
        or os.getenv("proxy")
    )
    return {"server": p} if p else None

# ---------- trip mode ----------
async def ensure_one_way(page):
    """
    Force 'One way' so AA doesn't require a return date.
    Tries radios, buttons, labels, then verifies via checked/pressed state.
    """
    await wait_busy_clear(page)
    await human_pause(120, 300)

    # Make sure the Flights tab is active (some variants default to Hotels/Vacations)
    try:
        await page.get_by_role("tab", name=re.compile(r"\b(Flights|Book)\b", re.I)).first.click(timeout=1200)
    except Exception:
        pass

    # Candidate controls that can represent "One way"
    candidates = [
        page.get_by_role("radio",  name=re.compile(r"\b(one[\s\-]?way)\b", re.I)).first,
        page.get_by_role("button", name=re.compile(r"\b(one[\s\-]?way)\b", re.I)).first,
        page.get_by_label(re.compile(r"\b(one[\s\-]?way)\b", re.I)).first,
        page.get_by_text(re.compile(r"\b(one[\s\-]?way)\b", re.I)).first,
        page.locator("input[type='radio'][value*='one']").first,
        page.locator("input[type='radio'][id*='one']").first,
        page.locator("input[type='radio'][name*='one']").first,
        page.locator("[data-trip*='one']").first,
    ]

    clicked = False
    for loc in candidates:
        try:
            # prefer .check() for inputs, fallback to .click()
            tag = (await loc.evaluate("el => el.tagName.toLowerCase()")).strip().lower()
            if tag == "input":
                typ = await loc.evaluate("el => el.type")
                if typ and typ.lower() == "radio":
                    await loc.check(timeout=1200)
                else:
                    await loc.click(timeout=1200)
            else:
                await loc.click(timeout=1200)
            clicked = True
            break
        except Exception:
            continue

    await human_pause(120, 250)
    await wait_busy_clear(page)

    # Verify: try common checked/pressed indicators
    verified = False
    try:
        # radio checked?
        radio = page.get_by_role("radio", name=re.compile(r"\b(one[\s\-]?way)\b", re.I)).first
        if await radio.is_visible():
            try:
                if await radio.is_checked():
                    verified = True
            except Exception:
                pass
    except Exception:
        pass

    if not verified:
        # button with aria-pressed?
        try:
            btn = page.get_by_role("button", name=re.compile(r"\b(one[\s\-]?way)\b", re.I)).first
            if await btn.is_visible():
                aria = await btn.get_attribute("aria-pressed")
                if aria and aria.lower() in ("true", "mixed"):
                    verified = True
        except Exception:
            pass

    # Last-resort heuristic: return date input becomes disabled/hidden after one-way
    if not verified:
        try:
            ret_inp = page.locator("input[name*='return'], input[id*='return'], input[aria-label*='Return']").first
            if await ret_inp.count():
                # Expect not focusable or hidden
                disabled = await ret_inp.is_disabled()
                visible  = await ret_inp.is_visible()
                if disabled or not visible:
                    verified = True
        except Exception:
            pass

    if not (clicked or verified):
        # Do not hard fail; log/debug step is taken by caller
        pass

# ---------- date picking ----------
def _iso_to_mmddyyyy(date_iso):
    dt = datetime.fromisoformat(date_iso)
    return dt.strftime("%m/%d/%Y")

async def _open_depart_calendar(page):
    await wait_busy_clear(page)
    for sel in [
        "button[aria-label*='Depart']",
        "button:has-text('Depart')",
        "input[name='departDate']",
        "input[id*='depart']",
        "[aria-controls*='depart']",
    ]:
        try:
            await page.locator(sel).first.click(timeout=1500)
            break
        except PWTimeout:
            continue
    else:
        await page.get_by_text("Depart", exact=False).first.click(timeout=1800)

    try:
        await page.locator(CALENDAR_DIALOG).first.wait_for(state="visible", timeout=3000)
    except PWTimeout:
        pass
    await human_pause(200, 500)
    await wait_busy_clear(page)

async def _month_header_visible(scope, month_name, year):
    head = scope.locator(f"text=/{re.escape(month_name)}\\s+{year}/i")
    return (await head.count()) > 0

async def _calendar_next(page):
    for loc in [
        page.get_by_role("button", name=re.compile(r"\bnext\b", re.I)).first,
        page.locator("button:has([aria-label*='Next'])").first,
        page.locator("[aria-label*='Next month']").first,
    ]:
        try:
            await loc.click(timeout=1200)
            return
        except PWTimeout:
            continue
    await page.keyboard.press("Tab")

async def _try_click_calendar_cell(scope, month_name, year, day, date_iso):
    candidates = [
        f"button[aria-label='{month_name} {day}, {year}']",
        f"button[aria-label='{month_name} {day:02d}, {year}']",
        f"td[data-date='{date_iso}']",
        f"[data-date='{date_iso}']",
        f"[role='gridcell'] >> text=^{day}$",
        f"button:has-text('^{day}$')",
        f"[aria-label*='{month_name}'][aria-label*=' {day}'][aria-label*='{year}']",
    ]
    for sel in candidates:
        loc = scope.locator(sel).first
        try:
            await loc.scroll_into_view_if_needed(timeout=500)
        except Exception:
            pass
        try:
            await loc.click(timeout=1800)
            return True
        except PWTimeout:
            continue
    return False

def _depart_input_selectors():
    return [
        "input[name='departDate']",
        "input[id='departDate']",
        "input[id*='depart']",
        "[aria-label*='Depart'] input",
        "input[placeholder*='Depart']",
    ]

async def _type_depart_mmddyyyy(page, date_iso):
    mmdd = _iso_to_mmddyyyy(date_iso)
    for sel in _depart_input_selectors():
        loc = page.locator(sel).first
        try:
            await loc.click(timeout=1200)
        except PWTimeout:
            continue
        try:
            # clear
            try:
                await loc.fill("", timeout=800)
            except Exception:
                await loc.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")

            await loc.type(mmdd, delay=60)
            await page.keyboard.press("Enter")
            await human_pause(250, 500)
            await wait_busy_clear(page)

            # verify
            try:
                val = await loc.input_value(timeout=800)
                if val and (mmdd in val or val.strip() == mmdd):
                    return True
            except Exception:
                pass

            await page.keyboard.press("Tab")
            await human_pause(200, 400)
            await wait_busy_clear(page)
            try:
                val = await loc.input_value(timeout=800)
                if val and (mmdd in val or val.strip() == mmdd):
                    return True
            except Exception:
                pass
        except Exception:
            continue
    return False

async def _js_set_depart_value(page, date_iso):
    """
    Last-resort: set the value via JS and dispatch events (works with React-style handlers).
    """
    mmdd = _iso_to_mmddyyyy(date_iso)
    for sel in _depart_input_selectors():
        try:
            ok = await page.evaluate(
                """(payload) => {
                    const { selector, val } = payload;
                    const el = document.querySelector(selector);
                    if (!el) return false;
                    const desc = Object.getOwnPropertyDescriptor(el.__proto__, 'value')
                               || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                    if (desc && desc.set) {
                        desc.set.call(el, val);
                    } else {
                        el.value = val;
                    }
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.blur?.();
                    return true;
                }""",
                {"selector": sel, "val": mmdd}
            )
            if ok:
                await human_pause(180, 360)
                await wait_busy_clear(page)
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False

async def select_depart_date(page, date_iso):
    """
    Robust: ensure one-way (in flow), open calendar, try click → type → JS set.
    """
    # 1) Try calendar path
    await _open_depart_calendar(page)

    dialog = page.locator(CALENDAR_DIALOG).filter(has_text=re.compile(r".")).first
    scope = dialog if (await dialog.count()) else page

    target = datetime.fromisoformat(date_iso)
    month_name, year, day = target.strftime("%B"), target.year, target.day

    for _ in range(24):
        if await _month_header_visible(scope, month_name, year):
            break
        await _calendar_next(page)
        await human_pause(120, 250)
        await wait_busy_clear(page)

    if await _try_click_calendar_cell(scope, month_name, year, day, date_iso):
        await human_pause(200, 400)
        await wait_busy_clear(page)
        return

    # 2) Fallback: type into the input
    if await _type_depart_mmddyyyy(page, date_iso):
        return

    # 3) Last resort: JS set + events
    if await _js_set_depart_value(page, date_iso):
        return

    # Capture artifacts and fail
    try:
        html = await page.content()
        await page.screenshot(path="data/debug/calendar_fail.png", full_page=True)
        with open("data/debug/calendar_fail.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass
    raise RuntimeError(f"Could not set depart date {date_iso} via any method.")

# --- Akamai guard: call before any interactive step ---
async def wait_akamai_clear(page, timeout_ms=15000):
    """Waits until we're not on the akamai resubmit URL anymore."""
    if "akamai-challenge-resubmit=true" not in page.url:
        return
    target_deadline = page.context._loop.time() + (timeout_ms / 1000)
    # Wait for the bounce to finish (URL changes away from resubmit)
    while "akamai-challenge-resubmit=true" in page.url:
        await page.wait_for_load_state("domcontentloaded")
        if page.context._loop.time() > target_deadline:
            break
        await asyncio.sleep(0.25)
    await page.wait_for_load_state("domcontentloaded")

# --- Bulletproof One-way toggle ---
async def force_one_way(page):
    """
    Flip trip type to 'one way' using 3 layers:
      1) Click the label (reliably forwards the event in AA's UI)
      2) Set the radio to checked via JS + dispatch change/input
      3) Verify with isChecked(); if not, use .check(force=True)
    """
    await wait_akamai_clear(page)

    one_id = "flightSearchForm\\.tripType\\.oneWay"
    round_id = "flightSearchForm\\.tripType\\.roundTrip"

    # 1) Label click first (bypasses most overlay-z-index weirdness)
    try:
        await page.locator("label[for='flightSearchForm.tripType.oneWay']").click(timeout=1200, force=True)
    except Exception:
        pass

    # 2) Hard-set with JS and fire events so AA's widget reacts
    await page.evaluate(
        """() => {
            const one = document.querySelector("#flightSearchForm\\.tripType\\.oneWay");
            const rt  = document.querySelector("#flightSearchForm\\.tripType\\.roundTrip");
            if (one) {
              one.checked = true;
              one.dispatchEvent(new Event('input', {bubbles:true}));
              one.dispatchEvent(new Event('change', {bubbles:true}));
            }
            if (rt) { rt.checked = false; }
            // AA sometimes hides Return only after a change; do it proactively:
            const ret = document.querySelector("#aa-returningFrom");
            if (ret) { ret.value = ""; ret.setAttribute("disabled", "disabled"); }
        }"""
    )

    # 3) Verify; if it still didn’t take, force the radio API
    radio = page.locator(f"#{one_id}")
    try:
        if not await radio.is_checked():
            await radio.check(timeout=1200, force=True)
    except Exception:
        pass

    # Final assert to fail early if needed
    assert await page.locator(f"#{one_id}").is_checked(), "One-way did not toggle"

async def set_depart_date_quick(page, iso_yyyy_mm_dd):
    await wait_akamai_clear(page)

    y, m, d = iso_yyyy_mm_dd.split("-")
    mmddyyyy = f"{int(m):02d}/{int(d):02d}/{y}"

    # Set the value without clicking; then dispatch the events AA listens to.
    await page.evaluate(
        """(val) => {
            const dep = document.querySelector("#aa-leavingOn, input[name='departDate']");
            if (dep) {
                dep.value = val;
                dep.dispatchEvent(new Event('input', {bubbles:true}));
                dep.dispatchEvent(new Event('change', {bubbles:true}));
                dep.blur && dep.blur();
            }
        }""",
        mmddyyyy
    )