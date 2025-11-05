from typing import List, Dict, Optional
from .utils import (
    _strip_plus_day_suffix, _parse_money_from_str, FLIGHT_RE, PRICE_RE,
    _dump, amount_to_int
)

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None  # caller must handle

def parse_cash_from_html(html_content: str, default_taxes: float = 5.60) -> List[Dict]:
    if BeautifulSoup is None:
        raise RuntimeError("BeautifulSoup not available. Install with: pip install beautifulsoup4")

    soup = BeautifulSoup(html_content, "html.parser")
    slices = []

    slice_blocks = soup.select("[id^=slice-details]") or soup.select("app-slice-details") or soup.select(".result, .slice, .flight, .app-slice-details")

    for slice_tag in slice_blocks:
        try:
            flight_card = slice_tag.select_one(".matrix-flight-card") or slice_tag.select_one("app-matrix-flight-card") or slice_tag
            if not flight_card:
                continue

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

            txt = flight_card.get_text(" ", strip=True) or ""
            times = re.findall(r'(\d{1,2}:\d{2}\s*(?:AM|PM))', txt, flags=re.IGNORECASE)
            if not dep and times:
                dep = times[0].strip()
            if not arr and len(times) >= 2:
                arr = times[-1].strip()
            if not arr and dep and len(times) == 1:
                arr = dep

            dep = _strip_plus_day_suffix(dep) if dep else ""
            arr = _strip_plus_day_suffix(arr) if arr else ""

            flt_tag = flight_card.select_one(".flight-number")
            flight_number = None
            if flt_tag:
                flight_number = flt_tag.get_text(" ", strip=True)
            else:
                m = FLIGHT_RE.search(txt)
                if m:
                    flight_number = m.group(1)

            if not flight_number:
                continue

            flight_number = flight_number.split(',')[0].strip().upper().replace(' ', '')

            cash_price = None
            main_price_tag = slice_tag.select_one(".product-groups .btn-flight.MAIN .per-pax-amount")
            if main_price_tag:
                cash_price = _parse_money_from_str(main_price_tag.get_text(" ", strip=True))
            if cash_price is None:
                any_price_tag = slice_tag.select_one(".product-groups .per-pax-amount, .per-pax-amount, .price .per-pax-amount, .price")
                if any_price_tag:
                    cash_price = _parse_money_from_str(any_price_tag.get_text(" ", strip=True))
            if cash_price is None:
                m = PRICE_RE.search(slice_tag.get_text(" ", strip=True))
                if m:
                    try:
                        cash_price = float(m.group(1).replace(',', ''))
                    except:
                        cash_price = None
            if cash_price is None:
                cash_price = _parse_money_from_str(txt)

            if cash_price is not None and dep and arr:
                slices.append({
                    "flight_number": flight_number,
                    "departure_time": dep,
                    "arrival_time": arr,
                    "cash_price_usd": float(cash_price),
                    "taxes_fees_usd": float(default_taxes) if default_taxes is not None else 5.60
                })
            else:
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