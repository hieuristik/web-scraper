import re
from typing import List, Dict, Any
from selenium.webdriver.common.by import By
from .utils import (
    _element_inner_text, _strip_plus_day_suffix, _find_first, FLIGHT_RE,
    TIME_RE, PRICE_RE, _dump, amount_to_int, _parse_money_from_str
)

def _format_flights(raw: List[Dict], is_award: bool) -> List[Dict]:
    _dump(raw, f"raw_{'award' if is_award else 'cash'}_flights")
    flights = []
    for f in raw:
        fn_raw = f.get("flight_number", "") or ""
        fn = fn_raw.split(',')[0].strip().upper().replace(' ', '')

        dep_raw = f.get("departure_time") or ""
        arr_raw = f.get("arrival_time") or ""
        dep = _strip_plus_day_suffix(dep_raw).strip() if dep_raw else ""
        arr = _strip_plus_day_suffix(arr_raw).strip() if arr_raw else ""

        if dep == "" or arr == "":
            _dump({"note": "missing_times", "raw": f}, f"missing_times_{fn}_{len(flights)}")

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
            _dump({"note": "missing_price_and_miles", "flight_number": fn, "dep": dep, "arr": arr, "entry": f}, f"missing_price_miles_{fn}_{len(flights)}")

    # Deduplicate
    seen = set()
    unique = []
    for f in flights:
        key = (f["flight_number"], f["departure_time"], f.get("cash_price_usd"), f.get("points_required"))
        if key not in seen:
            seen.add(key)
            unique.append(f)

    _dump(unique, f"parsed_{'award' if is_award else 'cash'}_flights")
    return unique

def parse_cash_flights_structured(driver) -> List[Dict]:
    flights = []
    try:
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
                flight_elem = _find_first(slice_elem, [
                    ".matrix-flight-card", ".grid-x .cell.large-3.origin", ".origin", ".cell.large-3.origin", ".flight-details", "app-matrix-flight-card", "app-slice-info-desktop"
                ]) or slice_elem

                product_elem = _find_first(slice_elem, [
                    "app-available-products-desktop", ".product-groups", ".available-products", ".product-group"
                ])
                if product_elem is None:
                    try:
                        parent = slice_elem.find_element(By.XPATH, "..")
                        product_elem = _find_first(parent, ["app-available-products-desktop", ".product-groups", ".available-products"])
                    except Exception:
                        product_elem = None

                departure_time = ""
                arrival_time = ""
                dep_time_elem = _find_first(flight_elem, [".origin .flt-times", ".origin .time", ".flt-times", ".time", ".flt-time"])
                if dep_time_elem:
                    departure_time = _strip_plus_day_suffix(_element_inner_text(dep_time_elem).strip())
                arr_time_elem = _find_first(flight_elem, [".destination .flt-times", ".destination .time", ".flt-times", ".time", ".flt-time"])
                if arr_time_elem:
                    arrival_time = _strip_plus_day_suffix(_element_inner_text(arr_time_elem).strip().split('\n')[0].strip())

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

                flight_number = None
                fn_el = _find_first(slice_elem, [".leg .flight-number", ".flight-number", ".segment .flight-number", ".flight-details [class*='number']"])
                if fn_el:
                    flt_txt = _element_inner_text(fn_el)
                    m = FLIGHT_RE.search(flt_txt)
                    if m:
                        flight_number = m.group(1).split(',')[0].strip()
                else:
                    ftext = _element_inner_text(slice_elem) or ""
                    m = FLIGHT_RE.search(ftext)
                    if m:
                        flight_number = m.group(1).split(',')[0].strip()

                if not flight_number:
                    _dump({"idx": idx, "note": "no_flight_number", "slice_text": (_element_inner_text(slice_elem) or "")[:1000]}, f"cash_fail_no_fn_{idx}")
                    continue

                cash_price = None
                if product_elem is not None:
                    try:
                        per_pax_nodes = product_elem.find_elements(By.CSS_SELECTOR, ".per-pax-amount, .per-pax, .price .per-pax-amount")
                        for p in per_pax_nodes:
                            val = _parse_money_from_str(_element_inner_text(p) or "")
                            if val and val >= 10:
                                cash_price = val
                                break
                        if cash_price is None:
                            price_buttons = product_elem.find_elements(By.CSS_SELECTOR, "button.btn-flight, .btn-flight, button, a")
                            for btn in price_buttons:
                                txt = _element_inner_text(btn) or ""
                                val = _parse_money_from_str(txt)
                                if val and val >= 10:
                                    cash_price = val
                                    break
                        if cash_price is None:
                            container_text = _element_inner_text(product_elem) or ""
                            val = _parse_money_from_str(container_text)
                            if val and val >= 10:
                                cash_price = val
                    except Exception:
                        pass
                else:
                    _dump({"idx": idx, "note": "no_product_elem", "slice_text": (_element_inner_text(slice_elem) or "")[:1000]}, f"cash_fail_no_product_{idx}")

                if cash_price is not None and departure_time and arrival_time:
                    flights.append({
                        "flight_number": flight_number,
                        "departure_time": departure_time,
                        "arrival_time": arrival_time,
                        "cash_price_usd": float(cash_price),
                        "taxes_fees_usd": 5.60
                    })
                else:
                    ctx = {"idx": idx, "flight_number": flight_number, "departure_time": departure_time, "arrival_time": arrival_time, "cash_price": cash_price, "flight_inner": (_element_inner_text(slice_elem) or "")[:1000], "product_inner": (_element_inner_text(product_elem) or "")[:800 if product_elem is not None else 0]}
                    _dump(ctx, f"cash_fail_{idx}")

            except Exception as e:
                print(f"    ⚠ Flight {idx}: Parsing error: {e}")
                continue
    except Exception as e:
        print(f"  ❌ Cash structured parsing failed: {e}")

    print(f"  Extracted {len(flights)} cash flights (structured)")
    return flights

