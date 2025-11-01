import os, re, json, asyncio, pathlib, sys, base64
from typing import Any, Dict, Optional, List, Callable

from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
    TimeoutError as PWTimeout,
)

# -------- settings / env -------
OUT = pathlib.Path("data/debug"); OUT.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = os.getenv("AA_PROFILE_DIR", ".pw-user")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
ACCEPT_LANG = "en-US,en;q=0.9"
TZ = "America/Los_Angeles"

def proxy_from_env():
    p = os.getenv("AA_HTTP_PROXY") or os.getenv("HTTP_PROXY")
    return {"server": p} if p else None

# --- JS hooks to capture API calls ---
INJECT_HOOKS = r"""
(() => {
  const tag = (kind, url, body) => {
    try {
      const b64 = body ? btoa(unescape(encodeURIComponent(body))) : '';
      console.debug('AA_HOOK|' + kind + '|' + url + '|' + b64);
    } catch(e) {}
  };

  const origFetch = window.fetch;
  window.fetch = async function(input, init={}) {
    try {
      const url = (typeof input === 'string') ? input : (input?.url || '');
      const method = (init?.method || 'GET').toUpperCase();
      let body = '';
      if (typeof init?.body === 'string') body = init.body;
      else if (init?.body instanceof URLSearchParams) body = init.body.toString();
      if (method === 'POST') tag('fetch', url, body || '');
    } catch(e) {}
    return origFetch.apply(this, arguments);
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__aa_url = url; this.__aa_method = method;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body) {
    try {
      if ((this.__aa_method||'').toUpperCase() === 'POST') {
        let b = '';
        if (typeof body === 'string') b = body;
        else if (body instanceof URLSearchParams) b = body.toString();
        tag('xhr', this.__aa_url||'', b || '');
      }
    } catch(e) {}
    return origSend.apply(this, arguments);
  };
})();
"""

# -------- helpers --------------
def _dump(obj, name):
    try: (OUT / f"{name}.json").write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception: pass

def looks_like_flights(js):
    if not isinstance(js, dict): return False
    text = json.dumps(js).lower()
    return any(h in text for h in ["offer", "itineraries", "slices", "segments", "fares"])

def build_headers():
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.aa.com",
        "Referer": "https://www.aa.com/",
    }

def mmddyyyy(date_iso):
    y, m, d = date_iso.split("-")
    return f"{int(m):02d}/{int(d):02d}/{y}"

def _console_scrape(text, bucket):
    if not isinstance(text, str) or not text.startswith("AA_HOOK|"): return
    try:
        _, kind, url, b64 = text.split("|", 3)
        body = base64.b64decode(b64).decode("utf-8", "ignore") if b64 else ""
        bucket.append({"url": url, "body": body, "source": f"console-{kind}"})
    except: pass

def _looks_like_shopping(url, headers):
    u = (url or "").lower()
    return "aa.com" in u and any(s in u for s in ["/booking/api", "/shopping", "/bff/"])

# -------- wait helpers ----------
async def wait_not_busy(page, timeout = 4000):
    try:
        await page.wait_for_function("""() => {
            const busy = document.querySelector('.aa-busy-module, .aa-busy-bg, [class*="spinner"], [class*="loading"]');
            return !busy || getComputedStyle(busy).opacity === '0';
        }""", timeout=timeout)
    except:
        await asyncio.sleep(0.3)

async def safe_click(loc, page):
    await wait_not_busy(page)
    try:
        await loc.wait_for(state="visible", timeout=5000)
        await loc.scroll_into_view_if_needed()
        await asyncio.sleep(0.1)
        await loc.click(timeout=3000)
    except:
        try:
            await asyncio.sleep(0.3)
            await loc.click(force=True, timeout=2000)
        except:
            await page.evaluate("el => el.click()", await loc.element_handle())

