from typing import List, Dict, Optional, Tuple
from .utils import normalize_flight_number, _strip_plus_day_suffix, _dump, calculate_cpp

def merge_and_dedup(cash: Optional[List[Dict]], award: Optional[List[Dict]]) -> List[Dict]:
    if cash is None:
        cash = []
    if award is None:
        award = []

    merged = []
    used_award = set()
    unmatched_cash = []

    for c in cash:
        cash_fn = normalize_flight_number(c.get("flight_number", ""))
        cash_dep = (c.get("departure_time", "") or "").strip()
        best_match = None
        best_idx = None

        for idx, a in enumerate(award):
            if idx in used_award:
                continue
            award_fn = normalize_flight_number(a.get("flight_number", ""))
            award_dep = (a.get("departure_time", "") or "").strip()
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
            item["cpp"] = calculate_cpp(item["cash_price_usd"], item["taxes_fees_usd"], item["points_required"])
            merged.append(item)
            used_award.add(best_idx)
        else:
            unmatched_cash.append(c)

    if unmatched_cash:
        _dump(unmatched_cash, "unmatched_cash")

    unused_award = [a for idx, a in enumerate(award) if idx not in used_award]
    if unused_award:
        _dump(unused_award, "unmatched_award")

    # Deduplicate
    seen = set()
    unique = []
    for m in merged:
        key = (m["flight_number"], m["departure_time"], m["cash_price_usd"], m["points_required"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    return unique

def merge_parsed_into_output_json(parsed_cash_list: List[Dict], output_json_path: str):
    import json
    try:
        with open(output_json_path, "r", encoding="utf-8") as f:
            out = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Could not read {output_json_path}: {e}")

    parsed_lookup = {}
    for p in parsed_cash_list:
        key = (normalize_flight_number(p.get("flight_number", "")), _strip_plus_day_suffix((p.get("departure_time") or "").strip()))
        existing = parsed_lookup.get(key)
        if existing is None:
            parsed_lookup[key] = p
        else:
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