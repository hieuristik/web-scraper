#!/usr/bin/env python3
# src/crawler_api.py
# Improved, robust AA scraper:
# - Broader result-detection (both modern app-slice-details and legacy containers)
# - Fallback extraction using text-fragment parsing (handles structural differences between cash & award pages)
# - Aggressive lazy-load coaxing (scrolling, clicking "show more", human-like small delays)
# - Better award-pass handling so it won't stall indefinitely and will retry with extra interactions
#
# Usage same as before:
# python -m src.crawler_api --origin LAX --destination JFK --date 2025-12-15

import os, re, json, time, sys, pathlib, types, subprocess
from typing import Any, Dict, List, Optional

OUT = pathlib.Path("data/debug")
OUT.mkdir(parents=True, exist_ok=True)

def _dump(x, name):
    try:
        (OUT/f"{name}.json").write_text(json.dumps(x, indent=2, ensure_ascii=False), encoding="utf-8")
    except:
        pass

def _save_html(driver, name):
    try:
        (OUT/f"{name}.html").write_text(driver.page_source, encoding="utf-8")
    except:
        pass

# ---- Py3.12 distutils shim ----
try:
    import distutils
except ModuleNotFoundError:
    from packaging.version import parse as _parse
    M = types.ModuleType("distutils")
    Vm = types.ModuleType("distutils.version")
    class LooseVersion:
        def __init__(s,v): s.v=_parse(str(v))
        def _cmp(s,o,op): ov=o.v if isinstance(o,LooseVersion) else _parse(str(o)); return op(s.v, ov)
        def __lt__(s,o): return s._cmp(o, lambda a,b:a<b)
        def __le__(s,o): return s._cmp(o, lambda a,b:a<=b)
        def __gt__(s,o): return s._cmp(o, lambda a,b:a>b)
        def __ge__(s,o): return s._cmp(o, lambda a,b:a>=b)
        def __eq__(s,o): return s._cmp(o, lambda a,b:a==b)
        def __ne__(s,o): return s._cmp(o, lambda a,b:a!=b)
    Vm.LooseVersion = LooseVersion
    sys.modules["distutils"]=M
    sys.modules["distutils.version"]=Vm

# ---- deps ----
try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.common.keys import Keys
    from selenium.common.exceptions import TimeoutException
except Exception as e:
    raise RuntimeError(f"Missing selenium / undetected_chromedriver: {e}")

# optional: BeautifulSoup for DOM fallback parsing
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None  # we'll handle absence gracefully and fall back to regex parsing

# Regex helpers used for fragment parsing
FLIGHT_RE = re.compile(r'\b([A-Z]{2}\s?\d{1,4})\b')
TIME_RE = re.compile(r'(\d{1,2}:\d{2}\s*(?:AM|PM))', re.IGNORECASE)
PRICE_RE = re.compile(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)')
MILES_K_RE = re.compile(r'(\d+(?:\.\d+)?)\s*K\b', re.IGNORECASE)      # 12.5K
MILES_COMMA_RE = re.compile(r'(\d{1,3}(?:,\d{3})+)\s*(?:mile|miles|point|points)?', re.IGNORECASE)
MILES_SIMPLE_RE = re.compile(r'\b(\d{3,6})\b(?=\s*(?:mile|miles|point|points))', re.IGNORECASE)

def mmddyyyy(date_iso: str) -> str:
    y, m, d = date_iso.split("-")
    return f"{int(m):02d}/{int(d):02d}/{y}"

def calculate_cpp(cash: float, taxes: float, points: int) -> float:
    return 0.0 if not points else round(((cash - taxes)/points)*100, 2)

