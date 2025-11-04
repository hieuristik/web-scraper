#!/usr/bin/env python3
# src/crawler_api.py
# Improved AA scraper with better handling for:
# - Multi-leg flights (using first leg's departure, last leg's arrival)
# - Award page button-wrapped data
# - Precise deduplication
# - Better matching between cash and award flights
# - Robust stripping of "+N" day suffixes from times
# - More flexible cash product/price discovery

import os, re, json, time, sys, pathlib, types, subprocess
from typing import Any, Dict, List, Optional, Tuple

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
        def _cmp(s,o,op): ov=o.v if isinstance(o,o.__class__) else _parse(str(o)); return op(s.v, ov)
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
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
except Exception as e:
    raise RuntimeError(f"Missing selenium / undetected_chromedriver: {e}")

# optional: BeautifulSoup for DOM fallback parsing
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

# Regex helpers
FLIGHT_RE = re.compile(r'\b([A-Z]{2}\s?\d{1,4})\b')
TIME_RE = re.compile(r'(\d{1,2}:\d{2}\s*(?:AM|PM))', re.IGNORECASE)
PRICE_RE = re.compile(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)')
MILES_K_RE = re.compile(r'(\d+(?:\.\d+)?)\s*K\b', re.IGNORECASE)
MILES_COMMA_RE = re.compile(r'(\d{1,3}(?:,\d{3})+)\s*(?:mile|miles|point|points)?', re.IGNORECASE)

def mmddyyyy(date_iso: str) -> str:
    y, m, d = date_iso.split("-")
    return f"{int(m):02d}/{int(d):02d}/{y}"

def calculate_cpp(cash: float, taxes: float, points: int) -> float:
    return 0.0 if not points else round(((cash - taxes)/points)*100, 2)

def normalize_flight_number(fn: str) -> str:
    """Normalize flight number by removing spaces and ensuring consistent format"""
    if not fn:
        return ""
    return re.sub(r'\s+', '', fn.upper())

def extract_first_flight_number(flight_str: str) -> str:
    """
    Extract only the first flight number from a string like 'AA 1956, AA 2848'.
    Returns normalized flight number (e.g., 'AA1956').
    """
    if not flight_str:
        return ""
    
    # Split by comma and take first flight
    first_flight = flight_str.split(',')[0].strip()
    
    # Normalize it
    return normalize_flight_number(first_flight)

