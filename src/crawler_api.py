#!/usr/bin/env python3
# src/crawler_api.py
# Improved AA scraper with better handling for:
# - Multi-leg flights (using first leg's departure, last leg's arrival)
# - Award page button-wrapped data
# - Precise deduplication
# - Better matching between cash and award flights
# - Robust stripping of "+N" day suffixes from times
# - More flexible cash product/price discovery
# - Fix: cash pass arrival/departure mismatch (fall back to nearby text and innerText)
# Additionally: integrated HTML-first-pass cash parser utilities (BeautifulSoup fallback)
# so you have one file to copy-paste over.

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

def _element_inner_text(el) -> str:
    """Return the element's innerText reliably (Selenium)"""
    try:
        txt = el.get_attribute("innerText")
        if txt:
            return txt
    except Exception:
        pass
    try:
        return el.text or ""
    except Exception:
        return ""

# Helper: find first selector that exists inside a Selenium element
def _find_first(parent, selectors: List[str]):
    for sel in selectors:
        try:
            return parent.find_element(By.CSS_SELECTOR, sel)
        except Exception:
            continue
    return None

# ---------------------------------------------------------------------------
# ----------------- Integrated parse_cash (HTML/BS4) + merge helpers ----------------
# ---------------------------------------------------------------------------

def _parse_money_from_str(s: Optional[str]) -> Optional[float]:
    """Parse money using PRICE_RE fallback to numeric heuristics."""
    if not s:
        return None
    m = PRICE_RE.search(s)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except:
            pass
    # fallback numeric
    m2 = re.search(r'(\d{2,6}(?:\.\d{1,2})?)', s.replace(',', ''))
    if m2:
        try:
            val = float(m2.group(1))
            if val >= 1:
                return val
        except:
            pass
    return None

def parse_cash_from_html(html_content: str, default_taxes: float = 5.60) -> List[Dict]:
    """
    Parse cash flights from a saved AA results HTML string using BeautifulSoup.
    This is the HTML-first-pass parser (useful when Selenium pass fails or for offline
    parsing of saved pages). Returns list of dicts with the same shape used elsewhere.

    Requires BeautifulSoup (bs4). If it's not available, raises RuntimeError.
    """
    if BeautifulSoup is None:
        raise RuntimeError("BeautifulSoup not available. Install with: pip install beautifulsoup4")

    soup = BeautifulSoup(html_content, "html.parser")
    slices = []

    # Look for app-slice-details by id or tag
    slice_blocks = soup.select("[id^=slice-details]")
    if not slice_blocks:
        slice_blocks = soup.select("app-slice-details")
    if not slice_blocks:
        # fallback to result-like containers
        slice_blocks = soup.select(".result, .slice, .flight, .app-slice-details")

    for slice_tag in slice_blocks:
        try:
            # try to find a flight-card-like block inside slice
            flight_card = slice_tag.select_one(".matrix-flight-card") or slice_tag.select_one("app-matrix-flight-card") or slice_tag
            if not flight_card:
                continue

            # Try multiple selectors for departure/arrival times
            dep = None
            arr = None

            dep_sel_candidates = [".origin .flt-times", ".origin .time", ".origin .time-only", ".origin .time-range", ".origin .time-small", ".flt-times", ".time", ".flt-time"]
            arr_sel_candidates = [".destination .flt-times", ".destination .time", ".destination .time-only", ".destination .time-range", ".destination .time-small", ".flt-times", ".time", ".flt-time"]

            for sel in dep_sel_candidates:
                t = flight_card.select_one(sel)
                if t:
                    dep = t.get_text(" ", strip=True)
                    break

            for sel in arr_sel_candidates:
                t = flight_card.select_one(sel)
                if t:
                    arr = t.get_text(" ", strip=True)
                    break

            # fallback to regex inside the card
            txt = flight_card.get_text(" ", strip=True) or ""
            times = TIME_RE.findall(txt)
            if not dep and times:
                dep = times[0].strip()
            if not arr and len(times) >= 2:
                arr = times[-1].strip()
            # if only one time found, keep as both (still better than empty)
            if not arr and dep and len(times) == 1:
                arr = dep

            dep = _strip_plus_day_suffix(dep) if dep else ""
            arr = _strip_plus_day_suffix(arr) if arr else ""

            # Flight number: prefer .flight-number, else regex
            flt_tag = flight_card.select_one(".flight-number")
            flight_number = None
            if flt_tag:
                flight_number = flt_tag.get_text(" ", strip=True)
            else:
                # search within card text
                m = FLIGHT_RE.search(txt)
                if m:
                    flight_number = m.group(1)

            if not flight_number:
                # can't match without flight number
                continue

            flight_number = extract_first_flight_number(flight_number)

            # Price extraction: prefer product-groups inside same slice
            cash_price = None
            # check MAIN product-group style
            main_price_tag = slice_tag.select_one(".product-groups .btn-flight.MAIN .per-pax-amount")
            if main_price_tag:
                cash_price = _parse_money_from_str(main_price_tag.get_text(" ", strip=True))
            if cash_price is None:
                any_price_tag = slice_tag.select_one(".product-groups .per-pax-amount, .per-pax-amount, .price .per-pax-amount, .price")
                if any_price_tag:
                    cash_price = _parse_money_from_str(any_price_tag.get_text(" ", strip=True))
            if cash_price is None:
                # fallback: search the whole slice for $ pattern
                m = PRICE_RE.search(slice_tag.get_text(" ", strip=True))
                if m:
                    try:
                        cash_price = float(m.group(1).replace(',', ''))
                    except:
                        cash_price = None
            if cash_price is None:
                # fallback numeric heuristics on text
                cash_price = _parse_money_from_str(txt)

            # Only include if we have reasonable data
            if cash_price is not None and dep and arr:
                slices.append({
                    "flight_number": flight_number,
                    "departure_time": dep,
                    "arrival_time": arr,
                    "cash_price_usd": float(cash_price),
                    "taxes_fees_usd": float(default_taxes) if default_taxes is not None else 5.60
                })
            else:
                # Save context to debug later
                ctx = {
                    "flight_inner": (flight_card.get_text(" ", strip=True) or "")[:1000],
                    "slice_inner": (slice_tag.get_text(" ", strip=True) or "")[:1000],
                    "departure_time": dep,
                    "arrival_time": arr,
                    "cash_price": cash_price
                }
                _dump(ctx, f"parse_cash_html_fail_{len(slices)}")
        except Exception as e:
            # don't crash on one slice
            print(f"warning: parse_cash_from_html slice error: {e}")
            continue

    return slices

