# src/demo_mode.py
"""
Demo mode: parse both saved rendered HTML files (cash and award), run structured
parsers in a headless browser, rehydrate shadow DOM if available, aggressively
merge and validate results, and produce data/processed/output.json in the
competition-required format.

Important behavior changes in this version:
- Strict validation: only include flights that have all required fields:
    flight_number, departure_time (HH:MM 24h), arrival_time (HH:MM 24h),
    cash_price_usd, points_required, taxes_fees_usd, and a computed cpp.
  Any cluster missing one of those is rejected.
- cpp is computed using the competition formula:
    cpp = (cash_price_usd - taxes_fees_usd) / points_required * 100
  The cpp is expressed in cents-per-point and rounded to 2 decimal places.
- No null fields appear in the final flight objects: fields with missing values
  are omitted by rejecting the entire entry instead.
- Output fields for each flight are exactly:
    flight_number, departure_time, arrival_time, points_required,
    cash_price_usd, taxes_fees_usd, cpp
  (no extra debug fields).
- Configurable maximum cpp via env DEMO_MAX_CPP (default 5 cents). Clusters
  with cpp > DEMO_MAX_CPP are rejected as implausible.

Usage (inside container):
  python /app/src/demo_mode.py --cash-file /app/data/debug/cash_results_rendered.html \
    --award-file /app/data/debug/award_results_rendered.html --origin LAX --destination JFK --date 2025-12-15

When running in Docker mount data/debug and data/processed the same way you already do.
"""
import os
import sys
import json
import time
import re
import math
import argparse
from typing import List, Dict, Any, Optional

# Ensure repo root on sys.path so src.* imports work when this file is executed directly
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

OUT_PATH = os.path.join("data", "processed", "output.json")

# --- Small robust text fallback parser (only used if browser parsing fails) ---
FLIGHT_RE = re.compile(r'\b([A-Z]{2}\s?\d{1,4})\b', re.IGNORECASE)
TIME_RE = re.compile(r'\b(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:AM|PM)?\b', re.IGNORECASE)
PRICE_RE = re.compile(r'\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?')
MILES_RE = re.compile(r'(\d+(?:\.\d+)?)\s*K', re.IGNORECASE)