# -------- launch -----
async def launch_context():
    p = await async_playwright().start()
    ctx = await p.chromium.launch_persistent_context(
        PROFILE_DIR,
        channel="chrome",
        headless=False,
        viewport={"width": 1366, "height": 900},
        user_agent=UA,
        locale=ACCEPT_LANG,
        timezone_id=TZ,
        proxy=proxy_from_env(),
        args=["--disable-blink-features=AutomationControlled"],
    )
    await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return p, ctx

# -------- step 0: load home ---
async def seed_home(ctx):
    page = await ctx.new_page()
    await page.add_init_script(INJECT_HOOKS)
    print("🌐 Loading AA.com...")
    await page.goto("https://www.aa.com/", wait_until="domcontentloaded")
    await asyncio.sleep(2)
    
    # Close popups
    for sel in ["button:has-text('Accept')", "button:has-text('Close')", "button[aria-label*='close' i]"]:
        try:
            await page.locator(sel).first.click(timeout=1000)
        except: pass
    
    try:
        await page.screenshot(path=str(OUT / "home.png"))
        (OUT / "home.html").write_text(await page.content(), encoding="utf-8")
        print("✓ Homepage loaded")
    except: pass
    return page

# -------- strategy A: direct API ----------
async def try_direct_api(ctx, params):
    print("\n🔬 Trying direct API...")
    o, d, dt = params["origin"], params["destination"], params["date"]
    
    urls = [
        "https://www.aa.com/booking/api/search",
        "https://www.aa.com/booking/api/1/shopping/flightSearch"
    ]
    
    for url in urls:
        for mode in ("cash", "award"):
            body = {
                "tripType": "ONE_WAY",
                "redeemMiles": (mode == "award"),
                "slices": [{"origin": o, "destination": d, "date": dt}],
                "passengers": {"adult": 1},
            }
            try:
                r = await ctx.request.post(url, headers=build_headers(), data=json.dumps(body))
                if r.ok:
                    js = await r.json()
                    if looks_like_flights(js):
                        print(f"✅ Direct API worked! ({mode})")
                        _dump(js, f"direct_{mode}")
                        return {"mode": mode, "json": js, "url": url}
            except: pass
    return None

# -------- strategy B: form interaction ----------
async def setup_oneway(page):
    print("📍 Setting one-way...")
    # Try radio button
    for radio_id in ["flightSearchForm.tripType.oneWay", "flightSearchForm.tripType.OneWay"]:
        try:
            radio = page.locator(f"input[id*='{radio_id}' i]").first
            if await radio.count():
                await safe_click(radio, page)
                await asyncio.sleep(0.2)
                return
        except: pass
    
    # Try label
    try:
        await safe_click(page.get_by_text("One way", exact=False).first, page)
        await asyncio.sleep(0.2)
    except: pass

async def fill_airport(page, field_name, code):
    print(f"✍ {field_name}: {code}")
    
    # Find input
    inp = None
    for sel in [f"input[name='{field_name}']", f"input[placeholder*='{field_name.split('Airport')[0]}' i]"]:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                inp = loc
                break
        except: continue
    
    if not inp:
        print(f"⚠ Field not found: {field_name}")
        return
    
    # Clear and type
    await safe_click(inp, page)
    await asyncio.sleep(0.2)
    await inp.fill("")
    await inp.type(code, delay=50)
    await asyncio.sleep(0.6)
    
    # Select from autocomplete
    try:
        await page.get_by_role("option", name=re.compile(rf"\b{re.escape(code)}\b", re.I)).first.click(timeout=1500)
        print(f"✓ Selected {code}")
    except:
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")

async def fill_date(page, date_iso):
    val = mmddyyyy(date_iso)
    print(f"📅 Date: {val}")
    
    # JS set
    await page.evaluate("""
    (val) => {
      document.querySelectorAll("input[name*='depart' i], input[placeholder*='date' i]").forEach(inp => {
        inp.value = val;
        ['input','change'].forEach(t => inp.dispatchEvent(new Event(t, {bubbles:true})));
      });
    }""", val)