def merge_parsed_into_output_json(parsed_cash_list: List[Dict], output_json_path: str) -> Tuple[Dict, List[Dict]]:
    """
    Merge a parsed cash list (from parse_cash_from_html) into an existing output.json
    by matching (flight_number, departure_time). Returns updated output dict and list
    of unmatched parsed entries.

    Matching uses normalized flight numbers and stripped departure_time.
    """
    try:
        with open(output_json_path, "r", encoding="utf-8") as f:
            out = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Could not read {output_json_path}: {e}")

    parsed_lookup = {}
    # Multiple parsed entries per key -> keep the lowest price
    for p in parsed_cash_list:
        key = (normalize_flight_number(p.get("flight_number", "")), _strip_plus_day_suffix((p.get("departure_time") or "").strip() ))
        existing = parsed_lookup.get(key)
        if existing is None:
            parsed_lookup[key] = p
        else:
            # choose the lowest non-null price
            try:
                cur_price = float(existing.get("cash_price_usd")) if existing.get("cash_price_usd") is not None else None
                new_price = float(p.get("cash_price_usd")) if p.get("cash_price_usd") is not None else None
                if new_price is not None and (cur_price is None or new_price < cur_price):
                    parsed_lookup[key] = p
            except Exception:
                parsed_lookup[key] = p

    matched_keys = set()
    flights = out.get("flights", [])
    for flight in flights:
        fnum = normalize_flight_number(flight.get("flight_number", ""))
        dtime = _strip_plus_day_suffix((flight.get("departure_time") or "").strip())
        key = (fnum, dtime)
        match = parsed_lookup.get(key)
        if match:
            if match.get("cash_price_usd") is not None:
                flight["cash_price_usd"] = match["cash_price_usd"]
            if match.get("taxes_fees_usd") is not None:
                flight["taxes_fees_usd"] = match["taxes_fees_usd"]
            matched_keys.add(key)

    unmatched = []
    for key, p in parsed_lookup.items():
        if key not in matched_keys:
            unmatched.append(p)

    return out, unmatched

