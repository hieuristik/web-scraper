# src/crawler_api.py
import os, re, json, time, sys, pathlib, types
from typing import Any, Dict, List
OUT = pathlib.Path("data/debug"); OUT.mkdir(parents=True, exist_ok=True)

def _dump(x, name):
    try:
        (OUT/f"{name}.json").write_text(json.dumps(x, indent=2, ensure_ascii=False), encoding="utf-8")
    except: pass

def _save_html(driver, name):
    try:
        (OUT/f"{name}.html").write_text(driver.page_source, encoding="utf-8")
    except: pass

# ---- Py3.12 distutils shim ----
try:
    import distutils  # noqa: F401
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
    from selenium.webdriver.common.keys import Keys
    from selenium.common.exceptions import TimeoutException
except Exception as e:
    raise RuntimeError(f"Missing selenium: {e}")

def mmddyyyy(date_iso: str) -> str:
    y, m, d = date_iso.split("-")
    return f"{int(m):02d}/{int(d):02d}/{y}"

def calculate_cpp(cash: float, taxes: float, points: int) -> float:
    return 0.0 if not points else round(((cash - taxes)/points)*100, 2)

def merge_and_dedup(cash: List[Dict], award: List[Dict]) -> List[Dict]:
    """Match by flight number + departure time."""
    merged, used_award = [], set()
    for c in cash:
        for idx, a in enumerate(award):
            if idx in used_award: continue
            if c.get("flight_number")==a.get("flight_number") and c.get("departure_time")==a.get("departure_time"):
                item = {
                    "flight_number": c["flight_number"],
                    "departure_time": c["departure_time"],
                    "arrival_time": c["arrival_time"],
                    "cash_price_usd": float(c["cash_price_usd"]),
                    "taxes_fees_usd": float(c.get("taxes_fees_usd", 5.60)),
                    "points_required": int(a["points_required"]),
                }
                item["cpp"] = calculate_cpp(item["cash_price_usd"], item["taxes_fees_usd"], item["points_required"])
                merged.append(item)
                used_award.add(idx)
                break
    # dedup
    seen, unique = set(), []
    for m in merged:
        key = (m["flight_number"], m["departure_time"], m["cash_price_usd"], m["points_required"])
        if key not in seen:
            seen.add(key); unique.append(m)
    return unique

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
        opts.add_argument("--disable-gpu")
        opts.add_argument("--lang=en-US,en")
        self.driver = uc.Chrome(options=opts)
        print("✓ Chrome ready")

    def _dismiss_popups(self):
        try:
            self.driver.execute_script("""
                const clickAll = (sel) => document.querySelectorAll(sel).forEach(b=>{try{b.click()}catch(e){}});
                clickAll('#onetrust-accept-btn-handler');
                clickAll('button[aria-label*="close" i]');
                document.querySelectorAll('adc-cookie-banner, #onetrust-banner-sdk, .onetrust-pc-dark-filter')
                  .forEach(el=>{try{el.remove()}catch(e){}});
            """)
        except: pass

    def _hard_reset_state(self):
        """Reduce SPA bleed between passes."""
        try:
            self.driver.delete_all_cookies()
        except: pass
        try:
            self.driver.execute_script("window.localStorage && localStorage.clear(); window.sessionStorage && sessionStorage.clear();")
        except: pass
        self.driver.get("https://www.aa.com/")
        time.sleep(4)
        self._dismiss_popups()

    def open_home(self):
        print("🌐 Loading AA.com...")
        self.driver.get("https://www.aa.com/")
        time.sleep(4)
        self._dismiss_popups()
        _save_html(self.driver, "home")
        print("✓ Homepage loaded")

    def fill_search_form(self, origin: str, dest: str, date: str, redeem_miles: bool):
        print(f"📝 Filling form ({'Award' if redeem_miles else 'Cash'} mode)...")
        self._dismiss_popups(); time.sleep(0.3)

        # One-way
        try:
            self.driver.find_element(By.CSS_SELECTOR,"label[for='flightSearchForm.tripType.oneWay']").click()
            print("✓ One-way selected (via label)")
        except:
            try:
                ow = self.driver.find_element(By.ID, "flightSearchForm.tripType.oneWay")
                self.driver.execute_script("arguments[0].click()", ow)
                print("✓ One-way selected (via JS)")
            except Exception as e:
                print(f"  ⚠ One-way selection failed: {e}")

        # Redeem miles
        try:
            cb = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
            checked = cb.is_selected()
            if redeem_miles and not checked:
                self.driver.execute_script("arguments[0].click()", cb)
            if not redeem_miles and checked:
                self.driver.execute_script("arguments[0].click()", cb)
            time.sleep(0.2)
            cb2 = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
            print(f"✓ Redeem miles {'enabled' if cb2.is_selected() else 'disabled'} (verified)")
        except Exception as e:
            print(f"  ⚠ Redeem miles toggle failed: {e}")

        # Origin/Dest
        try:
            o = self.driver.find_element(By.NAME, "originAirport"); o.clear(); o.send_keys(origin); time.sleep(0.6); o.send_keys(Keys.TAB)
            print(f"✓ Origin: {origin}")
        except Exception as e: print(f"  ⚠ Origin input failed: {e}")
        try:
            d = self.driver.find_element(By.NAME, "destinationAirport"); d.clear(); d.send_keys(dest); time.sleep(0.6); d.send_keys(Keys.TAB)
            print(f"✓ Destination: {dest}")
        except Exception as e: print(f"  ⚠ Destination input failed: {e}")

        # Date
        try:
            date_val = mmddyyyy(date)
            self.driver.execute_script("""
                const val = arguments[0];
                document.querySelectorAll("input[name*='depart' i]").forEach(inp=>{
                    inp.value = val;
                    inp.dispatchEvent(new Event('input',{bubbles:true}));
                    inp.dispatchEvent(new Event('change',{bubbles:true}));
                });
            """, date_val)
            print(f"✓ Date: {date_val}")
        except Exception as e: print(f"  ⚠ Date input failed: {e}")

    def submit_search(self):
        self._dismiss_popups()
        try:
            self.driver.find_element(By.XPATH,"//button[contains(., 'Search') or @type='submit']").click()
            print("✓ Search submitted")
        except:
            try:
                self.driver.find_element(By.NAME,"destinationAirport").send_keys(Keys.ENTER)
                print("↩ Search submitted (Enter key)")
            except Exception as e:
                print(f"  ⚠ Submit failed: {e}")
        time.sleep(2)

    # ---------- loading & detection ----------
    def ensure_all_results_loaded(self, expect_award: bool) -> Dict[str,int]:
        """
        Scrolls the results list until counts stabilize or we reach 40.
        Returns {'modern': m, 'legacy': l, 'total': t}.
        """
        last_total = -1
        stable_loops = 0
        max_loops = 30
        clicked_more_once = False

        for i in range(max_loops):
            self._dismiss_popups()
            counts = self.driver.execute_script("""
                const count = (sel) => document.querySelectorAll(sel).length;
                return {
                    modern: count('app-slice-details'),
                    legacy_btn: count('.btn-flight, button[class*="flight"]'),
                    rows_like: count('div[class*="result"], div[class*="slice"], div[class*="flight"]')
                };
            """)
            modern = counts.get("modern",0)
            legacy_guess = max(counts.get("legacy_btn",0), counts.get("rows_like",0))
            total = modern + legacy_guess

            if i == 0:
                print(f"✓ Found {total} flight elements (starting) [{'award/legacy' if expect_award else 'modern' if modern>legacy_guess else 'legacy'}]")

            # Try "Show more results" buttons if present once
            if not clicked_more_once:
                try:
                    more = self.driver.find_elements(By.XPATH, "//button[contains(., 'Show more') or contains(., 'More results')]")
                    for b in more:
                        if b.is_displayed(): 
                            self.driver.execute_script("arguments[0].click()", b)
                            clicked_more_once = True
                            time.sleep(1.0)
                            break
                except: pass

            # Scroll results container and window
            try:
                scrolled = self.driver.execute_script("""
                    const cands = [
                      '[class*=\"results\" i]',
                      'main',
                      'div[role=\"main\"]'
                    ];
                    let did = false;
                    for (const sel of cands) {
                      const el = document.querySelector(sel);
                      if (el) { el.scrollTo(0, el.scrollHeight); did = true; }
                    }
                    window.scrollTo(0, document.body.scrollHeight);
                    return did ? 1 : 0;
                """)
            except: pass
            time.sleep(0.3)

            # stabilization logic
            if total == last_total:
                stable_loops += 1
            else:
                stable_loops = 0
            last_total = total

            # success conditions
            if modern >= 40 or legacy_guess >= 40:
                print(f"✓ All results loaded: {max(modern, legacy_guess)} flight elements visible")
                return {"modern": modern, "legacy": legacy_guess, "total": total}

            # consider “stable enough”
            if stable_loops >= 5 and max(modern,legacy_guess) >= 40:
                print(f"✓ All results loaded: {max(modern, legacy_guess)} flight elements visible")
                return {"modern": modern, "legacy": legacy_guess, "total": total}

        print("⚠ ensure_all_results_loaded hit timeout")
        return {"modern": modern, "legacy": legacy_guess, "total": total}

    def wait_for_results(self, is_award=False):
        # initial spinner/wait; AA sometimes shows an overlay
        print("⏳ Waiting for results...")
        ok = False
        for _ in range(60):
            try:
                ready = self.driver.execute_script("""
                    const modern = document.querySelectorAll('app-slice-details').length;
                    const flights = document.querySelectorAll('.btn-flight, button[class*=\"flight\"]').length;
                    const rows   = document.querySelectorAll('div[class*=\"result\"], div[class*=\"slice\"], div[class*=\"flight\"]').length;
                    return modern + flights + rows;
                """)
                if ready and ready > 0:
                    ok = True; break
            except: pass
            time.sleep(0.5)
        if not ok:
            print("⚠ Results never appeared")
            return False

        # Awards pages sometimes hide miles behind a toggle. Try to click it.
        if is_award:
            try:
                toggles = self.driver.find_elements(By.XPATH, "//button[contains(translate(., 'MILES', 'miles'),'miles')]")
                for t in toggles:
                    if t.is_displayed(): 
                        self.driver.execute_script("arguments[0].click()", t)
                        time.sleep(0.4)
                        break
            except: pass

        # Now aggressively load all rows
        self.ensure_all_results_loaded(expect_award=is_award)
        return True

    # ---------- parsing ----------
    def parse_flights(self, is_award: bool) -> List[Dict]:
        print(f"📊 Parsing {'award' if is_award else 'cash'} flights...")
        _save_html(self.driver, f"{'award' if is_award else 'cash'}_results")

        structure = self.driver.execute_script("""
            return {
              modern: document.querySelectorAll('app-slice-details').length,
              legacy: document.querySelectorAll('.btn-flight, button[class*=\"flight\"], div[class*=\"result\"], div[class*=\"slice\"], div[class*=\"flight\"]').length
            };
        """)
        use_modern = structure['modern'] >= 20 and not is_award  # awards usually not modern
        print(f"  Using {'modern' if use_modern else 'legacy'} parser")

        raw = self._parse_modern_js() if use_modern else self._parse_legacy_award_js()
        return self._format_flights(raw, is_award)

    def _parse_modern_js(self) -> List[Dict]:
        js = r"""
        const slices = document.querySelectorAll('app-slice-details');
        const results = [];
        for (const slice of slices) {
          try {
            const txt = slice.innerText || '';
            const times = txt.match(/(\d{1,2}:\d{2}\s*(?:AM|PM))/gi);
            if (!times || times.length < 2) continue;
            const dep = times[0], arr = times[1];
            const fm = txt.match(/\b([A-Z]{2})\s+(\d{1,4})\b/);
            if (!fm) continue;
            const fno = fm[1] + fm[2];

            // walk upwards to find cash price if present
            let p = slice, price = null;
            for (let i=0; i<6 && p; i++) {
              const t = p.innerText || '';
              const pm = t.match(/\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)/);
              if (pm) { price = parseFloat(pm[1].replace(/,/g,'')); break; }
              p = p.parentElement;
            }
            if (!fno || !dep || !arr) continue;
            results.push({ flight_number: fno, departure_time: dep, arrival_time: arr, price: price, miles: null });
          } catch(e) {}
        }
        return results;
        """
        try:
            return self.driver.execute_script(js) or []
        except Exception as e:
            print(f"  ⚠ JS execution error (modern): {e}")
            return []

    def _parse_legacy_award_js(self) -> List[Dict]:
        # No 'and' or tricky concatenation; safely search containers then normalize.
        js = r"""
        // Collect candidate containers that look like a single flight row.
        const cands = Array.from(document.querySelectorAll(
          'div[class*="result"], div[class*="slice"], div[class*="flight"], li[class*="result"]'
        ));

        const results = [];
        const seen = new Set();

        function normMiles(text) {
          // 12.5K -> 12500, 12,500 -> 12500
          const k = text.match(/(\d+(?:\.\d+)?)\s*K\b/i);
          if (k) return Math.round(parseFloat(k[1]) * 1000);
          const c = text.match(/(\d{1,3}(?:,\d{3})+)\s*(?:mile|miles|point|points)\b/i);
          if (c) return parseInt(c[1].replace(/,/g,''));
          return null;
        }

        function findNearbyText(el, depth=2) {
          let txt = el.innerText || '';
          let p = el.parentElement;
          for (let i=0; i<depth && p; i++) {
            txt += ' ' + (p.innerText || '');
            p = p.parentElement;
          }
          return txt;
        }

        for (const div of cands) {
          try {
            const txt = (div.innerText || '');
            const times = txt.match(/(\d{1,2}:\d{2}\s*(?:AM|PM))/gi);
            if (!times || times.length < 2) continue;
            const dep = times[0], arr = times[1];

            // flight number in-node or nearby
            let fno = '';
            const fEl = div.querySelector('.flight-number, [class*="flight-number"], .flight-details [class*="number"]');
            if (fEl) {
              const m = (fEl.innerText || '').match(/([A-Z]{2})\s*(\d{1,4})/);
              if (m) fno = m[1]+m[2];
            }
            if (!fno) {
              const m2 = txt.match(/\b([A-Z]{2})\s+(\d{1,4})\b/);
              if (m2) fno = m2[1]+m2[2];
            }
            if (!fno) {
              // sometimes only nearby parents hold the flight number
              const near = findNearbyText(div, 3);
              const m3 = near.match(/\b([A-Z]{2})\s+(\d{1,4})\b/);
              if (m3) fno = m3[1]+m3[2];
            }
            if (!fno) continue;

            const key = fno + '|' + dep;
            if (seen.has(key)) continue;
            seen.add(key);

            // price (cash) if present in legacy view
            let price = null;
            const pm = txt.match(/\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)/);
            if (pm) price = parseFloat(pm[1].replace(/,/g,''));

            // miles: try node; then siblings/parents; then aria
            let miles = normMiles(txt);
            if (miles === null) {
              const near = findNearbyText(div, 2);
              miles = normMiles(near);
            }
            if (miles === null) {
              // aria-label probes
              const ariaNodes = div.querySelectorAll('[aria-label]');
              for (const n of ariaNodes) {
                const ml = normMiles(n.getAttribute('aria-label') || '');
                if (ml !== null) { miles = ml; break; }
              }
            }

            if ((price !== null || miles !== null)) {
              results.push({ flight_number: fno, departure_time: dep, arrival_time: arr, price: price, miles: miles });
            }
          } catch(e) {}
        }

        return results;
        """
        try:
            return self.driver.execute_script(js) or []
        except Exception as e:
            print(f"  ⚠ JS execution error (legacy): {e}")
            return []

    def _format_flights(self, raw: List[Dict], is_award: bool) -> List[Dict]:
        _dump(raw, f"raw_{'award' if is_award else 'cash'}_flights")
        flights = []
        for f in raw:
            if is_award and f.get("miles"):
                flights.append({
                    "flight_number": f["flight_number"],
                    "departure_time": f["departure_time"],
                    "arrival_time": f["arrival_time"],
                    "points_required": int(f["miles"])
                })
            elif not is_award and f.get("price") is not None:
                flights.append({
                    "flight_number": f["flight_number"],
                    "departure_time": f["departure_time"],
                    "arrival_time": f["arrival_time"],
                    "cash_price_usd": float(f["price"]),
                    "taxes_fees_usd": 5.60
                })
        # dedup
        seen, unique = set(), []
        for f in flights:
            key = (f["flight_number"], f["departure_time"], f.get("cash_price_usd"), f.get("points_required"))
            if key not in seen:
                seen.add(key); unique.append(f)
        print(f"  Extracted {len(unique)} flights")
        _dump(unique, f"parsed_{'award' if is_award else 'cash'}_flights")
        return unique

    def search_flights(self, origin: str, dest: str, date: str, redeem_miles: bool) -> List[Dict]:
        self.fill_search_form(origin, dest, date, redeem_miles)
        self.submit_search()
        if not self.wait_for_results(is_award=redeem_miles):
            return []
        return self.parse_flights(is_award=redeem_miles)

    def close(self):
        try:
            self.driver.quit()
        except: pass
        finally:
            self.driver = None