def merge_and_dedup(cash: List[Dict], award: List[Dict]) -> List[Dict]:
    """Match cash and award flights by flight number and departure time"""
    merged = []
    used_award = set()

    for c in cash:
        best_match = None
        best_idx = None

        for idx, a in enumerate(award):
            if idx in used_award:
                continue

            if (c.get("flight_number") == a.get("flight_number") and
                c.get("departure_time") == a.get("departure_time")):
                best_match = a
                best_idx = idx
                break

        if best_match:
            item = {
                "flight_number": c["flight_number"],
                "departure_time": c["departure_time"],
                "arrival_time": c["arrival_time"],
                "cash_price_usd": float(c["cash_price_usd"]),
                "taxes_fees_usd": float(c.get("taxes_fees_usd", 5.60)),
                "points_required": int(best_match["points_required"]),
            }
            item["cpp"] = calculate_cpp(
                item["cash_price_usd"],
                item["taxes_fees_usd"],
                item["points_required"]
            )
            merged.append(item)
            used_award.add(best_idx)

    # Deduplicate
    seen = set()
    unique = []
    for m in merged:
        key = (m["flight_number"], m["departure_time"], m["cash_price_usd"], m["points_required"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    return unique

# Helper to normalize amounts like "12.5K" to integer points (12500)
def amount_to_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.strip().upper().replace(',', '')
    s = re.sub(r'^[^\d\.\-]+', '', s)
    m = re.match(r'^([\d\.]+)K$', s)
    if m:
        try:
            val = float(m.group(1))
            return int(round(val * 1000))
        except:
            return None
    m2 = re.match(r'^(\d+)$', s)
    if m2:
        try:
            return int(m2.group(1))
        except:
            return None
    digits = re.findall(r'[\d\.]+', s)
    if digits:
        try:
            return int(float(digits[0]))
        except:
            return None
    return None

class AAScraper:
    def __init__(self):
        self.driver = None

    def setup(self):
        print("🚀 Launching Chrome...")
        opts = uc.ChromeOptions()
        opts.add_argument('--disable-blink-features=AutomationControlled')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument("--window-size=1400,1000")
        # helpful: set language to US to match site text rendering
        opts.add_argument("--lang=en-US,en")
        try:
            self.driver = uc.Chrome(options=opts)
            print("✓ Chrome ready")
        except Exception as e:
            raise RuntimeError(f"Failed to start Chrome: {e}")

    def _dismiss_popups(self):
        """Close cookie banners and obvious overlays"""
        try:
            self.driver.execute_script("""
                document.querySelectorAll('#onetrust-accept-btn-handler, button[aria-label*="close" i], button[title*="Close" i]').forEach(el => {
                    try { el.click(); } catch(e) {}
                });
                // remove any persistent overlays
                document.querySelectorAll('.onetrust-pc-dark-filter, .onetrust-banner-sdk, adc-cookie-banner').forEach(n=>{ try{ n.remove() }catch(e){}});
            """)
            time.sleep(0.4)
        except:
            pass

    def open_home(self):
        print("🌐 Loading AA.com...")
        self.driver.get("https://www.aa.com/")
        # small sleep + dismiss to allow initial JS
        time.sleep(3.0)
        self._dismiss_popups()
        _save_html(self.driver, "home")
        print("✓ Homepage loaded")

    def fill_search_form(self, origin: str, dest: str, date: str, redeem_miles: bool):
        """Fill the search form with exact selectors and human-like typing"""
        print(f"📝 Filling form ({'Award' if redeem_miles else 'Cash'} mode)...")
        self._dismiss_popups()
        time.sleep(0.3)

        # One-way: prefer clicking the label
        try:
            self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.oneWay']").click()
            time.sleep(0.22)
            print("✓ One-way selected (via label)")
        except Exception:
            try:
                ow = self.driver.find_element(By.ID, "flightSearchForm.tripType.oneWay")
                self.driver.execute_script("arguments[0].click()", ow)
                time.sleep(0.22)
                print("✓ One-way selected (via JS)")
            except Exception as e:
                print(f"  ⚠ One-way selection failed: {e}")

        # Redeem miles toggle
        try:
            # Attempt to locate the checkbox input first
            cb = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
            checked = cb.is_selected()
            if redeem_miles and not checked:
                # click label if available
                try:
                    lbl = self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.redeemMiles']")
                    lbl.click()
                except:
                    self.driver.execute_script("arguments[0].click()", cb)
                time.sleep(0.24)
            if not redeem_miles and checked:
                try:
                    lbl = self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.redeemMiles']")
                    lbl.click()
                except:
                    self.driver.execute_script("arguments[0].click()", cb)
                time.sleep(0.24)
            print(f"✓ Redeem miles {'enabled' if redeem_miles else 'disabled'} (attempted)")
        except Exception:
            # fallback: try to click any label that looks like redeem/miles
            try:
                lbls = self.driver.find_elements(By.XPATH, "//label[contains(., 'Redeem') or contains(., 'Miles') or contains(., 'redeem') or contains(., 'miles')]")
                for l in lbls:
                    try:
                        l.click()
                        time.sleep(0.2)
                        break
                    except:
                        continue
                print("✓ Redeem miles toggle attempted (label fallback)")
            except:
                print("  ⚠ Redeem miles toggle not found")

        # Origin
        try:
            origin_input = self.driver.find_element(By.NAME, "originAirport")
            origin_input.clear()
            time.sleep(0.12)
            for ch in origin:
                origin_input.send_keys(ch)
                time.sleep(0.03)
            time.sleep(0.8)
            origin_input.send_keys(Keys.TAB)
            time.sleep(0.4)
            print(f"✓ Origin: {origin}")
        except Exception as e:
            print(f"  ⚠ Origin input failed: {e}")

        # Destination
        try:
            dest_input = self.driver.find_element(By.NAME, "destinationAirport")
            dest_input.clear()
            time.sleep(0.12)
            for ch in dest:
                dest_input.send_keys(ch)
                time.sleep(0.03)
            time.sleep(0.8)
            dest_input.send_keys(Keys.TAB)
            time.sleep(0.4)
            print(f"✓ Destination: {dest}")
        except Exception as e:
            print(f"  ⚠ Destination input failed: {e}")

        # Date - set via JS to avoid datepicker issues
        try:
            date_val = mmddyyyy(date)
            self.driver.execute_script("""
                const val = arguments[0];
                document.querySelectorAll("input[name*='depart' i]").forEach(inp=>{
                    inp.value = val;
                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                });
            """, date_val)
            time.sleep(0.4)
            print(f"✓ Date: {date_val}")
        except Exception as e:
            print(f"  ⚠ Date input failed: {e}")

    def submit_search(self):
        """Submit the search with robust fallbacks"""
        self._dismiss_popups()
        try:
            btn = self.driver.find_element(By.XPATH, "//button[contains(., 'Search') or @type='submit']")
            btn.click()
            print("✓ Search submitted")
        except Exception:
            try:
                # fallback: press Enter on destination box
                dest = self.driver.find_element(By.NAME, "destinationAirport")
                dest.send_keys(Keys.ENTER)
                print("↩ Search submitted (Enter key)")
            except Exception as e:
                print(f"  ⚠ Submit failed: {e}")
        time.sleep(1.5)

    def _coax_lazy_load(self):
        """Try a set of actions to coax lazy-load of results (scrolling, clicking 'show more' etc.)"""
        try:
            # gentle scrolling pattern
            for _ in range(3):
                self.driver.execute_script("window.scrollBy(0, window.innerHeight/3);")
                time.sleep(0.35)
            # click 'Show more' or similar buttons
            try:
                more_buttons = self.driver.find_elements(By.XPATH, "//button[contains(., 'Show more') or contains(., 'More results') or contains(., 'Load more')]")
                for b in more_buttons:
                    try:
                        if b.is_displayed():
                            self.driver.execute_script("arguments[0].click()", b)
                            time.sleep(0.6)
                    except:
                        continue
            except:
                pass
            # small movement to trigger intersection observers
            try:
                self.driver.execute_script("document.querySelectorAll('div[class*=\"results\"], main, div[role=\"main\"]').forEach(el=>el.scrollTo(0, el.scrollHeight));")
            except:
                pass
        except Exception:
            pass

    def wait_for_results(self, timeout=60, is_award: bool = False, expected_min=10) -> bool:
        """
        Improved wait logic:
         - looks for modern 'app-slice-details' or legacy container counts
         - falls back to regex-based counts on page_source for times/flight numbers/miles/price
         - performs lazy-load coaxing periodically
        """
        print("⏳ Waiting for results...")
        start = time.time()
        last_count = 0
        while time.time() - start < timeout:
            try:
                # quick JS counts for modern/legacy containers
                counts = self.driver.execute_script("""
                    const modern = document.querySelectorAll('app-slice-details').length;
                    const legacy = document.querySelectorAll('div[class*="result"], div[class*="slice"], div[class*="flight"], li[role="option"], div[class*="offer"]').length;
                    return {modern: modern, legacy: legacy};
                """)
                modern = int(counts.get("modern", 0))
                legacy = int(counts.get("legacy", 0))
            except Exception:
                modern = 0
                legacy = 0

            # If site uses modern slices and we see enough elements -> done
            if modern >= expected_min or legacy >= expected_min:
                total = max(modern, legacy)
                print(f"✓ Found {total} flight elements (dom detection)")
                # wait a short moment for prices/miles to stabilize
                time.sleep(2.0)
                return True

            # Otherwise, use page_source heuristics (times or miles/price presence)
            try:
                src = self.driver.page_source or ""
            except Exception:
                src = ""

            # time occurrences (each flight has two times usually; require at least expected_min*2 matches)
            times_found = len(TIME_RE.findall(src))
            # flight number occurrences
            flights_found = len(FLIGHT_RE.findall(src))
            # price / miles indicators
            price_found = len(PRICE_RE.findall(src))
            miles_found = len(MILES_K_RE.findall(src)) + len(MILES_COMMA_RE.findall(src)) + len(MILES_SIMPLE_RE.findall(src))

            # heuristics: if we have many time matches or flight matches + price/miles hints -> results likely present
            # For award pages we expect miles to be present more often; for cash pages prices.
            if is_award:
                # treat a flight as present if we find at least 1 flight number and either times or miles
                if flights_found >= expected_min and (times_found >= expected_min or miles_found >= expected_min):
                    print(f"✓ Found {flights_found} candidate flight entries (text heuristics)")
                    time.sleep(1.5)
                    return True
            else:
                # cash: prefer price presence
                if flights_found >= expected_min and (times_found >= expected_min or price_found >= expected_min):
                    print(f"✓ Found {flights_found} candidate flight entries (text heuristics)")
                    time.sleep(1.5)
                    return True

            # If counts are increasing, update last_count and continue; else coax lazy load
            current_count = max(modern, legacy, flights_found)
            if current_count > last_count:
                last_count = current_count
            else:
                # coax lazy loading occasionally
                self._coax_lazy_load()

                # try clicking possible award toggles (for award pass)
                if is_award:
                    try:
                        toggles = self.driver.find_elements(By.XPATH, "//button[contains(translate(., 'MILES', 'miles'),'miles') or contains(., 'Redeem') or contains(., 'Award') or contains(., 'Show award')]")
                        for t in toggles:
                            try:
                                if t.is_displayed():
                                    self.driver.execute_script("arguments[0].click()", t)
                                    time.sleep(0.4)
                                    break
                            except:
                                continue
                    except:
                        pass

            time.sleep(1.0)

        print("⚠ Timeout waiting for results")
        return False

    # -------- DOM-structured parsing fallback ----------
    def _parse_dom(self, is_award: bool) -> List[Dict]:
        """
        Use BeautifulSoup to extract structured info when available:
        - For awards: look for per-pax amounts, flight-details blocks, and flight-number spans
        - For cash: look for price/fare spans and route/time blocks
        Returns a list of dicts similar to parse_flights() output with keys price/miles as applicable.
        """
        if BeautifulSoup is None:
            return []

        src = ""
        try:
            src = self.driver.page_source or ""
        except Exception:
            return []

        soup = BeautifulSoup(src, "html.parser")
        results = []
        seen = set()

        # Modern flight detail blocks
        flight_blocks = soup.find_all(id=re.compile(r'^flight-details-\d+$'))
        for fb in flight_blocks:
            # extract flight numbers (could be several)
            fnums = []
            for span in fb.find_all("span", class_=lambda c: c and 'flight-number' in c):
                txt = span.get_text(strip=True)
                if txt:
                    fnums.append(re.sub(r'\s+', '', txt))
            # fallback: text search inside block for flight pattern
            if not fnums:
                m = FLIGHT_RE.search(fb.get_text(" ", strip=True) if fb else "")
                if m:
                    fnums = [re.sub(r'\s+', '', m.group(1))]

            flight_number = fnums[0] if fnums else None

            # times
            dep = None
            arr = None
            origin_div = fb.find('div', class_=lambda c: c and 'origin' in c) if fb else None
            dest_div = fb.find('div', class_=lambda c: c and 'destination' in c) if fb else None
            if origin_div:
                t = origin_div.find('div', class_=lambda c: c and 'flt-times' in c)
                if t:
                    dep = re.sub(r'\+1', '', t.get_text(" ", strip=True)).strip()
            if dest_div:
                t = dest_div.find('div', class_=lambda c: c and 'flt-times' in c)
                if t:
                    arr = re.sub(r'\+1', '', t.get_text(" ", strip=True)).strip()

            # find corresponding product block nearby
            product_block = None
            # search siblings in DOM
            sib = fb.find_next_sibling()
            walk = 0
            while sib and walk < 8:
                if sib.find_all('span', class_=lambda c: c and ('per-pax-amount' in c or 'price' in c or 'amount' in c)):
                    product_block = sib
                    break
                sib = sib.find_next_sibling()
                walk += 1
            # fallback: search parent for app-available-products-desktop
            if not product_block:
                parent = fb.parent
                if parent:
                    product_block = parent.find('app-available-products-desktop') or parent.find('div', class_=lambda c: c and 'available-products' in c)

            # extract amounts from product_block (award: per-pax-amount; cash: price spans)
            if product_block:
                if is_award:
                    amount_spans = product_block.find_all('span', class_=lambda c: c and 'per-pax-amount' in c)
                    for s in amount_spans:
                        raw = s.get_text(strip=True)
                        pts = amount_to_int(raw)
                        # availability heuristic
                        parent_btn = s.find_parent('button')
                        available = True
                        if parent_btn:
                            cls = parent_btn.get('class') or []
                            if 'disabled' in cls or 'not-available' in cls:
                                available = False
                        if pts and flight_number:
                            key = (flight_number, dep, pts)
                            if key in seen:
                                continue
                            seen.add(key)
                            results.append({
                                "flight_number": flight_number,
                                "departure_time": dep,
                                "arrival_time": arr,
                                "miles": pts
                            })
                else:
                    # cash prices
                    price_spans = product_block.find_all('span', class_=lambda c: c and ('price' in c or 'amount' in c or 'fare' in c))
                    if not price_spans:
                        # fallback: any $ occurrences in product_block text
                        for m in PRICE_RE.finditer(product_block.get_text(" ", strip=True)):
                            try:
                                val = float(m.group(1).replace(',', ''))
                                if flight_number:
                                    key = (flight_number, dep, val)
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    results.append({
                                        "flight_number": flight_number,
                                        "departure_time": dep,
                                        "arrival_time": arr,
                                        "price": val
                                    })
                            except:
                                continue
                    else:
                        for s in price_spans:
                            raw = s.get_text(strip=True)
                            m = PRICE_RE.search(raw)
                            if m:
                                try:
                                    val = float(m.group(1).replace(',', ''))
                                    if flight_number:
                                        key = (flight_number, dep, val)
                                        if key in seen:
                                            continue
                                        seen.add(key)
                                        results.append({
                                            "flight_number": flight_number,
                                            "departure_time": dep,
                                            "arrival_time": arr,
                                            "price": val
                                        })
                                except:
                                    continue

        # As an additional fallback, look for "available-products-desktop" style lists (legacy)
        if not results:
            for container in soup.find_all('div', class_=lambda c: c and ('available-products' in c or 'available-products-desktop' in c or 'choose-flights-price' in c)):
                txt = container.get_text(" ", strip=True)
                fnums = FLIGHT_RE.findall(txt)
                if not fnums:
                    continue
                flight_number = re.sub(r'\s+', '', fnums[0])
                dep = None
                arr = None
                times = TIME_RE.findall(txt)
                if times:
                    dep = times[0]
                    arr = times[1] if len(times) > 1 else None
                if is_award:
                    for m in MILES_K_RE.finditer(txt):
                        pts = amount_to_int(m.group(0))
                        if pts:
                            key = (flight_number, dep, pts)
                            if key in seen:
                                continue
                            seen.add(key)
                            results.append({"flight_number": flight_number, "departure_time": dep, "arrival_time": arr, "miles": pts})
                    # comma-style
                    for m in MILES_COMMA_RE.finditer(txt):
                        try:
                            pts = int(m.group(1).replace(',', ''))
                            key = (flight_number, dep, pts)
                            if key in seen:
                                continue
                            seen.add(key)
                            results.append({"flight_number": flight_number, "departure_time": dep, "arrival_time": arr, "miles": pts})
                        except:
                            continue
                else:
                    for m in PRICE_RE.finditer(txt):
                        try:
                            val = float(m.group(1).replace(',', ''))
                            key = (flight_number, dep, val)
                            if key in seen:
                                continue
                            seen.add(key)
                            results.append({"flight_number": flight_number, "departure_time": dep, "arrival_time": arr, "price": val})
                        except:
                            continue

        return results

    # -------- parsing logic that handles both modern and legacy structures ----------
    def parse_flights(self, is_award: bool) -> List[Dict]:
        """Robust parse that gathers candidate text fragments from multiple selectors, then regex-parses them.
           Additionally attempts DOM-structured parsing via BeautifulSoup when available and merges results.
        """
        print(f"📊 Parsing {'award' if is_award else 'cash'} flights...")
        _save_html(self.driver, f"{'award' if is_award else 'cash'}_results")

        # Candidate selectors to extract fragments from (modern + legacy + list-like)
        selectors = [
            "app-slice-details",
            "div[class*='result']",
            "div[class*='slice']",
            "div[class*='flight']",
            "li[role='option']",
            "div[class*='offer']",
            "ul[role='list'] > li",
            "div[class*='itinerary']",
            "div[class*='card']"
        ]

        # Collect fragments via JS to minimize cross-process DOM queries
        fragments = []
        try:
            js = """
            const sels = arguments[0];
            const out = [];
            for (const s of sels) {
                try {
                    const nodes = Array.from(document.querySelectorAll(s));
                    for (const n of nodes) {
                        const t = (n.innerText || '').trim();
                        if (t && t.length > 20) out.push(t);
                    }
                } catch(e){}
            }
            // Also include page-level splits for cases where everything is in one container
            try {
                const shell = document.querySelector('div[class*=\"results\"], main, div[role=\"main\"]');
                if (shell) {
                    const parts = shell.innerText.split('\\n\\n');
                    for (const p of parts) {
                        const q = (p || '').trim();
                        if (q && q.length > 20) out.push(q);
                    }
                }
            } catch(e){}
            return out;
            """
            fragments = self.driver.execute_script(js, selectors) or []
        except Exception as e:
            # fallback: use full page source
            try:
                fragments = [self.driver.page_source]
            except Exception:
                fragments = []

        # Python-side parsing of fragments
        parsed = []
        seen_keys = set()
        for frag in fragments:
            text = re.sub(r'\s+', ' ', frag).strip()
            # Extract flight numbers (could be multiple per fragment for multi-segment)
            fnums = FLIGHT_RE.findall(text)
            if not fnums:
                continue
            # Use first flight number as canonical for this fragment
            flight_number = fnums[0].replace(" ", "")
            # Extract times
            times = TIME_RE.findall(text)
            if not times:
                # sometimes times use 24-hour or other spacing; try a looser regex for HH:MM
                times_loose = re.findall(r'\b([0-2]?\d:[0-5]\d)\b', text)
                times = times_loose[:2] if times_loose else []
            if not times or len(times) < 1:
                # can't parse times; skip fragment
                continue
            departure_time = times[0]
            arrival_time = times[1] if len(times) > 1 else None

            # Price
            price = None
            m_price = PRICE_RE.search(text)
            if m_price:
                try:
                    price = float(m_price.group(1).replace(',', ''))
                except:
                    price = None

            # Miles / points
            miles = None
            m_k = MILES_K_RE.search(text)
            if m_k:
                try:
                    miles = int(round(float(m_k.group(1)) * 1000))
                except:
                    miles = None
            if miles is None:
                m_comma = MILES_COMMA_RE.search(text)
                if m_comma:
                    try:
                        miles = int(m_comma.group(1).replace(',', ''))
                    except:
                        miles = None
            if miles is None:
                m_simple = MILES_SIMPLE_RE.search(text)
                if m_simple:
                    try:
                        miles = int(m_simple.group(1).replace(',', ''))
                    except:
                        miles = None

            # For award pages we allow entries with miles and no price; for cash pages we require price
            if is_award and miles is None:
                # try looser search for any "award" digits nearby
                extra = re.search(r'(\d{2,6})\s*(?:award|awards|miles|points|pts)\b', text, re.I)
                if extra:
                    try:
                        miles = int(extra.group(1).replace(',', ''))
                    except:
                        miles = None

            if not is_award and price is None:
                # Skip fragments without price for cash pass
                continue

            # Build record
            rec = {
                "flight_number": flight_number,
                "departure_time": departure_time,
                "arrival_time": arrival_time,
                "price": price,
                "miles": miles
            }

            key = (rec["flight_number"], rec["departure_time"], str(rec.get("price")), str(rec.get("miles")))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            parsed.append(rec)

        # Try DOM-structured extraction (BeautifulSoup) and merge results if it finds additional entries
        dom_parsed = []
        try:
            dom_parsed = self._parse_dom(is_award=is_award)
        except Exception:
            dom_parsed = []

        # Convert dom_parsed into same intermediate structure as parsed
        for d in dom_parsed:
            # d may contain 'miles' or 'price'
            fp = {
                "flight_number": d.get("flight_number"),
                "departure_time": d.get("departure_time"),
                "arrival_time": d.get("arrival_time"),
                "price": d.get("price") if "price" in d else None,
                "miles": d.get("miles") if "miles" in d else None
            }
            key = (fp["flight_number"], fp["departure_time"], str(fp.get("price")), str(fp.get("miles")))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            parsed.append(fp)

        # If parsed is empty but the page contains many times/flight numbers, fall back to page_source block parsing
        if not parsed:
            try:
                src = self.driver.page_source or ""
                blocks = re.split(r'</(?:div|li|section|article)>', src, flags=re.IGNORECASE)
                for b in blocks:
                    if len(b) < 200:
                        continue
                    txt = re.sub(r'<[^>]+>', ' ', b)
                    txt = re.sub(r'\s+', ' ', txt).strip()
                    if len(txt) < 30:
                        continue
                    # reuse same parsing logic for block
                    fnums = FLIGHT_RE.findall(txt)
                    if not fnums:
                        continue
                    flight_number = fnums[0].replace(" ", "")
                    times = TIME_RE.findall(txt)
                    if not times:
                        continue
                    departure_time = times[0]
                    arrival_time = times[1] if len(times) > 1 else None
                    m_price = PRICE_RE.search(txt)
                    price = float(m_price.group(1).replace(',', '')) if m_price else None
                    miles = None
                    m_k = MILES_K_RE.search(txt)
                    if m_k:
                        miles = int(round(float(m_k.group(1)) * 1000))
                    m_comma = MILES_COMMA_RE.search(txt)
                    if miles is None and m_comma:
                        try:
                            miles = int(m_comma.group(1).replace(',', ''))
                        except:
                            miles = None
                    rec = {"flight_number": flight_number, "departure_time": departure_time, "arrival_time": arrival_time, "price": price, "miles": miles}
                    key = (rec["flight_number"], rec["departure_time"], str(rec.get("price")), str(rec.get("miles")))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    parsed.append(rec)
                    if len(parsed) > 500:
                        break
            except Exception:
                pass

        _dump(parsed, f"raw_{'award' if is_award else 'cash'}_flights")

        # Convert parsed list to expected output format for this stage (before merging)
        flights = []
        for p in parsed:
            if is_award and p.get("miles"):
                flights.append({
                    "flight_number": p["flight_number"],
                    "departure_time": p["departure_time"],
                    "arrival_time": p["arrival_time"],
                    "points_required": int(p["miles"])
                })
            elif not is_award and p.get("price") is not None:
                flights.append({
                    "flight_number": p["flight_number"],
                    "departure_time": p["departure_time"],
                    "arrival_time": p["arrival_time"],
                    "cash_price_usd": float(p["price"]),
                    "taxes_fees_usd": 5.60
                })

        # Deduplicate final list
        seen = set()
        unique = []
        for f in flights:
            key = (f["flight_number"], f["departure_time"], f.get("cash_price_usd"), f.get("points_required"))
            if key not in seen:
                seen.add(key)
                unique.append(f)

        print(f"  Extracted {len(unique)} flights")
        _dump(unique, f"parsed_{'award' if is_award else 'cash'}_flights")
        return unique

    def search_flights(self, origin: str, dest: str, date: str, redeem_miles: bool) -> List[Dict]:
        """Complete search flow with additional coaxing for award pass"""
        self.fill_search_form(origin, dest, date, redeem_miles)
        self.submit_search()
        # Award pages can be slower / require extra toggles: give them more time and different heuristics
        ok = self.wait_for_results(timeout=90 if redeem_miles else 60, is_award=redeem_miles, expected_min=10)
        if not ok:
            print("⚠ Results not detected (timed out). Attempting one recovery cycle (scroll/click/toggle) and retry.")
            # recovery attempt: scroll, click possible award toggles, re-check
            try:
                self._coax_lazy_load()
                # click possible award toggles
                if redeem_miles:
                    try:
                        toggles = self.driver.find_elements(By.XPATH, "//button[contains(., 'Miles') or contains(., 'Redeem') or contains(., 'Award') or contains(., 'Show award') or contains(., 'See awards')]")
                        for t in toggles:
                            try:
                                if t.is_displayed():
                                    self.driver.execute_script("arguments[0].click()", t)
                                    time.sleep(0.6)
                            except:
                                continue
                    except:
                        pass
                time.sleep(2.0)
                ok = self.wait_for_results(timeout=45, is_award=redeem_miles, expected_min=8)
            except Exception:
                ok = False

        if not ok:
            print("⚠ Failed to detect results after recovery attempts; returning empty list")
            return []

        return self.parse_flights(is_award=redeem_miles)

    def close(self):
        try:
            time.sleep(1.0)
            if self.driver:
                self.driver.quit()
        except:
            pass

# ---------- top-level orchestration ----------
def search(params: Dict[str, Any]) -> Dict[str, Any]:
    scraper = AAScraper()

    try:
        scraper.setup()
        scraper.open_home()

        # PASS 1: Cash prices
        print(f"\n{'='*60}")
        print("PASS 1: CASH PRICES")
        print(f"{'='*60}")
        cash_flights = scraper.search_flights(
            params["origin"],
            params["destination"],
            params["date"],
            redeem_miles=False
        )

        # Return home (soft reset)
        print("\n🏠 Returning home...")
        try:
            scraper.driver.get("https://www.aa.com/")
            time.sleep(2.5)
            scraper._dismiss_popups()
        except Exception:
            pass

        # PASS 2: Award prices
        print(f"\n{'='*60}")
        print("PASS 2: AWARD PRICES")
        print(f"{'='*60}")
        award_flights = scraper.search_flights(
            params["origin"],
            params["destination"],
            params["date"],
            redeem_miles=True
        )

        # Merge and calculate CPP
        merged = merge_and_dedup(cash_flights, award_flights)

        output = {
            "search_metadata": {
                "origin": params["origin"],
                "destination": params["destination"],
                "date": params["date"],
                "passengers": params.get("passengers", 1),
                "cabin_class": params.get("cabin", "economy"),
                "cash_count": len(cash_flights),
                "award_count": len(award_flights),
                "merged_count": len(merged)
            },
            "flights": merged,
            "total_results": len(merged)
        }

        # Save output
        pathlib.Path("output.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

        print(f"\n{'='*60}")
        print("✅ SUCCESS")
        print(f"{'='*60}")
        print(f"Cash flights:  {len(cash_flights)}")
        print(f"Award flights: {len(award_flights)}")
        print(f"Merged:        {len(merged)}")
        print(f"Output:        output.json")
        print(f"{'='*60}\n")

        return output

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        scraper.close()

def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="AA Flight Scraper - CPP Calculator")
    ap.add_argument("--origin", required=True, help="Origin airport code (e.g., LAX)")
    ap.add_argument("--destination", required=True, help="Destination airport code (e.g., JFK)")
    ap.add_argument("--date", required=True, help="Date in YYYY-MM-DD format")
    ap.add_argument("--passengers", type=int, default=1, help="Number of passengers")
    ap.add_argument("--cabin", default="economy", help="Cabin class")
    args = ap.parse_args(argv)

    return {
        "origin": args.origin.upper(),
        "destination": args.destination.upper(),
        "date": args.date,
        "passengers": args.passengers,
        "cabin": args.cabin.lower()
    }

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    params = _cli(argv)

    print(f"\n{'='*60}")
    print("AA Flight Scraper - CPP Calculator")
    print(f"{params['origin']} → {params['destination']} on {params['date']}")
    print(f"{'='*60}\n")

    result = search(params)

    # Print summary
    if result["flights"]:
        print("Sample flights:")
        sample = result["flights"][:5]
        for flight in sample:
            # merged flights have cash_price_usd and points_required
            cp = flight.get("cash_price_usd")
            pts = flight.get("points_required")
            cpp = flight.get("cpp")
            if cp is None or pts is None:
                print(f"  {flight.get('flight_number')} - partial data")
            else:
                print(f"  {flight['flight_number']}: ${cp:.2f} or {pts:,} pts → CPP: {cpp}")

if __name__ == "__main__":
    main()