# ---------------------------------------------------------------------------
# The rest of the file is the original Selenium-driven scraper and CLI.
# ---------------------------------------------------------------------------

def parse_award_flights_structured(driver) -> List[Dict]:
    """
    Parse award flights from structured DOM (Pass 2).
    Award page has different structure with cabin columns.
    This version restores the previous award parsing logic (flight_sections + product_sections zipped)
    because the per-slice rewrite broke award extraction in practice. Reverting ensures award pass
    returns data as it did previously.
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
                    flight_text = _element_inner_text(flight_elem) or ""
                    times = TIME_RE.findall(flight_text)
                    departure_time = times[0].strip() if times else ""
                try:
                    arr_time_elem = flight_elem.find_element(By.CSS_SELECTOR, ".destination .flt-times")
                    arrival_time = _strip_plus_day_suffix(arr_time_elem.text.strip().split('\n')[0].strip())
                except Exception:
                    # Fallback to last time in text
                    flight_text = _element_inner_text(flight_elem) or ""
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
                            txt = _element_inner_text(leg) or ""
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
                                    miles_text = _element_inner_text(btn) or ""
                                
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
    Uses a slice-by-slice pairing approach so times/prices are taken from the same container.
    """
    flights = []
    
    try:
        # Wait for results grid
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "app-slice-details, app-slice-info-desktop"))
        )

        # Find slice containers (robust)
        slice_selectors = [
            "app-slice-details",
            "app-slice-info-desktop",
            "div[id^='slice-details']",
            "div[class*='slice-details']",
            "div[class*='grid-x'][id^='flight-details']"
        ]
        slice_containers = []
        for sel in slice_selectors:
            try:
                found = driver.find_elements(By.CSS_SELECTOR, sel)
                if found:
                    slice_containers = found
                    break
            except Exception:
                continue
        if not slice_containers:
            slice_containers = driver.find_elements(By.CSS_SELECTOR, "app-slice-details, app-slice-info-desktop")

        print(f"  Found {len(slice_containers)} slice containers (cash)")

        for idx, slice_elem in enumerate(slice_containers):
            try:
                # Locate flight-info and product nodes inside this slice
                flight_elem = _find_first(slice_elem, [
                    ".matrix-flight-card", ".grid-x .cell.large-3.origin", ".origin", ".cell.large-3.origin", ".flight-details", "app-matrix-flight-card", "app-slice-info-desktop"
                ])
                if flight_elem is None:
                    flight_elem = slice_elem

                product_elem = _find_first(slice_elem, [
                    "app-available-products-desktop", ".product-groups", ".available-products", ".product-group", ".product-groups"
                ])
                if product_elem is None:
                    try:
                        parent = slice_elem.find_element(By.XPATH, "..")
                        product_elem = _find_first(parent, [
                            "app-available-products-desktop", ".product-groups", ".available-products"
                        ])
                    except Exception:
                        product_elem = None

                # Extract times from the flight element/slice only
                departure_time = ""
                arrival_time = ""
                try:
                    dep_time_elem = _find_first(flight_elem, [".origin .flt-times", ".origin .time", ".flt-times", ".time", ".flt-time"])
                    if dep_time_elem:
                        departure_time = _strip_plus_day_suffix(_element_inner_text(dep_time_elem).strip())
                except Exception:
                    departure_time = ""
                try:
                    arr_time_elem = _find_first(flight_elem, [".destination .flt-times", ".destination .time", ".flt-times", ".time", ".flt-time"])
                    if arr_time_elem:
                        arrival_time = _strip_plus_day_suffix(_element_inner_text(arr_time_elem).strip().split('\n')[0].strip())
                except Exception:
                    arrival_time = ""

                # If missing, inspect leg nodes inside the slice for first and last leg times
                if (not departure_time or not arrival_time):
                    try:
                        leg_elems = slice_elem.find_elements(By.CSS_SELECTOR, ".leg, .segment, .leg-info, .flight-leg")
                        if leg_elems:
                            if not departure_time:
                                firstDep = _find_first(leg_elems[0], [".origin .time", ".time", ".flt-times", ".departure"])
                                if firstDep:
                                    m = TIME_RE.search(_element_inner_text(firstDep))
                                    if m:
                                        departure_time = _strip_plus_day_suffix(m.group(0))
                                else:
                                    txt = _element_inner_text(leg_elems[0]) or ""
                                    m = TIME_RE.search(txt)
                                    if m:
                                        departure_time = _strip_plus_day_suffix(m.group(0))
                            if not arrival_time:
                                last = leg_elems[-1]
                                lastArr = _find_first(last, [".destination .time", ".time", ".flt-times", ".arrival"])
                                if lastArr:
                                    m2 = TIME_RE.search(_element_inner_text(lastArr))
                                    if m2:
                                        arrival_time = _strip_plus_day_suffix(m2.group(0))
                                else:
                                    txt = _element_inner_text(last) or ""
                                    m2 = TIME_RE.search(txt)
                                    if m2:
                                        arrival_time = _strip_plus_day_suffix(m2.group(0))
                    except Exception:
                        pass

                # Final fallback: regex on slice text only
                if not departure_time or not arrival_time:
                    collected = _element_inner_text(slice_elem) or ""
                    times = TIME_RE.findall(collected)
                    if len(times) >= 2:
                        if not departure_time:
                            departure_time = _strip_plus_day_suffix(times[0].strip())
                        if not arrival_time:
                            arrival_time = _strip_plus_day_suffix(times[-1].strip())
                    elif len(times) == 1:
                        if not departure_time:
                            departure_time = _strip_plus_day_suffix(times[0].strip())
                        if not arrival_time:
                            arrival_time = departure_time

                departure_time = departure_time.strip() if departure_time else ""
                arrival_time = arrival_time.strip() if arrival_time else ""

                # Extract flight numbers (first leg)
                flight_number = None
                try:
                    fn_el = _find_first(slice_elem, [".leg .flight-number", ".flight-number", ".segment .flight-number", ".flight-details [class*='number']"])
                    if fn_el:
                        flt_txt = _element_inner_text(fn_el)
                        m = FLIGHT_RE.search(flt_txt)
                        if m:
                            flight_number = extract_first_flight_number(m.group(1))
                    else:
                        # fallback to regex on slice text
                        ftext = _element_inner_text(slice_elem) or ""
                        m = FLIGHT_RE.search(ftext)
                        if m:
                            flight_number = extract_first_flight_number(m.group(1))
                except Exception:
                    flight_number = None

                if not flight_number:
                    # Can't match without flight number
                    _dump({
                        "idx": idx,
                        "note": "no_flight_number",
                        "slice_text": (_element_inner_text(slice_elem) or "")[:1000]
                    }, f"cash_fail_no_fn_{idx}")
                    continue

                # Extract cash price from product_elem only
                cash_price = None
                if product_elem is not None:
                    try:
                        per_pax_nodes = product_elem.find_elements(By.CSS_SELECTOR, ".per-pax-amount, .per-pax, .price .per-pax-amount")
                        for p in per_pax_nodes:
                            val = _extract_price_from_element_text(_element_inner_text(p) or "")
                            if val and val >= 10:
                                cash_price = val
                                break
                        if cash_price is None:
                            price_buttons = product_elem.find_elements(By.CSS_SELECTOR, "button.btn-flight, .btn-flight, button, a")
                            for btn in price_buttons:
                                txt = _element_inner_text(btn) or ""
                                val = _extract_price_from_element_text(txt)
                                if val and val >= 10:
                                    cash_price = val
                                    break
                        if cash_price is None:
                            container_text = _element_inner_text(product_elem) or ""
                            val = _extract_price_from_element_text(container_text)
                            if val and val >= 10:
                                cash_price = val
                    except Exception as e:
                        print(f"    ⚠ Flight {idx}: Could not parse cash price: {e}")
                else:
                    # No product element found inside slice - dump and continue
                    _dump({
                        "idx": idx,
                        "note": "no_product_elem",
                        "slice_text": (_element_inner_text(slice_elem) or "")[:1000]
                    }, f"cash_fail_no_product_{idx}")

                # Only append if we have times and price
                if cash_price is not None and departure_time and arrival_time:
                    flights.append({
                        "flight_number": flight_number,
                        "departure_time": departure_time,
                        "arrival_time": arrival_time,
                        "cash_price_usd": float(cash_price),
                        "taxes_fees_usd": 5.60
                    })
                else:
                    ctx = {
                        "idx": idx,
                        "flight_number": flight_number,
                        "departure_time": departure_time,
                        "arrival_time": arrival_time,
                        "cash_price": cash_price,
                        "flight_inner": (_element_inner_text(slice_elem) or "")[:1000],
                        "product_inner": (_element_inner_text(product_elem) or "")[:800 if product_elem is not None else 0]
                    }
                    _dump(ctx, f"cash_fail_{idx}")

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

    # ----------------------------
    # REPLACED: _parse_modern (per-slice-first JS)
    # ----------------------------
    def _parse_modern(self, is_award: bool) -> List[Dict]:
        """Parse modern app-slice-details structure (per-slice-first, robust leg handling)."""
        js_code = """
        // per-slice parsing: prefer precise selectors INSIDE each slice
        const slices = Array.from(document.querySelectorAll('app-slice-details, .slice, .result'));
        const results = [];

        for (const slice of slices) {
          try {
            // Try to find structured card inside the slice
            let card = slice.querySelector('.matrix-flight-card') || slice.querySelector('app-matrix-flight-card') || slice;

            // 1) times: prefer explicit origin/destination selectors in the card
            let depEl = card.querySelector('.origin .time, .origin .flt-times, .origin .flt-time, .origin .time-only');
            let arrEl = card.querySelector('.destination .time, .destination .flt-times, .destination .flt-time, .destination .time-only');

            let depTime = depEl ? depEl.innerText.trim() : null;
            let arrTime = arrEl ? arrEl.innerText.trim() : null;

            // 2) If multi-leg layout: inspect leg nodes for first and last leg times
            if ((!depTime || !arrTime) && card.querySelectorAll) {
              const legNodes = Array.from(card.querySelectorAll('.leg, .segment, .leg-info, .flight-leg'));
              if (legNodes.length) {
                // first leg -> departure
                if (!depTime) {
                  const firstDep = legNodes[0].querySelector('.origin .time, .flt-times, .time, .departure, .dep') || legNodes[0];
                  const fdTxt = (firstDep && firstDep.innerText) ? firstDep.innerText : '';
                  const m = fdTxt.match(/\\d{1,2}:\\d{2}\\s*(?:AM|PM)/i);
                  if (m) depTime = m[0];
                }
                // last leg -> arrival
                if (!arrTime) {
                  const last = legNodes[legNodes.length - 1];
                  const lastArr = last.querySelector('.destination .time, .flt-times, .time, .arrival, .arr') || last;
                  const laTxt = (lastArr && lastArr.innerText) ? lastArr.innerText : '';
                  const m2 = laTxt.match(/\\d{1,2}:\\d{2}\\s*(?:AM|PM)/i);
                  if (m2) arrTime = m2[0];
                }
              }
            }

            // 3) Fallback to regex on the slice's innerText (but only use slice-local times)
            if ((!depTime || !arrTime)) {
              const allTimes = (slice.innerText || '').match(/\\d{1,2}:\\d{2}\\s*(?:AM|PM)/gi) || [];
              if (allTimes.length >= 2) {
                if (!depTime) depTime = allTimes[0];
                if (!arrTime) arrTime = allTimes[allTimes.length - 1];
              } else if (allTimes.length === 1) {
                if (!depTime) depTime = allTimes[0];
                if (!arrTime) arrTime = allTimes[0];  // still keep non-empty
              }
            }

            // 4) Flight number: prefer structured leg flight-number nodes inside the slice
            let flightNo = '';
            const fnEl = slice.querySelector('.leg .flight-number, .flight-number, .segment .flight-number, .flight-details [class*="number"]');
            if (fnEl && fnEl.innerText) {
              const mfn = fnEl.innerText.match(/([A-Z]{2})\\s*(\\d{1,4})/);
              if (mfn) flightNo = mfn[1] + mfn[2];
            }
            if (!flightNo) { // fallback to slice text
              const mtxt = (slice.innerText || '').match(/\\b([A-Z]{2})\\s*(\\d{1,4})\\b/);
              if (mtxt) flightNo = mtxt[1] + mtxt[2];
            }

            // 5) Price inside slice: prefer product-groups/per-pax inside the slice
            let price = null;
            const perPax = slice.querySelector('.product-groups .per-pax-amount, .per-pax-amount, .price .per-pax-amount, .per-pax');
            if (perPax && perPax.innerText) {
              const pm = perPax.innerText.match(/\\$(\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?)/);
              if (pm) price = parseFloat(pm[1].replace(/,/g,''));
            }
            if (price === null) {
              // try buttons inside slice
              const btn = slice.querySelector('button.btn-flight, .btn-flight, button, a');
              if (btn && btn.innerText) {
                const pm2 = btn.innerText.match(/\\$(\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?)/);
                if (pm2) price = parseFloat(pm2[1].replace(/,/g,''));
              }
            }

            // 6) As last resort, walk parents but stop quickly (2 levels) to avoid mixing slices
            if ((price === null) && slice.parentElement) {
              let parent = slice.parentElement;
              let attempts = 0;
              while (parent && attempts < 2) {
                const pm3 = (parent.innerText || '').match(/\\$(\\d{1,3}(?:,\\d{3})*(?:\\.\\d{2})?)/);
                if (pm3) { price = parseFloat(pm3[1].replace(/,/g,'')); break; }
                parent = parent.parentElement;
                attempts++;
              }
            }

            if (flightNo && depTime && arrTime && (price !== null || /\\d+K/i.test(slice.innerText || ''))) {
              results.push({
                flight_number: flightNo,
                departure_time: depTime,
                arrival_time: arrTime,
                price: price,
                miles: (slice.innerText || '').match(/(\\d+(?:\\.\\d+)?)K/i) ? Math.round(parseFloat((slice.innerText || '').match(/(\\d+(?:\\.\\d+)?)K/i)[1]) * 1000) : null
              });
            }
          } catch (e) {
            console.error('slice parse error', e);
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

    # ----------------------------
    # REPLACED: _format_flights (normalization + debugging)
    # ----------------------------
    def _format_flights(self, raw: List[Dict], is_award: bool) -> List[Dict]:
        """Convert raw parsed data to proper format with normalization and debugging dumps."""
        _dump(raw, f"raw_{'award' if is_award else 'cash'}_flights")

        flights = []
        for f in raw:
            # Normalize flight number and times early
            fn_raw = f.get("flight_number", "") or ""
            fn = extract_first_flight_number(fn_raw)

            dep_raw = f.get("departure_time") or ""
            arr_raw = f.get("arrival_time") or ""
            # Strip "+N" suffixes and parenthetical tooltips
            dep = _strip_plus_day_suffix(dep_raw).strip() if dep_raw else ""
            arr = _strip_plus_day_suffix(arr_raw).strip() if arr_raw else ""

            # If times are identical or empty, keep but dump context for debugging
            if dep == "" or arr == "":
                _dump({
                    "note": "missing_times",
                    "raw": f,
                }, f"missing_times_{fn}_{len(flights)}")

            if dep == arr and dep != "":
                # dump context to help refine selectors if we keep seeing duplicates
                _dump({
                    "note": "dep_equals_arr",
                    "flight_number": fn,
                    "dep_raw": dep_raw,
                    "arr_raw": arr_raw,
                    "entry": f
                }, f"dep_eq_arr_{fn}_{len(flights)}")

            if is_award and f.get("miles") is not None:
                try:
                    points = int(f["miles"])
                except:
                    points = amount_to_int(str(f.get("miles")))
                if fn and dep and arr and points:
                    flights.append({
                        "flight_number": fn,
                        "departure_time": dep,
                        "arrival_time": arr,
                        "points_required": int(points),
                    })
            elif (not is_award) and f.get("price") is not None:
                try:
                    price = float(f["price"])
                except:
                    price = _parse_money_from_str(str(f.get("price")))
                if fn and dep and arr and price is not None:
                    flights.append({
                        "flight_number": fn,
                        "departure_time": dep,
                        "arrival_time": arr,
                        "cash_price_usd": float(price),
                        "taxes_fees_usd": 5.60
                    })
            else:
                # Missing both price and miles - dump for debugging
                _dump({
                    "note": "missing_price_and_miles",
                    "flight_number": fn,
                    "dep": dep,
                    "arr": arr,
                    "entry": f
                }, f"missing_price_miles_{fn}_{len(flights)}")

        # Deduplicate
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

        # PASS 2: AWARD PRICES
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