# src/playwright_flow.py
import os, re, asyncio, pathlib, random, string
from typing import Any, Dict, List, Optional
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Page

OUT = pathlib.Path("data/debug"); OUT.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = ".pw-user"

PREWARM = os.getenv("AA_PREWARM", "0").lower() in ("1","true","yes")
ROTATE  = os.getenv("AA_ROTATE",  "0").lower() in ("1","true","yes")
BLOCK_MEDIA_ON_HOME = False  # flip True after you’re stable

UA = os.getenv("AA_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)
NETWORK_KEEP = re.compile(r"(availability|shopping|offers?|price|itinerary|calendar|miles|fare)", re.I)

# ---------------- proxy helper (simple env variant) ----------------
def proxy_from_env(idx: int):
    p = os.getenv("AA_HTTP_PROXY") or os.getenv("HTTP_PROXY")
    return {"server": p} if p else None

# ---------------- debug utils ----------------
async def debug_step(page: Page, name: str):
    try:
        (OUT / f"{name}.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    except Exception:
        pass

async def wait_akamai_clear(page: Page):
    await page.wait_for_load_state("domcontentloaded")
    # title check is faster than full HTML for early deny
    try:
        if "Access Denied" in (await page.title()):
            raise RuntimeError("akamai_denied_title")
    except Exception:
        pass

async def accept_banners(page: Page):
    sels = [
        "button:has-text('Accept')", "button:has-text('Agree')", "button:has-text('Got it')",
        "button[aria-label*='dismiss' i]", "button[aria-label*='close' i]"
    ]
    for s in sels:
        try: await page.locator(s).first.click(timeout=800)
        except Exception: pass

async def blocked(page: Page) -> bool:
    try:
        html = (await page.content()).lower()
        return ("access denied" in html) and ("edgesuite.net" in html or "akamai" in html)
    except Exception:
        return False

def _rand(n=6):
    return "".join(random.choice(string.ascii_lowercase+string.digits) for _ in range(n))

# ---------------- panel/form anchoring ----------------
async def ensure_book_flights_panel(page: Page) -> None:
    """Make absolutely sure 'Book → Flights' is the visible tabpanel."""
    await wait_akamai_clear(page)

    # 1) Click Book tab if present
    for sel in (
        "button[role='tab']:has-text('Book')",
        "a[role='tab']:has-text('Book')",
        "[data-aa-tab='book']",
        "nav [role='tablist'] >> text=Book",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                await loc.click(timeout=900)
                break
        except Exception:
            pass

    # 2) Click Flights sub-tab if present
    for sel in (
        "button[role='tab']:has-text('Flight')",
        "a[role='tab']:has-text('Flight')",
        "li[role='presentation'] a:has-text('Flight')",
        "li[role='tab'] a:has-text('Flight')",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                await loc.click(timeout=900)
                break
        except Exception:
            pass

    # 3) Hide/disable other panels that steal focus (e.g., Flight Status)
    try:
        await page.evaluate("""
        () => {
          // ensure booking form's panel is visible
          const form = document.querySelector('form#reservationFlightSearchForm') ||
                       document.querySelector('form[action*="find-flights"]');
          if (form) {
            const host = form.closest('[role="tabpanel"]');
            if (host) {
              host.removeAttribute('aria-hidden');
              host.style.display='block';
              host.style.visibility='visible';
            }
          }
          // positively hide flight status panel
          document.querySelectorAll('[role="tabpanel"]').forEach(p=>{
            const t = (p.textContent||'').toLowerCase();
            if (t.includes('flight status') || t.includes('status')) {
              p.setAttribute('aria-hidden','true');
              p.style.display='none';
              p.style.visibility='hidden';
            }
          });
        }
        """)
    except Exception:
        pass

async def get_booking_form_selector(page: Page) -> str:
    """Return CSS selector for the visible booking form that has originAirport."""
    await wait_akamai_clear(page)
    sel = await page.evaluate("""
    () => {
      const forms = Array.from(document.querySelectorAll('form'));
      for (const f of forms) {
        if (f.querySelector("input[name='originAirport']")) {
          const p = f.closest('[role="tabpanel"]');
          if (p) { p.removeAttribute('aria-hidden'); p.style.display='block'; p.style.visibility='visible'; }
          if (f.id) return `form#${f.id}`;
          const i = forms.indexOf(f);
          return i >= 0 ? `form:nth-of-type(${i+1})` : 'form';
        }
      }
      return '';
    }
    """)
    if not sel:
        raise RuntimeError("Booking form not found")
    return sel

# ---------------- One-way enforcer (scoped) ----------------
async def force_one_way_hard(page: Page, form_sel: str):
    """Robustly toggle one-way and verify via radios + hidden mirrors + inferred return disabled."""
    await wait_akamai_clear(page)

    # a) Try direct radio/label clicks if present
    try:
        radio = page.locator(f"{form_sel} input#flightSearchForm\\.tripType\\.oneWay")
        if await radio.count():
            await radio.first.check(timeout=900)
    except Exception:
        pass
    try:
        lab = page.locator(f"{form_sel} label[for='flightSearchForm.tripType.oneWay']")
        if await lab.count():
            await lab.first.click(timeout=900)
    except Exception:
        pass

    # b) Also flip all possible mirrors (ids/names/data attrs)
    await page.evaluate("""
    (formSel) => {
      const root = document.querySelector(formSel); if (!root) return;
      const fire = el => ['input','change','click'].forEach(t=>el.dispatchEvent(new Event(t,{bubbles:true})));
      const setChecked = el => { try { el.checked = true; fire(el);} catch(e){} };

      // radios by id/name/text
      root.querySelectorAll("input[type='radio']").forEach(r=>{
        const name = (r.name||'').toLowerCase();
        const id   = (r.id||'').toLowerCase();
        const val  = (r.value||'').toLowerCase();
        if (name.includes('triptype') || id.includes('triptype') || val.includes('one')) {
          if (id.includes('oneway') || val.includes('one')) setChecked(r);
        }
      });

      // hidden <input> mirrors
      root.querySelectorAll("input[type='hidden']").forEach(h=>{
        const n=(h.name||'').toLowerCase();
        if (n.includes('triptype')) { try { h.value = 'oneWay'; fire(h);} catch(e){} }
      });

      // AA sometimes uses data attributes on the form
      try { root.setAttribute('data-triptype','oneWay'); } catch(e){}
      try { root.dataset.triptype = 'oneWay'; } catch(e){}

      // disable/clear return date mirrors so validation doesn't flip trip type back
      const clear = el => { try { el.value=''; fire(el);} catch(e){} };
      root.querySelectorAll("input[name*='return' i], input[name*='back' i], input[name*='returnDate' i]").forEach(el=>{
        clear(el);
        try { el.setAttribute('disabled','disabled'); } catch(e){}
      });
    }
    """, form_sel)

    # c) Verify by querying back the state
    ok = await page.evaluate("""
    (formSel) => {
      const root = document.querySelector(formSel); if (!root) return false;
      // radio confirmed
      const radio = root.querySelector("input#flightSearchForm\\.tripType\\.oneWay");
      if (radio && radio.checked) return true;

      // hidden mirror or data attr
      const hid = Array.from(root.querySelectorAll("input[type='hidden']")).some(h=>{
        const n = (h.name||'').toLowerCase();
        return n.includes('triptype') && (h.value||'').toLowerCase().includes('one');
      });
      if (hid) return true;

      const dt = (root.getAttribute('data-triptype')||root.dataset?.triptype||'').toLowerCase();
      return dt.includes('one');
    }
    """, form_sel)
    if not ok:
        raise RuntimeError("One-way did not toggle")

# ---------------- airport fill (scoped to form) ----------------
async def fill_airport(page: Page, form_sel: str, name_attr: str, code: str):
    await wait_akamai_clear(page)
    inp = page.locator(f"{form_sel} input[name='{name_attr}']").first
    await inp.wait_for(state="visible", timeout=6000)
    try: await inp.fill("")
    except Exception: pass
    await inp.type(code, delay=25)

    # Prefer ARIA options; fallback to jQuery UI
    try:
        opt = page.get_by_role("option", name=re.compile(rf"\b{re.escape(code)}\b", re.I)).first
        await opt.click(timeout=1200); return
    except Exception:
        pass
    try:
        dd = page.locator("ul.ui-autocomplete li a", has_text=re.compile(rf"\b{re.escape(code)}\b", re.I)).first
        await dd.click(timeout=1200); return
    except Exception:
        pass

    # Minimal keyboard nudge
    try:
        await inp.type(" ")
        await page.keyboard.press("Backspace")
        await page.keyboard.press("Enter")
    except Exception:
        pass

# ---------------- date set (scoped to form) ----------------
async def set_depart_date(page: Page, form_sel: str, date_iso: str):
    y, m, d = date_iso.split("-")
    mmddyyyy = f"{int(m):02d}/{int(d):02d}/{y}"

    # 1) Try direct input (if visible)
    date_inp = page.locator(
        f"{form_sel} input[name='departDate'], "
        f"{form_sel} input#aa-leavingOn, "
        f"{form_sel} input[placeholder*='mm/dd' i]"
    ).filter(has_not=page.locator("[type='hidden']")).first

    try:
        await date_inp.wait_for(state="visible", timeout=5000)
        await date_inp.click()
        await date_inp.fill(mmddyyyy)
        await page.keyboard.press("Tab")
    except Exception:
        pass

    # 2) Fill all mirrors and fire events
    await page.evaluate("""
    ({formSel, val}) => {
      const root = document.querySelector(formSel); if (!root) return;
      const fire = el => ['input','change','blur'].forEach(t=>el.dispatchEvent(new Event(t,{bubbles:true})));
      const sels = [
        "input[name='departDate']",
        "input#aa-leavingOn",
        "input[name*='leave' i]",
        "input[placeholder*='mm/dd' i]"
      ];
      const seen = new Set();
      for (const s of sels) {
        root.querySelectorAll(s).forEach(inp=>{
          if (!inp || seen.has(inp)) return;
          try { inp.value = val; fire(inp); } catch(e) {}
          seen.add(inp);
        });
      }
      // hidden mirrors
      root.querySelectorAll("input[type='hidden']").forEach(h=>{
        const n=(h.name||'').toLowerCase();
        if (n.includes('depart')||n.includes('leave')) { try { h.value=val; fire(h);}catch(e){} }
      });
    }
    """, {"formSel": form_sel, "val": mmddyyyy})

    # 3) Verify strictly inside the form
    ok = await page.evaluate("""
    (formSel) => {
      const r = document.querySelector(formSel);
      if (!r) return false;
      const re=/^\\d{2}\\/\\d{2}\\/\\d{4}$/;
      return Array.from(r.querySelectorAll('input')).some(i=>re.test(i.value||''));
    }
    """, form_sel)
    if not ok:
        raise RuntimeError("Depart date not set correctly")

# ---------------- JSON capture ----------------
async def _capture_json(resp, bucket: List[Dict[str, Any]]):
    try:
        url = resp.url
        if not NETWORK_KEEP.search(url): return
        ct = (resp.headers.get("content-type") or "").lower()
        if "json" not in ct: return
        js = await resp.json()
        bucket.append({"url": url, "json": js})
    except Exception:
        pass

# ---------------- prewarm (optional) ----------------
async def prewarm(page: Page):
    try:
        await page.goto("https://www.aa.com/i18n/customer-service/support/contact-american/american-customer-service.jsp",
                        wait_until="domcontentloaded", timeout=9000)
        await wait_akamai_clear(page)
        await asyncio.sleep(0.2)
    except Exception:
        pass
    try:
        await page.goto("https://www.aa.com/i18n/travel-info/experience/dining/main-cabin.jsp",
                        wait_until="domcontentloaded", timeout=9000)
        await wait_akamai_clear(page)
        await asyncio.sleep(0.2)
    except Exception:
        pass

# ---------------- main ----------------
async def search_and_capture(params: Dict[str, Any]) -> Dict[str, Any]:
    attempts, max_attempts = 0, 4
    last_html = ""
    while attempts < max_attempts:
        attempts += 1
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                PROFILE_DIR,
                channel="chrome",
                headless=False,
                viewport={"width": 1366, "height": 900},
                user_agent=UA,
                locale="en-US,en;q=0.9",
                timezone_id="America/Los_Angeles",
                proxy=proxy_from_env(attempts if ROTATE else 1),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=Translate",
                    "--no-default-browser-check",
                    "--no-first-run",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
            )
            await ctx.set_extra_http_headers({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.aa.com/",
                "Upgrade-Insecure-Requests": "1",
                "sec-ch-ua": '"Chromium";v="127", "Not=A?Brand";v="24", "Google Chrome";v="127"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
            })
            await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page = await ctx.new_page()

            # optional resource slimming
            if BLOCK_MEDIA_ON_HOME:
                async def route_filter(route):
                    try:
                        if route.request.resource_type in ("image","media","font"):
                            return await route.abort()
                    except Exception:
                        pass
                    try: await route.continue_()
                    except Exception: pass
                await page.route("**/*", route_filter)

            # capture JSON
            captured: List[Dict[str, Any]] = []
            page.on("response", lambda r: asyncio.create_task(_capture_json(r, captured)))

            try:
                # ---- Home ----
                await page.goto("https://www.aa.com/", wait_until="domcontentloaded")
                await wait_akamai_clear(page)
                await accept_banners(page)
                await debug_step(page, "01_home")

                if PREWARM:
                    await prewarm(page)
                    await page.goto("https://www.aa.com/", wait_until="domcontentloaded")
                    await wait_akamai_clear(page)

                # Anchor booking panel and select form
                await ensure_book_flights_panel(page)
                form_sel = await get_booking_form_selector(page)

                # One-way
                await force_one_way_hard(page, form_sel)
                await debug_step(page, "02_oneway")

                # Airports
                await fill_airport(page, form_sel, "originAirport", params["origin"])
                await debug_step(page, "03_origin")
                await fill_airport(page, form_sel, "destinationAirport", params["destination"])
                await debug_step(page, "04_destination")

                # Date
                await set_depart_date(page, form_sel, params["date"])
                await debug_step(page, "05_date_set")

                # Submit to HTTPS
                await wait_akamai_clear(page)
                await page.evaluate("""
                (formSel) => {
                  const f = document.querySelector(formSel);
                  if (!f) throw new Error('search form not found');
                  f.setAttribute('action','https://www.aa.com/booking/find-flights');
                  f.submit();
                }
                """, form_sel)

                await page.wait_for_load_state("domcontentloaded")
                await debug_step(page, "06_after_submit")

                if await blocked(page):
                    last_html = await page.content()
                    await ctx.close()
                    await asyncio.sleep(0.9 + attempts*0.5)
                    continue

                # Results shell (best effort)
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
                await ctx.close()
                return {"network_json": captured, "page_html": html}

            except Exception:
                try:
                    last_html = await page.content()
                    (OUT / "last.html").write_text(last_html, encoding="utf-8")
                    await page.screenshot(path=str(OUT / "last.png"), full_page=True)
                except Exception:
                    pass
                await ctx.close()
                raise

    if last_html:
        (OUT / "akamai_last.html").write_text(last_html, encoding="utf-8")
    raise RuntimeError("Blocked or failed after multiple attempts. Use a sticky US residential proxy and retry.")
