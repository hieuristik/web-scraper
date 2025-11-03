# src/crawler_api.py
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
    raise RuntimeError(f"Missing selenium: {e}")
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
        
        try:
            self.driver = uc.Chrome(options=opts)
            print("✓ Chrome ready")
        except Exception as e:
            raise RuntimeError(f"Failed to start Chrome: {e}")
    
    def _dismiss_popups(self):
        """Close cookie banners - AGGRESSIVE version"""
        try:
            # Method 1: Click accept button
            self.driver.execute_script("""
                document.querySelectorAll('#onetrust-accept-btn-handler, button[aria-label*="close" i]').forEach(el => {
                    try { el.click(); } catch(e) {}
                });
            """)
            time.sleep(0.3)
            
            # Method 2: Hide cookie banner element completely
            self.driver.execute_script("""
                const banners = document.querySelectorAll('adc-cookie-banner, #onetrust-banner-sdk, .onetrust-pc-dark-filter');
                banners.forEach(el => {
                    if (el) {
                        el.style.display = 'none';
                        el.style.visibility = 'hidden';
                        el.style.opacity = '0';
                        el.style.zIndex = '-9999';
                        el.remove();
                    }
                });
            """)
            time.sleep(0.3)
        except:
            pass
    
    def open_home(self):
        print("🌐 Loading AA.com...")
        self.driver.get("https://www.aa.com/")
        time.sleep(4)
        self._dismiss_popups()
        _save_html(self.driver, "home")
        print("✓ Homepage loaded")
    
    def fill_search_form(self, origin: str, dest: str, date: str, redeem_miles: bool):
        """Fill the search form with exact selectors"""
        print(f"📝 Filling form ({'Award' if redeem_miles else 'Cash'} mode)...")
        
        # CRITICAL: Dismiss popups FIRST before any clicks
        self._dismiss_popups()
        time.sleep(0.5)
        
        # Click one-way radio - click the LABEL, not the input
        try:
            # Try clicking the label first (most reliable)
            oneway_label = self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.oneWay']")
            oneway_label.click()
            time.sleep(0.3)
            print("✓ One-way selected (via label)")
        except Exception as e1:
            # Fallback: try the input element
            try:
                oneway = self.driver.find_element(By.ID, "flightSearchForm.tripType.oneWay")
                if not oneway.is_selected():
                    # Use JavaScript click as fallback
                    self.driver.execute_script("arguments[0].click();", oneway)
                    time.sleep(0.3)
                print("✓ One-way selected (via JS)")
            except Exception as e2:
                print(f"  ⚠ One-way selection failed: {e1}, {e2}")
        
        # Dismiss popups again before checkbox
        self._dismiss_popups()
        
        # Toggle redeem miles checkbox if needed
        if redeem_miles:
            try:
                # Try label first
                try:
                    checkbox_label = self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.redeemMiles']")
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox_label)
                    time.sleep(0.3)
                    checkbox_label.click()
                except:
                    # Fallback: Force click with JavaScript
                    checkbox = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox)
                    time.sleep(0.3)
                    self.driver.execute_script("arguments[0].click();", checkbox)
                
                time.sleep(0.5)
                
                # Verify it's actually checked
                checkbox = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
                if checkbox.is_selected():
                    print("✓ Redeem miles enabled (verified)")
                else:
                    print("⚠ Redeem miles checkbox not checked - retrying...")
                    self.driver.execute_script("arguments[0].checked = true; arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", checkbox)
                    time.sleep(0.3)
                    if checkbox.is_selected():
                        print("✓ Redeem miles enabled (via JS force)")
                    else:
                        print("❌ Redeem miles failed to enable")
            except Exception as e:
                print(f"  ⚠ Redeem miles failed: {e}")
        else:
            # Make sure it's unchecked for cash search
            try:
                checkbox = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
                if checkbox.is_selected():
                    self.driver.execute_script("arguments[0].click();", checkbox)
                    time.sleep(0.3)
                print("✓ Redeem miles disabled")
            except:
                pass
        
        self._dismiss_popups()
        
        # Fill origin
        try:
            origin_input = self.driver.find_element(By.NAME, "originAirport")
            origin_input.clear()
            time.sleep(0.2)
            for char in origin:
                origin_input.send_keys(char)
                time.sleep(0.05)
            time.sleep(1)
            origin_input.send_keys(Keys.TAB)
            time.sleep(0.5)
            print(f"✓ Origin: {origin}")
        except Exception as e:
            print(f"  ⚠ Origin input failed: {e}")
        
        # Fill destination
        try:
            dest_input = self.driver.find_element(By.NAME, "destinationAirport")
            dest_input.clear()
            time.sleep(0.2)
            for char in dest:
                dest_input.send_keys(char)
                time.sleep(0.05)
            time.sleep(1)
            dest_input.send_keys(Keys.TAB)
            time.sleep(0.5)
            print(f"✓ Destination: {dest}")
        except Exception as e:
            print(f"  ⚠ Destination input failed: {e}")
        
        # Fill date
        try:
            date_val = mmddyyyy(date)
            self.driver.execute_script(f"""
                const inputs = document.querySelectorAll("input[name*='depart' i]");
                inputs.forEach(inp => {{
                    inp.value = '{date_val}';
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                }});
            """)
            time.sleep(0.5)
            print(f"✓ Date: {date_val}")
        except Exception as e:
            print(f"  ⚠ Date input failed: {e}")
    
    def submit_search(self):
        """Submit the search"""
        self._dismiss_popups()
        
        try:
            # Find search button
            search_btn = self.driver.find_element(By.XPATH, "//button[contains(., 'Search') or @type='submit']")
            search_btn.click()
            print("✓ Search submitted")
        except:
            # Fallback: Enter key on destination
            try:
                dest = self.driver.find_element(By.NAME, "destinationAirport")
                dest.send_keys(Keys.ENTER)
                print("↩ Search submitted (Enter key)")
            except Exception as e:
                print(f"  ⚠ Submit failed: {e}")
        
        time.sleep(2)
    
    def wait_for_results(self, timeout=90, is_award=False):
        """Wait for flight results - handles both modern and legacy page structures"""
        print("⏳ Waiting for results...")
        
        for i in range(timeout):
            self._dismiss_popups()
            
            try:
                # Modern structure (cash flights typically use this)
                modern_count = self.driver.execute_script("""
                    return document.querySelectorAll('app-slice-details').length;
                """)
                
                # Legacy structure (award flights use this)
                legacy_count = self.driver.execute_script("""
                    return document.querySelectorAll('.btn-flight, button[class*="flight"]').length;
                """)
                
                total_count = modern_count + legacy_count
                
                # Debug logging every 10 seconds
                if i > 0 and i % 10 == 0:
                    print(f"  [{i}s] Found: {modern_count} modern + {legacy_count} legacy elements")
                
                # Accept if we have enough flights in either format
                if modern_count >= 10 or legacy_count >= 10:
                    structure = "modern (app-slice-details)" if modern_count >= 10 else "legacy (btn-flight)"
                    print(f"✓ Found {total_count} flight elements after {i+1}s ({structure})")
                    time.sleep(3)  # Extra wait for prices to stabilize
                    return True
                    
            except Exception as e:
                if i == 30:
                    print(f"  Error during wait: {e}")
            
            time.sleep(1)
        
        print("⚠ Timeout waiting for results")
        _save_html(self.driver, f"{'award' if is_award else 'cash'}_timeout")
        return False
    
    def parse_flights(self, is_award: bool) -> List[Dict]:
        """Parse flights - handles both modern and legacy page structures"""
        print(f"📊 Parsing {'award' if is_award else 'cash'} flights...")
        
        _save_html(self.driver, f"{'award' if is_award else 'cash'}_results")
        
        # Detect which structure we have
        structure_check = self.driver.execute_script("""
            return {
                modern: document.querySelectorAll('app-slice-details').length,
                legacy: document.querySelectorAll('.btn-flight, button[class*="flight"]').length
            };
        """)
        
        use_modern = structure_check['modern'] >= 10
        print(f"  Using {'modern' if use_modern else 'legacy'} parser ({structure_check['modern']} modern, {structure_check['legacy']} legacy)")
        
        if use_modern:
            return self._parse_modern(is_award)
        else:
            return self._parse_legacy(is_award)
    
    def _parse_modern(self, is_award: bool) -> List[Dict]:
        """Parse modern app-slice-details structure"""
        js_code = """
        const slices = document.querySelectorAll('app-slice-details');
        const results = [];
        
        for (let slice of slices) {
            try {
                const text = slice.innerText || '';
                
                // Extract times
                const times = text.match(/(\\d{1,2}:\\d{2}\\s*(?:AM|PM))/gi);
                if (!times || times.length < 2) continue;
                
                const depTime = times[0];
                const arrTime = times[1];
                
                // Extract flight number
                const flightMatch = text.match(/\\b([A-Z]{2})\\s+(\\d{1,4})\\b/);
                const flightNo = flightMatch ? flightMatch[1] + flightMatch[2] : '';
                
                if (!flightNo) continue;
                
                // Walk up DOM to find parent with price/miles
                let parent = slice.parentElement;
                let price = null;
                let miles = null;
                let attempts = 0;
                
                while (parent && attempts < 6) {
                    const parentText = parent.innerText || '';
                    
                    // Extract price
                    if (!price) {
                        const priceMatch = parentText.match(/\\$(\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?)/);
                        if (priceMatch) {
                            price = parseFloat(priceMatch[1].replace(/,/g, ''));
                        }
                    }
                    
                    // Extract miles
                    if (!miles) {
                        const milesMatch = parentText.match(/(\\d+(?:\\.\\d+)?)K\\b|(\\d{1,3}(?:,\\d{3})+)\\s*(?:mile|point)/i);
                        if (milesMatch) {
                            if (milesMatch[1]) {
                                miles = parseInt(parseFloat(milesMatch[1]) * 1000);
                            } else if (milesMatch[2]) {
                                miles = parseInt(milesMatch[2].replace(/,/g, ''));
                            }
                        }
                    }
                    
                    if (price !== null || miles !== null) break;
                    
                    parent = parent.parentElement;
                    attempts++;
                }
                
                if (flightNo && depTime && arrTime && (price !== null || miles !== null)) {
                    results.push({
                        flight_number: flightNo,
                        departure_time: depTime,
                        arrival_time: arrTime,
                        price: price,
                        miles: miles
                    });
                }
            } catch (e) {
                console.error('Parse error:', e);
            }
        }
        
        return results;
        """
        
        try:
            raw = self.driver.execute_script(js_code)
        except Exception as e:
            print(f"  ⚠ JS execution error: {e}")
            raw = []
        
        return self._format_flights(raw, is_award)
    
    def _parse_legacy(self, is_award: bool) -> List[Dict]:
        """Parse legacy structure used by award flights"""
        js_code = """
        // Try multiple strategies to find flight containers
        let containers = [];
        
        // Strategy 1: Find by button and walk up
        const buttons = document.querySelectorAll('.btn-flight, button[class*="flight"], button[id*="flight"]');
        for (let btn of buttons) {
            let container = btn.closest('div[class*="result"], div[class*="flight"], .flight-row');
            if (!container) {
                // Walk up manually
                let el = btn.parentElement;
                let depth = 0;
                while (el && depth < 5) {
                    const text = (el.innerText || '').substring(0, 300);
                    // Check if this element has flight info (times + flight number + price/miles)
                    if (text.match(/\\d{1,2}:\\d{2}\\s*(?:AM|PM)/gi) && 
                        text.match(/[A-Z]{2}\\s*\\d{1,4}/) &&
                        (text.match(/\\$\\d+/) || text.match(/\\d+\\.?\\d*K/))) {
                        container = el;
                        break;
                    }
                    el = el.parentElement;
                    depth++;
                }
            }
            if (container && !containers.includes(container)) {
                containers.push(container);
            }
        }
        
        // Strategy 2: Find all divs with class containing "flight" or "result"
        if (containers.length < 10) {
            const divs = document.querySelectorAll('div[class*="flight"], div[class*="result"], div[class*="slice"]');
            for (let div of divs) {
                const text = (div.innerText || '').substring(0, 300);
                if (text.match(/\\d{1,2}:\\d{2}\\s*(?:AM|PM)/gi) && 
                    text.match(/[A-Z]{2}\\s*\\d{1,4}/) &&
                    (text.match(/\\$\\d+/) || text.match(/\\d+\\.?\\d*K/)) &&
                    !containers.includes(div)) {
                    containers.push(div);
                }
            }
        }
        
        const results = [];
        const seen = new Set();
        
        for (let container of containers) {
            try {
                const text = container.innerText || '';
                
                // Extract times
                const times = text.match(/(\\d{1,2}:\\d{2}\\s*(?:AM|PM))/gi);
                if (!times || times.length < 2) continue;
                
                const depTime = times[0];
                const arrTime = times[1];
                
                // Extract flight number - try multiple methods
                let flightNo = '';
                
                // Method 1: Look for element with flight-number class
                const flightNumEl = container.querySelector('.flight-number, [class*="flight-number"], .flight-details [class*="number"]');
                if (flightNumEl) {
                    const fnText = flightNumEl.innerText || '';
                    const match = fnText.match(/([A-Z]{2})\\s*(\\d{1,4})/);
                    if (match) flightNo = match[1] + match[2];
                }
                
                // Method 2: Search in text
                if (!flightNo) {
                    const match = text.match(/\\b([A-Z]{2})\\s+(\\d{1,4})\\b/);
                    if (match) flightNo = match[1] + match[2];
                }
                
                if (!flightNo) continue;
                
                // Deduplicate by flight number + departure time
                const key = flightNo + '|' + depTime;
                if (seen.has(key)) continue;
                seen.add(key);
                
                // Extract price or miles
                let price = null;
                let miles = null;
                
                // Price
                const priceMatch = text.match(/\\$(\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?)/);
                if (priceMatch) {
                    price = parseFloat(priceMatch[1].replace(/,/g, ''));
                }
                
                // Miles - be more aggressive in matching
                const milesMatch = text.match(/(\\d+(?:\\.\\d+)?)K|([0-9,]+)\\s*(?:mile|point)/i);
                if (milesMatch) {
                    if (milesMatch[1]) {
                        miles = Math.round(parseFloat(milesMatch[1]) * 1000);
                    } else if (milesMatch[2]) {
                        miles = parseInt(milesMatch[2].replace(/,/g, ''));
                    }
                }
                
                if (flightNo && depTime && arrTime && (price !== null || miles !== null)) {
                    results.push({
                        flight_number: flightNo,
                        departure_time: depTime,
                        arrival_time: arrTime,
                        price: price,
                        miles: miles
                    });
                }
            } catch (e) {
                console.error('Legacy parse error:', e);
            }
        }
        
        return results;
        """
        
        try:
            raw = self.driver.execute_script(js_code)
        except Exception as e:
            print(f"  ⚠ JS execution error: {e}")
            raw = []
        
        print(f"  Legacy parser found {len(raw)} raw results")
        return self._format_flights(raw, is_award)
    
    def _format_flights(self, raw: List[Dict], is_award: bool) -> List[Dict]:
        """Convert raw parsed data to proper format"""
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
            elif not is_award and f.get("price"):
                flights.append({
                    "flight_number": f["flight_number"],
                    "departure_time": f["departure_time"],
                    "arrival_time": f["arrival_time"],
                    "cash_price_usd": float(f["price"]),
                    "taxes_fees_usd": 5.60
                })
        
        # Deduplicate
        seen = set()
        unique = []
        for f in flights:
            key = (f["flight_number"], f["departure_time"], 
                   f.get("cash_price_usd"), f.get("points_required"))
            if key not in seen:
                seen.add(key)
                unique.append(f)
        
        print(f"  Extracted {len(unique)} flights")
        _dump(unique, f"parsed_{'award' if is_award else 'cash'}_flights")
        
        return unique
    
    def search_flights(self, origin: str, dest: str, date: str, redeem_miles: bool) -> List[Dict]:
        """Complete search flow"""
        self.fill_search_form(origin, dest, date, redeem_miles)
        self.submit_search()
        
        if not self.wait_for_results(is_award=redeem_miles):
            return []
        
        return self.parse_flights(is_award=redeem_miles)
    
    def close(self):
        try:
            time.sleep(2)
            self.driver.quit()
        except:
            pass
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
        
        # Return home
        print("\n🏠 Returning home...")
        scraper.driver.get("https://www.aa.com/")
        time.sleep(3)
        scraper._dismiss_popups()
        
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
                "source": "app-slice-details",
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
        print(f"Debug files:   data/debug/")
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
        print("\nSample flights with CPP:")
        for flight in result["flights"][:5]:
            print(f"  {flight['flight_number']}: ${flight['cash_price_usd']:.2f} or {flight['points_required']:,} pts → CPP: {flight['cpp']}")
    else:
        print("\n⚠ No matching flights found with both cash and award pricing")
if __name__ == "__main__":
    main()