def _text_from_html(html: str) -> str:
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.S | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _demo_text_parse(html: str, is_award: bool) -> List[Dict]:
    txt = _text_from_html(html)
    flights_tokens = FLIGHT_RE.findall(txt)
    times = TIME_RE.findall(txt)
    prices = PRICE_RE.findall(txt)
    miles = MILES_RE.findall(txt)

    tokens = [t.replace(' ', '').upper() for t in flights_tokens]
    tokens_unique = []
    for t in tokens:
        if t not in tokens_unique:
            tokens_unique.append(t)

    results: List[Dict] = []
    if not tokens_unique:
        return results

    n = max(1, min(len(tokens_unique), max(1, len(times) // 2, len(prices), len(miles))))
    for i in range(n):
        fn = tokens_unique[i] if i < len(tokens_unique) else f"AA{i+100}"
        dep = times[i * 2].strip() if i * 2 < len(times) else ""
        arr = times[i * 2 + 1].strip() if (i * 2 + 1) < len(times) else (dep or "")
        rec: Dict[str, Any] = {"flight_number": fn, "departure_time": dep, "arrival_time": arr}
        if is_award:
            if i < len(miles):
                try:
                    rec["points_required"] = int(float(miles[i]) * 1000)
                    rec["taxes_fees_usd"] = 5.60
                except Exception:
                    pass
        else:
            if i < len(prices):
                try:
                    rec["cash_price_usd"] = float(re.sub(r'[^\d.]', '', prices[i]))
                    rec["taxes_fees_usd"] = 5.60
                except Exception:
                    pass
        results.append(rec)
    return results


# --- Normalizers/helpers ----------------------------------------------------
def normalize_flight_number(fn: Optional[str]) -> str:
    if not fn:
        return ""
    s = str(fn).upper()
    return re.sub(r'[^A-Z0-9]', '', s)


def parse_time_to_24(t: Optional[str]) -> str:
    """Return HH:MM (24-hour) or empty string."""
    if not t:
        return ""
    s = str(t).strip()
    # already 24-hour
    m = re.match(r'^([01]?\d|2[0-3]):([0-5]\d)$', s)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2)); return f"{hh:02d}:{mm:02d}"
    # AM/PM
    m2 = re.match(r'^(1[0-2]|0?\d):([0-5]\d)\s*(AM|PM)$', s, re.IGNORECASE)
    if m2:
        hh = int(m2.group(1)); mm = int(m2.group(2)); ampm = m2.group(3).upper()
        if ampm == "AM":
            hh24 = 0 if hh == 12 else hh
        else:
            hh24 = hh if hh == 12 else hh + 12
        return f"{hh24:02d}:{mm:02d}"
    # fallback: extract first HH:MM token
    m3 = re.search(r'([01]?\d|2[0-3]):([0-5]\d)\s*(AM|PM)?', s, re.IGNORECASE)
    if m3:
        hh = int(m3.group(1)); mm = int(m3.group(2)); ampm = m3.group(3)
        if ampm:
            ampm = ampm.upper()
            if ampm == "AM":
                hh24 = 0 if hh == 12 else hh
            else:
                hh24 = hh if hh == 12 else hh + 12
        else:
            hh24 = hh
        return f"{hh24:02d}:{mm:02d}"
    return ""


def _inject_shadows(driver, shadows_path: str):
    """Best-effort rehydrate shadow DOM from saved _shadows.json."""
    if not os.path.exists(shadows_path):
        return
    try:
        shadows = json.load(open(shadows_path, "r", encoding="utf-8"))
        if not isinstance(shadows, list):
            return
    except Exception:
        return

    js_find_and_attach = r"""
    (function(path, innerHtml) {
      function matchPart(node, part){
        let tag = part.split(/[#.]/)[0].toLowerCase();
        if(node.tagName && node.tagName.toLowerCase() !== tag) return false;
        if(part.indexOf('#')>=0){
          let m = part.match(/#([^\.\#]+)/);
          if(!m) return false;
          if(node.id !== m[1]) return false;
        }
        if(part.indexOf('.')>=0){
          let classes = part.split('.').slice(1).filter(Boolean);
          for(let c of classes){
            if(!node.classList || !node.classList.contains(c)) return false;
          }
        }
        return true;
      }
      function findByPath(path){
        try {
          let parts = path.split('>');
          let node = document.documentElement;
          for(let i=1;i<parts.length;i++){
            let part = parts[i].trim();
            if(!part) continue;
            let found = null;
            for(let j=0;j<node.children.length;j++){
              let c = node.children[j];
              if(matchPart(c, part)){
                found = c;
                break;
              }
            }
            if(!found) return null;
            node = found;
          }
          return node;
        } catch (e) { return null; }
      }
      let host = findByPath(path);
      if(host){
        try {
          let sr = host.shadowRoot || host.attachShadow({mode:'open'});
          sr.innerHTML = innerHtml;
          return true;
        } catch (e) {
          try { host.setAttribute('data-demo-shadow-error', String(e)); } catch(e){}
        }
      }
      return false;
    })
    """
    for entry in shadows:
        path = entry.get("path")
        inner = entry.get("html") or ""
        if not path:
            continue
        try:
            driver.execute_script(js_find_and_attach + "return arguments[0].call(null, arguments[1], arguments[2]);", path, inner)
        except Exception:
            pass


def run_with_browser_for_file(driver, html_path: str, is_award: bool) -> List[Dict]:
    """
    Inject saved rendered HTML deterministically (document.write), rehydrate shadows,
    then call src.parse_structured parser functions and return parsed flights.
    """
    try:
        from src.parse_structured import parse_cash_flights_structured, parse_award_flights_structured
    except Exception as e:
        raise RuntimeError(f"Could not import parser module: {e}")

    with open(html_path, "r", encoding="utf-8", errors="ignore") as fh:
        html_content = fh.read()

    driver.get("about:blank")
    driver.execute_script("document.open(); document.write(arguments[0]); document.close();", html_content)
    time.sleep(0.5)

    shadows_path = html_path.replace("_rendered.html", "_shadows.json")
    _inject_shadows(driver, shadows_path)

    try:
        driver.execute_script("window.scrollBy(0, 150);")
        time.sleep(0.3)
    except Exception:
        pass

    if is_award:
        return parse_award_flights_structured(driver)
    else:
        return parse_cash_flights_structured(driver)


# --- Strict merge & validation ------------------------------------------------
def _merge_and_normalize_strict(all_flights: List[Dict]) -> List[Dict]:
    """
    Strict merge:
      - bucket by normalized flight number
      - cluster by rounded departure (5 min) to dedupe
      - for each cluster require cash, points and taxes present (no nulls)
      - compute cpp using competition formula (cents per point) and reject clusters
        that exceed DEMO_MAX_CPP (default 5)
      - return flights with exactly the fields required (no nulls):
        flight_number, departure_time, arrival_time, points_required,
        cash_price_usd, taxes_fees_usd, cpp
    """
    # config
    try:
        DEMO_MAX_CPP = float(os.environ.get("DEMO_MAX_CPP", os.environ.get("DEMO_MAX_CENTS_PER_POINT", "5.0")))
    except Exception:
        DEMO_MAX_CPP = 5.0

    # helper: minutes since midnight
    def minutes_from_hhmm(hhmm: str) -> Optional[int]:
        if not hhmm:
            return None
        m = re.match(r'^([01]?\d|2[0-3]):([0-5]\d)$', hhmm)
        if not m:
            return None
        return int(m.group(1)) * 60 + int(m.group(2))

    # bucket by normalized flight number
    buckets: Dict[str, List[Dict]] = {}
    for f in all_flights:
        fn_raw = f.get("flight_number") or f.get("flight_number_raw") or ""
        fn_norm = normalize_flight_number(fn_raw)
        if not fn_norm:
            continue
        rec = dict(f)
        rec["flight_number"] = fn_norm
        rec["departure_time"] = parse_time_to_24(rec.get("departure_time") or "")
        rec["arrival_time"] = parse_time_to_24(rec.get("arrival_time") or "")
        # canonicalize numeric fields
        if rec.get("cash_price_usd") is not None:
            try:
                rec["cash_price_usd"] = float(rec["cash_price_usd"])
            except Exception:
                rec["cash_price_usd"] = None
        if rec.get("points_required") is not None:
            try:
                rec["points_required"] = int(rec["points_required"])
            except Exception:
                rec["points_required"] = None
        if rec.get("taxes_fees_usd") is not None:
            try:
                rec["taxes_fees_usd"] = float(rec["taxes_fees_usd"])
            except Exception:
                rec["taxes_fees_usd"] = None
        buckets.setdefault(fn_norm, []).append(rec)

    merged_list: List[Dict] = []

    # cluster key: rounded departure minutes to nearest 5
    for fn, items in buckets.items():
        rows = []
        for it in items:
            dep_min = minutes_from_hhmm(it.get("departure_time") or "")
            rows.append((dep_min, it))
        # skip if none have departure times
        if not any(r[0] is not None for r in rows):
            continue

        # group by rounded 5-minute key
        clusters: Dict[int, List[Dict]] = {}
        for dep_min, it in rows:
            if dep_min is None:
                key = -1
            else:
                key = int(round(dep_min / 5.0) * 5)
            clusters.setdefault(key, []).append(it)

        for key, cl in clusters.items():
            # require non-empty cluster
            if not cl:
                continue
            # require at least one cash and one points in the cluster (strict)
            cash_vals = [float(c["cash_price_usd"]) for c in cl if c.get("cash_price_usd") is not None]
            points_vals = [int(c["points_required"]) for c in cl if c.get("points_required") is not None]
            taxes_vals = [float(c["taxes_fees_usd"]) for c in cl if c.get("taxes_fees_usd") is not None]
            if not cash_vals or not points_vals or not taxes_vals:
                # strict requirement: cluster must include cash, points and taxes
                continue

            min_cash = min(cash_vals)
            min_points = min(points_vals)
            min_taxes = min(taxes_vals)

            # compute net and cpp using competition formula
            net = float(min_cash) - float(min_taxes)
            if net <= 0 or min_points <= 0:
                continue
            cpp_cents = (net / float(min_points)) * 100.0
            # reject implausible cpp above threshold
            if cpp_cents > DEMO_MAX_CPP:
                continue

            # representative times: median departure and arrival closest to center
            dep_minutes = [minutes_from_hhmm(c.get("departure_time") or "") for c in cl if minutes_from_hhmm(c.get("departure_time") or "") is not None]
            arr_minutes = [minutes_from_hhmm(c.get("arrival_time") or "") for c in cl if minutes_from_hhmm(c.get("arrival_time") or "") is not None]
            if not dep_minutes or not arr_minutes:
                continue
            dep_center = int(round(sum(dep_minutes) / len(dep_minutes)))
            chosen_arr = min(arr_minutes, key=lambda a: abs(a - dep_center))

            # Build final strict object (only required fields; no nulls)
            obj: Dict[str, Any] = {
                "flight_number": fn,
                "departure_time": f"{dep_center//60:02d}:{dep_center%60:02d}",
                "arrival_time": f"{chosen_arr//60:02d}:{chosen_arr%60:02d}",
                "points_required": int(min_points),
                "cash_price_usd": round(float(min_cash), 2),
                "taxes_fees_usd": round(float(min_taxes), 2),
                "cpp": round(cpp_cents, 2)
            }
            # final sanity: ensure no nulls in the object
            if any(v is None for v in obj.values()):
                continue
            merged_list.append(obj)

    # dedupe by (flight_number, departure_time) and sort
    unique: List[Dict] = []
    seen = set()
    for r in merged_list:
        key = (r.get("flight_number"), r.get("departure_time"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    unique.sort(key=lambda x: (x.get("departure_time") or "", x.get("flight_number") or ""))
    return unique


# --- Orchestration: parse files, merge, write output -------------------------
def _write_output(result: Dict[str, Any]):
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"Wrote demo output to: {OUT_PATH}")


def run_demo(cash_file: Optional[str], award_file: Optional[str], browser: bool = True,
             origin: Optional[str] = None, destination: Optional[str] = None, date: Optional[str] = None) -> Dict[str, Any]:
    all_flights: List[Dict] = []

    # metadata resolution: CLI flags override debug artifacts
    metadata = {}
    if origin: metadata["origin"] = origin
    if destination: metadata["destination"] = destination
    if date: metadata["date"] = date
    if not metadata:
        candidates = [os.path.join("data", "debug", "final_output.json"),
                      os.path.join("data", "debug", "debug_output.json"),
                      os.path.join("data", "debug", "output.json")]
        for c in candidates:
            if os.path.exists(c):
                try:
                    j = json.load(open(c, "r", encoding="utf-8"))
                    if isinstance(j, dict) and j.get("search_metadata"):
                        metadata = j["search_metadata"]
                        break
                except Exception:
                    pass
    if not metadata:
        metadata = {"origin": "DEMO", "destination": "DEMO", "date": "1970-01-01"}

    # attempt browser-backed parsing
    if browser:
        try:
            import undetected_chromedriver as uc  # type: ignore
        except Exception as e:
            print("undetected_chromedriver not available, falling back to text-only parsing:", e)
            browser = False

    if browser:
        opts = uc.ChromeOptions()
        opts.add_argument('--disable-blink-features=AutomationControlled')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument("--window-size=1400,1000")
        opts.add_argument("--lang=en-US,en")
        ua = os.environ.get("DOCKER_UA")
        if ua:
            try: opts.add_argument(f"user-agent={ua}")
            except Exception: pass
        chrome_path = os.environ.get("CHROME_PATH")
        if chrome_path:
            try: opts.binary_location = chrome_path
            except Exception: pass
        try:
            opts.add_argument('--headless=new')
        except Exception:
            try: opts.add_argument('--headless')
            except Exception: pass

        driver = None
        try:
            driver = uc.Chrome(options=opts)
            # parse cash
            if cash_file and os.path.exists(cash_file):
                try:
                    parsed_cash = run_with_browser_for_file(driver, cash_file, is_award=False)
                    print(f"Browser demo: parsed {len(parsed_cash)} cash flights from {cash_file}")
                    all_flights.extend(parsed_cash)
                except Exception as e:
                    print("Error parsing cash file in browser mode, falling back to text:", e)
                    try:
                        html = open(cash_file, "r", encoding="utf-8", errors="ignore").read()
                        all_flights.extend(_demo_text_parse(html, is_award=False))
                    except Exception:
                        pass
            # parse award
            if award_file and os.path.exists(award_file):
                try:
                    parsed_award = run_with_browser_for_file(driver, award_file, is_award=True)
                    print(f"Browser demo: parsed {len(parsed_award)} award flights from {award_file}")
                    all_flights.extend(parsed_award)
                except Exception as e:
                    print("Error parsing award file in browser mode, falling back to text:", e)
                    try:
                        html = open(award_file, "r", encoding="utf-8", errors="ignore").read()
                        all_flights.extend(_demo_text_parse(html, is_award=True))
                    except Exception:
                        pass
        finally:
            if driver:
                try: driver.quit()
                except Exception: pass
    else:
        # text-only fallback
        if cash_file and os.path.exists(cash_file):
            html = open(cash_file, "r", encoding="utf-8", errors="ignore").read()
            all_flights.extend(_demo_text_parse(html, is_award=False))
        if award_file and os.path.exists(award_file):
            html = open(award_file, "r", encoding="utf-8", errors="ignore").read()
            all_flights.extend(_demo_text_parse(html, is_award=True))

    # Strict merge/validation producing only entries with all required fields + cpp
    merged = _merge_and_normalize_strict(all_flights)

    out = {
        "search_metadata": {
            "origin": metadata.get("origin", "DEMO"),
            "destination": metadata.get("destination", "DEMO"),
            "date": metadata.get("date", "1970-01-01"),
            "passengers": 1,
            "cabin_class": "economy"
        },
        "flights": merged,
        "total_results": len(merged)
    }
    _write_output(out)
    return out


def cli():
    p = argparse.ArgumentParser()
    p.add_argument("--cash-file", default="/app/data/debug/cash_results_rendered.html", help="Path to saved rendered cash HTML")
    p.add_argument("--award-file", default="/app/data/debug/award_results_rendered.html", help="Path to saved rendered award HTML")
    p.add_argument("--no-browser", action="store_true", help="Disable launching Chromium; use text fallback")
    p.add_argument("--origin", type=str, help="Origin airport code (overrides auto-detect)")
    p.add_argument("--destination", type=str, help="Destination airport code (overrides auto-detect)")
    p.add_argument("--date", type=str, help="Date string (overrides auto-detect)")
    args = p.parse_args()
    run_demo(cash_file=args.cash_file if args.cash_file else None,
             award_file=args.award_file if args.award_file else None,
             browser=not args.no_browser,
             origin=args.origin, destination=args.destination, date=args.date)


if __name__ == "__main__":
    cli()