def merge_and_dedup(cash: Optional[List[Dict]], award: Optional[List[Dict]]) -> List[Dict]:
    """
    Match cash and award flights by flight number and departure time.
    Enhanced matching logic with normalization.
    """
    if cash is None:
        cash = []
    if award is None:
        award = []
    
    print(f"\n🔄 Merging {len(cash)} cash flights with {len(award)} award flights...")
    
    merged = []
    used_award = set()
    unmatched_cash = []

    for c in cash:
        cash_fn = normalize_flight_number(c.get("flight_number", ""))
        cash_dep = c.get("departure_time", "").strip()
        
        best_match = None
        best_idx = None

        for idx, a in enumerate(award):
            if idx in used_award:
                continue
            
            award_fn = normalize_flight_number(a.get("flight_number", ""))
            award_dep = a.get("departure_time", "").strip()

            if cash_fn == award_fn and cash_dep == award_dep:
                best_match = a
                best_idx = idx
                break

        if best_match:
            item = {
                "flight_number": cash_fn,
                "departure_time": cash_dep,
                "arrival_time": c.get("arrival_time", "").strip(),
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
        else:
            unmatched_cash.append(c)

    # Log unmatched for debugging
    if unmatched_cash:
        print(f"⚠ {len(unmatched_cash)} cash flights without award match")
        _dump(unmatched_cash, "unmatched_cash")
    
    unused_award = [a for idx, a in enumerate(award) if idx not in used_award]
    if unused_award:
        print(f"⚠ {len(unused_award)} award flights without cash match")
        _dump(unused_award, "unmatched_award")

    # Final deduplication by unique key
    seen = set()
    unique = []
    for m in merged:
        key = (m["flight_number"], m["departure_time"], m["cash_price_usd"], m["points_required"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    print(f"✅ Successfully merged {len(unique)} unique flight pairs")
    return unique

def amount_to_int(s: Optional[str]) -> Optional[int]:
    """Convert award amounts like '12.5K' or '12,500' to integer"""
    if not s:
        return None
    s = s.strip().upper().replace(',', '').replace('$', '')
    s = re.sub(r'^[^\d\.\-]+', '', s)
    
    # Handle K format (12.5K -> 12500)
    m = re.match(r'^([\d\.]+)K$', s)
    if m:
        try:
            return int(round(float(m.group(1)) * 1000))
        except:
            return None
    
    # Handle plain number
    try:
        return int(float(s))
    except:
        return None

# --- New helpers for stripping "+N" suffix and robust price extraction -----

def _strip_plus_day_suffix(time_str: Optional[str]) -> Optional[str]:
    """
    Remove trailing ' +N' day-offset markers (e.g. '6:00 AM +1') and any
    following tooltip text. Leaves the normal time unchanged.
    Examples:
      '6:00 AM +1' -> '6:00 AM'
      '6:00 AM +1 (next day)' -> '6:00 AM'
    """
    if not time_str:
        return time_str
    s = re.sub(r'\s*\(.*?\)', '', time_str)     # remove parenthetical tooltip parts
    s = re.sub(r'\s*\+\d+\b.*$', '', s)         # remove " +1" (or +2, etc) and anything after
    return s.strip()

def _extract_price_from_element_text(txt: str) -> Optional[float]:
    """
    Heuristic to extract a USD cash price from a chunk of text.
    Returns float or None.
    """
    if not txt:
        return None
    # Look for $###.## or $###
    m = PRICE_RE.search(txt)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except:
            pass
    # If no $ sign, look for a plain numeric that seems like a fare (>=50)
    m2 = re.search(r'(\d{2,5}(?:\.\d{1,2})?)', txt.replace(',', ''))
    if m2:
        try:
            val = float(m2.group(1))
            if val >= 50:
                return val
        except:
            pass
    return None

# ---------------------------------------------------------------------------

def parse_award_flights_structured(driver) -> List[Dict]:
    """
    Parse award flights from structured DOM (Pass 2).
    Award page has different structure with cabin columns.
    This version strips '+N' from times and is defensive about missing nodes.
    """
    flights = []
    
    try:
        # Wait for award results grid
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "results-grid-container"))
        )
        
        # Find all flight detail sections (left side)
        flight_sections = driver.find_elements(By.CSS_SELECTOR, "app-slice-info-desktop")
        
        # Find all product sections (right side with prices)
        product_sections = driver.find_elements(By.CSS_SELECTOR, "app-available-products-desktop")
        
        print(f"  Found {len(flight_sections)} flight blocks")
        
        if len(flight_sections) != len(product_sections):
            print(f"  ⚠ Mismatch: {len(flight_sections)} flights vs {len(product_sections)} product sections")
        
        for idx, (flight_elem, product_elem) in enumerate(zip(flight_sections, product_sections)):
            try:
                # Parse flight details (left side)
                try:
                    origin_elem = flight_elem.find_element(By.CSS_SELECTOR, ".origin .city-code")
                    origin = origin_elem.text.strip()
                except Exception:
                    origin = ""
                try:
                    dest_elem = flight_elem.find_element(By.CSS_SELECTOR, ".destination .city-code")
                    destination = dest_elem.text.strip()
                except Exception:
                    destination = ""
                try:
                    dep_time_elem = flight_elem.find_element(By.CSS_SELECTOR, ".origin .flt-times")
                    departure_time = _strip_plus_day_suffix(dep_time_elem.text.strip())
                except Exception:
                    # Fallback to first time found in text
                    flight_text = flight_elem.text or ""
                    times = TIME_RE.findall(flight_text)
                    departure_time = times[0].strip() if times else ""
                try:
                    arr_time_elem = flight_elem.find_element(By.CSS_SELECTOR, ".destination .flt-times")
                    arrival_time = _strip_plus_day_suffix(arr_time_elem.text.strip().split('\n')[0].strip())
                except Exception:
                    # Fallback to last time in text
                    flight_text = flight_elem.text or ""
                    times = TIME_RE.findall(flight_text)
                    arrival_time = times[-1].strip() if times else ""
                try:
                    duration_elem = flight_elem.find_element(By.CSS_SELECTOR, ".duration")
                    duration = duration_elem.text.strip()
                except Exception:
                    duration = ""
                try:
                    stops_elem = flight_elem.find_element(By.CSS_SELECTOR, ".stops")
                    stops_text = stops_elem.text.strip()
                except Exception:
                    stops_text = ""
                
                # Parse stops
                if "Nonstop" in stops_text or "Nonstop" in duration:
                    stops = 0
                elif "1 stop" in stops_text:
                    stops = 1
                elif "2 stops" in stops_text:
                    stops = 2
                else:
                    stops = None
                
                # Extract flight numbers (legs)
                flight_numbers = []
                try:
                    leg_elems = flight_elem.find_elements(By.CSS_SELECTOR, ".leg-info")
                    if not leg_elems:
                        leg_elems = flight_elem.find_elements(By.CSS_SELECTOR, ".segment, .leg")
                    for leg in leg_elems:
                        try:
                            flt_num_elem = leg.find_element(By.CSS_SELECTOR, ".flight-number")
                            flt_num = flt_num_elem.text.strip()
                        except Exception:
                            # fallback to any text that matches flight regex
                            txt = leg.text or ""
                            m = FLIGHT_RE.search(txt)
                            flt_num = m.group(1) if m else ""
                        if flt_num:
                            flight_numbers.append(flt_num)
                except Exception:
                    pass
                
                # Only use the FIRST flight number for matching
                if flight_numbers:
                    flight_number = extract_first_flight_number(flight_numbers[0])
                else:
                    flight_number = "Unknown"
                
                # Parse award prices (right side)
                award_miles = None
                award_fees = None
                
                try:
                    # Find all price buttons (defensive)
                    price_buttons = product_elem.find_elements(By.CSS_SELECTOR, "button.btn-flight, .btn-flight, button")
                    
                    for btn in price_buttons:
                        try:
                            hidden_type = ""
                            try:
                                hidden_type = btn.find_element(By.CSS_SELECTOR, ".hidden-product-type").text.strip()
                            except Exception:
                                # sometimes type is in an attribute or aria-label
                                try:
                                    hidden_type = btn.get_attribute("aria-label") or ""
                                except:
                                    hidden_type = ""
                            
                            # Normalize type name
                            if hidden_type:
                                ht = hidden_type.strip().lower()
                            else:
                                ht = ""
                            
                            # Prefer main cabin / coach naming variants
                            if ht in ("main", "main cabin", "maincabin", "coach", "main cabin (coach)"):
                                # Get the miles amount
                                miles_text = ""
                                try:
                                    miles_elem = btn.find_element(By.CSS_SELECTOR, ".per-pax-amount, .per-pax")
                                    miles_text = miles_elem.text.strip()
                                except Exception:
                                    miles_text = btn.text or ""
                                
                                # Parse miles (e.g., "18K" -> 18000)
                                miles_match = re.search(r'([\d\.]+)K', miles_text, re.IGNORECASE)
                                if miles_match:
                                    miles_value = float(miles_match.group(1))
                                    award_miles = int(miles_value * 1000)
                                else:
                                    # maybe a full number like 18,000
                                    num_match = re.search(r'(\d{1,3}(?:[,\d]{0,})+)', miles_text.replace(' ', ''))
                                    if num_match:
                                        award_miles = amount_to_int(num_match.group(1))
                                
                                # Get the fees
                                try:
                                    fees_elem = btn.find_element(By.CSS_SELECTOR, ".per-pax-addon, .fees, .addon")
                                    fees_text = fees_elem.text.strip()
                                    fees_match = re.search(r'\$?([\d.]+)', fees_text)
                                    if fees_match:
                                        award_fees = float(fees_match.group(1))
                                except:
                                    award_fees = award_fees
                                
                                # Found a main-cabin candidate; break if miles found
                                if award_miles is not None:
                                    break
                        except Exception:
                            continue
                    
                except Exception as e:
                    print(f"    ⚠ Flight {idx}: Could not parse award prices: {e}")
                
                # Only add flight if we found award pricing
                if award_miles is not None:
                    flights.append({
                        "flight_number": flight_number,
                        "departure_time": departure_time,
                        "arrival_time": arrival_time,
                        "points_required": award_miles,
                        "taxes_fees_usd": award_fees if award_fees else 5.60
                    })
                
            except Exception as e:
                print(f"    ⚠ Flight {idx}: Parsing error: {e}")
                continue
        
        print(f"  Extracted {len(flights)} flights")
        
    except Exception as e:
        print(f"  ❌ Award structured parsing failed: {e}")
    
    return flights