def parse_award_flights_structured(driver) -> List[Dict]:
    flights = []
    try:
        flight_sections = driver.find_elements(By.CSS_SELECTOR, "app-slice-info-desktop")
        product_sections = driver.find_elements(By.CSS_SELECTOR, "app-available-products-desktop")

        print(f"  Found {len(flight_sections)} flight blocks and {len(product_sections)} product sections (award)")

        if len(flight_sections) != len(product_sections):
            print(f"  ⚠ Mismatch: {len(flight_sections)} flights vs {len(product_sections)} product sections")

        for idx, (flight_elem, product_elem) in enumerate(zip(flight_sections, product_sections)):
            try:
                origin = ""
                destination = ""
                try:
                    origin = flight_elem.find_element(By.CSS_SELECTOR, ".origin .city-code").text.strip()
                except Exception:
                    origin = ""
                try:
                    destination = flight_elem.find_element(By.CSS_SELECTOR, ".destination .city-code").text.strip()
                except Exception:
                    destination = ""
                try:
                    dep_time_elem = flight_elem.find_element(By.CSS_SELECTOR, ".origin .flt-times")
                    departure_time = _strip_plus_day_suffix(dep_time_elem.text.strip())
                except Exception:
                    flight_text = _element_inner_text(flight_elem) or ""
                    times = TIME_RE.findall(flight_text)
                    departure_time = times[0].strip() if times else ""
                try:
                    arr_time_elem = flight_elem.find_element(By.CSS_SELECTOR, ".destination .flt-times")
                    arrival_time = _strip_plus_day_suffix(arr_time_elem.text.strip().split('\n')[0].strip())
                except Exception:
                    flight_text = _element_inner_text(flight_elem) or ""
                    times = TIME_RE.findall(flight_text)
                    arrival_time = times[-1].strip() if times else ""
                try:
                    leg_elems = flight_elem.find_elements(By.CSS_SELECTOR, ".leg-info")
                    if not leg_elems:
                        leg_elems = flight_elem.find_elements(By.CSS_SELECTOR, ".segment, .leg")
                    flight_numbers = []
                    for leg in leg_elems:
                        try:
                            flt_num_elem = leg.find_element(By.CSS_SELECTOR, ".flight-number")
                            flt_num = flt_num_elem.text.strip()
                        except Exception:
                            txt = _element_inner_text(leg) or ""
                            m = FLIGHT_RE.search(txt)
                            flt_num = m.group(1) if m else ""
                        if flt_num:
                            flight_numbers.append(flt_num)
                except Exception:
                    flight_numbers = []

                flight_number = flight_numbers[0] if flight_numbers else "Unknown"
                flight_number = flight_number.split(',')[0].strip().upper().replace(' ', '')

                award_miles = None
                award_fees = None
                try:
                    price_buttons = product_elem.find_elements(By.CSS_SELECTOR, "button.btn-flight, .btn-flight, button")
                    for btn in price_buttons:
                        try:
                            hidden_type = ""
                            try:
                                hidden_type = btn.find_element(By.CSS_SELECTOR, ".hidden-product-type").text.strip()
                            except Exception:
                                hidden_type = btn.get_attribute("aria-label") or ""
                            ht = hidden_type.strip().lower() if hidden_type else ""
                            if ht in ("main", "main cabin", "maincabin", "coach", "main cabin (coach)"):
                                miles_text = ""
                                try:
                                    miles_elem = btn.find_element(By.CSS_SELECTOR, ".per-pax-amount, .per-pax")
                                    miles_text = miles_elem.text.strip()
                                except Exception:
                                    miles_text = _element_inner_text(btn) or ""
                                miles_match = re.search(r'([\d\.]+)K', miles_text, re.IGNORECASE)
                                if miles_match:
                                    miles_value = float(miles_match.group(1))
                                    award_miles = int(miles_value * 1000)
                                else:
                                    num_match = re.search(r'(\d{1,3}(?:[,\d]{0,})+)', miles_text.replace(' ', ''))
                                    if num_match:
                                        award_miles = amount_to_int(num_match.group(1))
                                try:
                                    fees_elem = btn.find_element(By.CSS_SELECTOR, ".per-pax-addon, .fees, .addon")
                                    fees_text = fees_elem.text.strip()
                                    fees_match = re.search(r'\$?([\d.]+)', fees_text)
                                    if fees_match:
                                        award_fees = float(fees_match.group(1))
                                except:
                                    award_fees = award_fees
                                if award_miles is not None:
                                    break
                        except: pass
                except Exception as e:
                    print(f"    ⚠ Flight {idx}: Could not parse award prices: {e}")

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
    except Exception as e:
        print(f"  ❌ Award structured parsing failed: {e}")

    print(f"  Extracted {len(flights)} award flights (structured)")
    return flights