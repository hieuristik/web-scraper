import json
import pathlib
import re
from typing import Any, Dict, List, Optional
from selenium.webdriver.common.by import By

# Debug output dir
OUT = pathlib.Path("data/debug")
OUT.mkdir(parents=True, exist_ok=True)

def _dump(x: Any, name: str) -> None:
    try:
        (OUT / f"{name}.json").write_text(json.dumps(x, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _save_html(driver, name: str) -> None:
    try:
        (OUT / f"{name}.html").write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass

# Regex helpers (exported)
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
    if not fn:
        return ""
    return re.sub(r'\s+', '', fn.upper())

def extract_first_flight_number(flight_str: str) -> str:
    if not flight_str:
        return ""
    first_flight = flight_str.split(',')[0].strip()
    return normalize_flight_number(first_flight)

def amount_to_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.strip().upper().replace(',', '').replace('$', '')
    s = re.sub(r'^[^\d\.\-]+', '', s)
    m = re.match(r'^([\d\.]+)K$', s)
    if m:
        try:
            return int(round(float(m.group(1)) * 1000))
        except:
            return None
    try:
        return int(float(s))
    except:
        return None

def _strip_plus_day_suffix(time_str: Optional[str]) -> Optional[str]:
    if not time_str:
        return time_str
    s = re.sub(r'\s*\(.*?\)', '', time_str)
    s = re.sub(r'\s*\+\d+\b.*$', '', s)
    return s.strip()

def _parse_money_from_str(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = PRICE_RE.search(s)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except:
            pass
    m2 = re.search(r'(\d{2,6}(?:\.\d{1,2})?)', s.replace(',', ''))
    if m2:
        try:
            val = float(m2.group(1))
            if val >= 1:
                return val
        except:
            pass
    return None

def _extract_price_from_element_text(txt: str) -> Optional[float]:
    if not txt:
        return None
    m = PRICE_RE.search(txt)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except:
            pass
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

def _find_first(parent, selectors: List[str]):
    for sel in selectors:
        try:
            return parent.find_element(By.CSS_SELECTOR, sel)
        except Exception:
            continue
    return None

# New helper: convert 12-hour "H:MM AM/PM" style times to 24-hour "HH:MM"
def convert_to_24h_time(time_str: Optional[str]) -> Optional[str]:
    """
    Convert a time string like "6:05 AM", "12:45 AM", "10:49 PM" to 24-hour "HH:MM".
    Returns the converted string, or None if conversion wasn't possible.
    This function is forgiving: it will search for an AM/PM time inside the input,
    strip any trailing +N markers or parentheses (but expects those are removed earlier).
    """
    if not time_str:
        return None
    s = time_str.strip()
    # find the first HH:MM + AM/PM occurrence
    m = re.search(r'(\d{1,2}):(\d{2})\s*([AP]M)', s, re.IGNORECASE)
    if not m:
        # maybe it's already 24-hour like "08:00" — validate it
        m2 = re.match(r'^([01]?\d|2[0-3]):([0-5]\d)$', s)
        if m2:
            hh = int(m2.group(1))
            mm = int(m2.group(2))
            return f"{hh:02d}:{mm:02d}"
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = m.group(3).upper()
    if ampm == "AM":
        if hour == 12:
            hour = 0
    else:  # PM
        if hour != 12:
            hour += 12
    return f"{hour:02d}:{minute:02d}"