def parse_cash_flights_structured(driver) -> List[Dict]:
    """
    Parse cash flights from structured DOM (Pass 1).
    Uses Selenium to extract departure and arrival times correctly and
    more robustly locate cash product/price nodes.
    """
    flights = []
    
    try:
        # Wait for results grid
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "app-slice-details, app-slice-info-desktop"))
        )
        
        # Find all flight detail sections
        flight_sections = driver.find_elements(By.CSS_SELECTOR, "app-slice-details, app-slice-info-desktop")
        
        # Find all product sections (right side with prices)
        product_sections = driver.find_elements(By.CSS_SELECTOR, "app-available-products-desktop, app-product-groups, .product-groups")
        
        print(f"  Found {len(flight_sections)} flight blocks")
        
        if len(flight_sections) != len(product_sections):
            print(f"  ⚠ Mismatch: {len(flight_sections)} flights vs {len(product_sections)} product sections")
        
        for idx, (flight_elem, product_elem) in enumerate(zip(flight_sections, product_sections)):
            try:
                # Extract departure time (origin)
                try:
                    dep_time_elem = flight_elem.find_element(By.CSS_SELECTOR, ".origin .flt-times")
                    departure_time = _strip_plus_day_suffix(dep_time_elem.text.strip())
                except Exception:
                    # Fallback to searching for time patterns
                    flight_text = flight_elem.text or ""
                    times = TIME_RE.findall(flight_text)
                    departure_time = times[0].strip() if times else ""
                
                # Extract arrival time (destination)
                try:
                    arr_time_elem = flight_elem.find_element(By.CSS_SELECTOR, ".destination .flt-times")
                    arrival_time = _strip_plus_day_suffix(arr_time_elem.text.strip().split('\n')[0].strip())
                except Exception:
                    # Fallback to searching for time patterns
                    flight_text = flight_elem.text or ""
                    times = TIME_RE.findall(flight_text)
                    arrival_time = times[-1].strip() if len(times) >= 1 else ""
                
                # Extract flight numbers
                flight_numbers = []
                try:
                    leg_elems = flight_elem.find_elements(By.CSS_SELECTOR, ".leg-info .flight-number, .flight-number, .leg .flight-number")
                    for leg in leg_elems:
                        flt_num = leg.text.strip()
                        if flt_num:
                            flight_numbers.append(flt_num)
                except:
                    pass
                
                # If no flight numbers via class, try regex on flight text
                if not flight_numbers:
                    flight_text = flight_elem.text or ""
                    matches = FLIGHT_RE.findall(flight_text)
                    flight_numbers = [m for m in matches if m]
                
                # Only use the FIRST flight number for matching
                if flight_numbers:
                    flight_number = extract_first_flight_number(flight_numbers[0])
                else:
                    # If we cannot find a flight number, skip (can't match)
                    continue
                
                # Extract cash price from product container with multiple fallbacks
                cash_price = None
                
                try:
                    # 1) Look for explicit per-pax amount nodes (common in cash HTML)
                    try:
                        per_pax_nodes = product_elem.find_elements(By.CSS_SELECTOR, ".per-pax-amount, .per-pax, .price .per-pax-amount")
                        for p in per_pax_nodes:
                            val = _extract_price_from_element_text(p.text or "")
                            if val and val >= 50:
                                cash_price = val
                                break
                    except Exception:
                        per_pax_nodes = []
                    
                    # 2) If not found, inspect product buttons for $ in text
                    if cash_price is None:
                        price_buttons = product_elem.find_elements(By.CSS_SELECTOR, "button.btn-flight, .btn-flight, button, a")
                        for btn in price_buttons:
                            txt = btn.text or ""
                            val = _extract_price_from_element_text(txt)
                            if val and val >= 50:
                                cash_price = val
                                break
                    
                    # 3) Fallback: search the whole product container text
                    if cash_price is None:
                        container_text = product_elem.text or ""
                        val = _extract_price_from_element_text(container_text)
                        if val and val >= 50:
                            cash_price = val
                except Exception as e:
                    print(f"    ⚠ Flight {idx}: Could not parse cash price: {e}")
                
                # Only add flight if we found pricing and times
                if cash_price is not None and departure_time and arrival_time:
                    flights.append({
                        "flight_number": flight_number,
                        "departure_time": departure_time,
                        "arrival_time": arrival_time,
                        "cash_price_usd": cash_price,
                        "taxes_fees_usd": 5.60
                    })
                
            except Exception as e:
                print(f"    ⚠ Flight {idx}: Parsing error: {e}")
                continue
        
        print(f"  Extracted {len(flights)} flights")
        
    except Exception as e:
        print(f"  ❌ Cash structured parsing failed: {e}")
    
    return flights

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
                document.querySelectorAll('.onetrust-pc-dark-filter, .onetrust-banner-sdk, adc-cookie-banner').forEach(n=>{ try{ n.remove() }catch(e){}});
            """)
            time.sleep(0.4)
        except:
            pass

    def open_home(self):
        print("🌐 Loading AA.com...")
        self.driver.get("https://www.aa.com/")
        time.sleep(3.0)
        self._dismiss_popups()
        _save_html(self.driver, "home")
        print("✓ Homepage loaded")

    def fill_search_form(self, origin: str, dest: str, date: str, redeem_miles: bool):
        """Fill the search form with exact selectors and human-like typing"""
        print(f"📝 Filling form ({'Award' if redeem_miles else 'Cash'} mode)...")
        self._dismiss_popups()
        time.sleep(0.3)

        # One-way
        try:
            self.driver.find_element(By.CSS_SELECTOR, "label[for='flightSearchForm.tripType.oneWay']").click()
            time.sleep(0.22)
            print("✓ One-way selected")
        except Exception:
            try:
                ow = self.driver.find_element(By.ID, "flightSearchForm.tripType.oneWay")
                self.driver.execute_script("arguments[0].click()", ow)
                time.sleep(0.22)
            except Exception as e:
                print(f"  ⚠ One-way selection failed: {e}")

        # Redeem miles toggle
        try:
            cb = self.driver.find_element(By.ID, "flightSearchForm.tripType.redeemMiles")
            checked = cb.is_selected()
            if redeem_miles and not checked:
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
            print(f"✓ Redeem miles {'enabled' if redeem_miles else 'disabled'}")
        except Exception:
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

        # Date
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
        """Submit the search"""
        self._dismiss_popups()
        try:
            btn = self.driver.find_element(By.XPATH, "//button[contains(., 'Search') or @type='submit']")
            btn.click()
            print("✓ Search submitted")
        except Exception:
            try:
                dest = self.driver.find_element(By.NAME, "destinationAirport")
                dest.send_keys(Keys.ENTER)
                print("↩ Search submitted (Enter)")
            except Exception as e:
                print(f"  ⚠ Submit failed: {e}")
        time.sleep(1.5)

    def _coax_lazy_load(self):
        """Scroll and trigger lazy loading"""
        try:
            for _ in range(3):
                self.driver.execute_script("window.scrollBy(0, window.innerHeight/3);")
                time.sleep(0.35)
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
        except Exception:
            pass

    def wait_for_results(self, timeout=60, is_award: bool = False, expected_min=10) -> bool:
        """Wait for results to load"""
        print("⏳ Waiting for results...")
        start = time.time()
        last_count = 0
        
        while time.time() - start < timeout:
            try:
                counts = self.driver.execute_script("""
                    const modern = document.querySelectorAll('app-slice-details').length;
                    const legacy = document.querySelectorAll('div[class*="result"], div[class*="slice"], div[class*="flight"]').length;
                    return {modern: modern, legacy: legacy};
                """)
                modern = int(counts.get("modern", 0))
                legacy = int(counts.get("legacy", 0))
            except Exception:
                modern = 0
                legacy = 0

            if modern >= expected_min or legacy >= expected_min:
                total = max(modern, legacy)
                print(f"✓ Found {total} flight elements")
                time.sleep(2.0)
                return True

            try:
                src = self.driver.page_source or ""
            except Exception:
                src = ""

            times_found = len(TIME_RE.findall(src))
            flights_found = len(FLIGHT_RE.findall(src))
            price_found = len(PRICE_RE.findall(src))
            miles_found = len(MILES_K_RE.findall(src)) + len(MILES_COMMA_RE.findall(src))

            if is_award:
                if flights_found >= expected_min and (times_found >= expected_min or miles_found >= expected_min):
                    print(f"✓ Found {flights_found} flights (text heuristics)")
                    time.sleep(1.5)
                    return True
            else:
                if flights_found >= expected_min and (times_found >= expected_min or price_found >= expected_min):
                    print(f"✓ Found {flights_found} flights (text heuristics)")
                    time.sleep(1.5)
                    return True

            current_count = max(modern, legacy, flights_found)
            if current_count > last_count:
                last_count = current_count
            else:
                self._coax_lazy_load()

            time.sleep(1.0)

        print("⚠ Timeout waiting for results")
        return False

    def parse_flights_structured(self, is_award: bool) -> List[Dict]:
        """
        Parse flights using structured approach.
        Routes to specialized parsers for cash vs award flights.
        """
        print(f"📊 Parsing {'award' if is_award else 'cash'} flights (structured)...")
        
        # Use specialized parsers
        if is_award:
            return parse_award_flights_structured(self.driver)
        else:
            return parse_cash_flights_structured(self.driver)

    def parse_flights(self, is_award: bool) -> List[Dict]:
        """Main parsing dispatcher"""
        _save_html(self.driver, f"{'award' if is_award else 'cash'}_results")
        
        # Try structured parsing first
        flights = self.parse_flights_structured(is_award)
        
        if not flights or len(flights) < 10:
            print(f"  ⚠ Structured parsing found only {len(flights)} flights, expected ~40")
        
        return flights

    def search_flights(self, origin: str, dest: str, date: str, redeem_miles: bool) -> List[Dict]:
        """Complete search flow"""
        self.fill_search_form(origin, dest, date, redeem_miles)
        self.submit_search()
        ok = self.wait_for_results(timeout=90 if redeem_miles else 60, is_award=redeem_miles, expected_min=10)
        
        if not ok:
            print("⚠ Results not detected, attempting recovery...")
            try:
                self._coax_lazy_load()
                time.sleep(2.0)
                ok = self.wait_for_results(timeout=45, is_award=redeem_miles, expected_min=8)
            except Exception:
                ok = False

        if not ok:
            print("⚠ Failed to detect results, returning empty list")
            return []

        return self.parse_flights(is_award=redeem_miles)

    def close(self):
        try:
            time.sleep(1.0)
            if self.driver:
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
                "cash_count": len(cash_flights) if cash_flights else 0,
                "award_count": len(award_flights) if award_flights else 0,
                "merged_count": len(merged)
            },
            "flights": merged,
            "total_results": len(merged)
        }

        pathlib.Path("output.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

        print(f"\n{'='*60}")
        print("✅ SUCCESS")
        print(f"{'='*60}")
        print(f"Cash flights:  {len(cash_flights) if cash_flights else 0}")
        print(f"Award flights: {len(award_flights) if award_flights else 0}")
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
    ap.add_argument("--origin", required=True, help="Origin airport code")
    ap.add_argument("--destination", required=True, help="Destination airport code")
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

    if result["flights"]:
        print("\nSample flights:")
        sample = result["flights"][:5]
        for flight in sample:
            cp = flight.get("cash_price_usd")
            pts = flight.get("points_required")
            cpp = flight.get("cpp")
            if cp is None or pts is None:
                print(f"  {flight.get('flight_number')} - partial data")
            else:
                print(f"  {flight['flight_number']}: ${cp:.2f} or {pts:,} pts → CPP: {cpp}")

if __name__ == "__main__":
    main()