async def discover_via_form(page, params):
    ctx = page.context
    candidates: List[dict] = []
    
    def on_req(req):
        try:
            if req.method.upper() == "POST" and _looks_like_shopping(req.url, req.headers):
                candidates.append({"url": req.url, "body": req.post_data or ""})
        except: pass
    ctx.on("request", on_req)
    page.on("console", lambda msg: _console_scrape(msg.text, candidates))
    
    print("\n📝 Filling form...")
    await setup_oneway(page)
    await fill_airport(page, "originAirport", params["origin"])
    await fill_airport(page, "destinationAirport", params["destination"])
    await fill_date(page, params["date"])
    await asyncio.sleep(0.3)
    
    # Submit
    print("🚀 Submitting...")
    await page.evaluate("""
      () => {
        const form = document.querySelector('form');
        if (form) {
          form.setAttribute('action', 'https://www.aa.com/booking/find-flights');
          form.submit();
        }
      }
    """)
    
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except:
        await asyncio.sleep(5)
    
    if not candidates:
        raise RuntimeError("No API calls captured")
    
    # Pick best
    def score(c):
        s = 0
        if "/booking/api/" in c["url"]: s += 2
        if c.get("body"): s += 1
        return -s
    
    candidates.sort(key=score)
    best = candidates[0]
    print(f"✓ Captured {len(candidates)} calls, using: {best['url'][:60]}...")
    
    for i, c in enumerate(candidates[:3]):
        (OUT / f"req_{i}.txt").write_text(f"{c['url']}\n\n{c.get('body','')}", encoding="utf-8")
    
    return best

# -------- master function ----
async def fetch_shopping_json(params):
    p, ctx = await launch_context()
    try:
        page = await seed_home(ctx)
        
        # Try direct first
        direct = await try_direct_api(ctx, params)
        if direct:
            return {"template": None, "result": direct}
        
        # Fall back to form
        template = await discover_via_form(page, params)
        
        # Replay with real params
        print("\n🔄 Replaying API call...")
        try:
            body_obj = json.loads(template["body"]) if template["body"] else {}
            # Patch body (simplified)
            if "slices" in body_obj and body_obj["slices"]:
                body_obj["slices"][0].update({
                    "origin": params["origin"],
                    "destination": params["destination"],
                    "date": params["date"]
                })
        except:
            body_obj = {}
        
        r = await ctx.request.post(template["url"], 
                                   headers=build_headers(), 
                                   data=json.dumps(body_obj))
        
        if not r.ok:
            raise RuntimeError(f"Replay failed: {r.status}")
        
        js = await r.json()
        if not looks_like_flights(js):
            _dump(js, "replay_bad")
            raise RuntimeError("Replay didn't return flights")
        
        _dump(js, "replay_success")
        print("✅ Replay successful!")
        
        return {
            "template": {"url": template["url"], "body": body_obj},
            "result": {"json": js, "url": template["url"]}
        }
        
    finally:
        await asyncio.sleep(2)  # Let you see the result
        try: await ctx.close()
        except: pass
        try: await p.stop()
        except: pass

# -------- CLI ----------
def _cli_parse(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", required=True)
    ap.add_argument("--destination", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--passengers", type=int, default=1)
    ap.add_argument("--cabin", default="ECONOMY")
    args = ap.parse_args(argv)
    return {
        "origin": args.origin.upper(),
        "destination": args.destination.upper(),
        "date": args.date,
        "passengers": args.passengers,
        "cabin": args.cabin,
    }

async def _main(argv):
    params = _cli_parse(argv)
    print(f"\n{'='*60}")
    print(f"AA Flight Scraper")
    print(f"{params['origin']} → {params['destination']} on {params['date']}")
    print(f"{'='*60}\n")
    
    res = await fetch_shopping_json(params)
    
    out = {
        "search_metadata": {
            "origin": params["origin"],
            "destination": params["destination"],
            "date": params["date"],
            "passengers": params["passengers"],
            "cabin_class": params["cabin"].lower(),
            "source": "aa.com",
        },
        "raw": res["result"]["json"],
        "template_used": res["template"],
    }
    
    (OUT / "crawler_output.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n✅ Output: {OUT}/crawler_output.json ({len(json.dumps(out))} bytes)")

if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))