def search(params: Dict[str, Any]) -> Dict[str, Any]:
    s = AAScraper()
    try:
        s.setup()
        s.open_home()

        print(f"\n{'='*60}\nPASS 1: CASH PRICES\n{'='*60}")
        cash = s.search_flights(params["origin"], params["destination"], params["date"], redeem_miles=False)

        print("\n🏠 Returning home & resetting...")
        s._hard_reset_state()

        print(f"\n{'='*60}\nPASS 2: AWARD PRICES\n{'='*60}")
        award = s.search_flights(params["origin"], params["destination"], params["date"], redeem_miles=True)

        merged = merge_and_dedup(cash, award)
        out = {
            "search_metadata": {
                "origin": params["origin"], "destination": params["destination"], "date": params["date"],
                "passengers": params.get("passengers",1), "cabin_class": params.get("cabin","economy"),
                "cash_count": len(cash), "award_count": len(award), "merged_count": len(merged)
            },
            "flights": merged, "total_results": len(merged)
        }
        pathlib.Path("output.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

        print(f"\n{'='*60}\n✅ SUCCESS\n{'='*60}")
        print(f"Cash flights:  {len(cash)}")
        print(f"Award flights: {len(award)}")
        print(f"Merged:        {len(merged)}")
        print(f"Output:        output.json")
        print(f"Debug files:   data/debug/\n{'='*60}\n")
        return out
    finally:
        s.close()

def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="AA Flight Scraper - CPP Calculator")
    ap.add_argument("--origin", required=True)
    ap.add_argument("--destination", required=True)
    ap.add_argument("--date", required=True)  # YYYY-MM-DD
    ap.add_argument("--passengers", type=int, default=1)
    ap.add_argument("--cabin", default="economy")
    a = ap.parse_args(argv)
    return {"origin": a.origin.upper(), "destination": a.destination.upper(), "date": a.date,
            "passengers": a.passengers, "cabin": a.cabin.lower()}

def main(argv=None):
    if argv is None: argv = sys.argv[1:]
    p = _cli(argv)
    print(f"\n{'='*60}\nAA Flight Scraper - CPP Calculator\n{p['origin']} → {p['destination']} on {p['date']}\n{'='*60}\n")
    res = search(p)
    if res["flights"]:
        print("\nSample flights with CPP:")
        for f in res["flights"][:5]:
            print(f"  {f['flight_number']}: ${f['cash_price_usd']:.2f} or {f['points_required']:,} pts → CPP: {f['cpp']}")
    else:
        print("\n⚠ No matching flights found with both cash and award pricing")

if __name__ == "__main__":
    main()
