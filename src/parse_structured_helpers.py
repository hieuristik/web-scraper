"""
Robust helpers used by parse_structured.* to extract flight number, time and price
from a DOM element when structured selectors fail.

Usage:
- import the helper(s) in parse_structured.py:
    from .parse_structured_helpers import find_flight_in_element, find_price_in_element, find_time_in_element

- Call find_flight_in_element(web_element) when you can't find a flight using existing selectors.
This will return a string like "AA28" or None.
"""
import re
from typing import Optional

# Common regexes
FLIGHT_RE = re.compile(r'\b([A-Z]{2})\s*[-]?\s*(\d{1,4})\b')
FLIGHT_SEPARATED_RE = re.compile(r'\b([A-Z]{2})\b(?:\D{0,40}?)\b(\d{1,4})\b')  # carrier then number separated by other chars
TIME_RE = re.compile(r'\b([01]?\d|2[0-3]):[0-5]\d\s*(?:AM|PM|am|pm)?\b')
PRICE_RE = re.compile(r'\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?')

def _get_text_from_element(el) -> str:
    """Best-effort read of text from a Selenium WebElement or a plain string."""
    if el is None:
        return ""
    # If caller passed a string, return normalized
    if isinstance(el, str):
        return re.sub(r'\s+', ' ', el).strip()
    # Selenium WebElement-like: try .text first, then attributes
    text = ""
    try:
        text = getattr(el, "text", "") or ""
    except Exception:
        text = ""
    if not text:
        try:
            # fallback to innerText or textContent
            text = (el.get_attribute("innerText") or el.get_attribute("textContent") or "") if hasattr(el, "get_attribute") else ""
        except Exception:
            text = ""
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def find_flight_in_element(el) -> Optional[str]:
    """
    Attempt several strategies to extract a flight code (e.g. "AA28", "AA 28") from the element.
    Returns normalized 'AANC' (carrier+number) string (no spaces) or None.
    """
    text = _get_text_from_element(el)
    if not text:
        return None

    # Strategy 1: direct match (AA123)
    m = FLIGHT_RE.search(text)
    if m:
        return f"{m.group(1)}{m.group(2)}"

    # Strategy 2: carrier and number separated by other tags/characters
    m2 = FLIGHT_SEPARATED_RE.search(text)
    if m2:
        return f"{m2.group(1)}{m2.group(2)}"

    # Strategy 3: sometimes number and carrier are reversed (unlikely), try any digit cluster near a carrier
    # Already covered by FLIGHT_SEPARATED_RE, but keep fallback to find any short numeric token near uppercase letters
    m3 = re.search(r'([A-Z]{2}).{0,30}?(\d{1,4})', text)
    if m3:
        return f"{m3.group(1)}{m3.group(2)}"

    return None

def find_time_in_element(el) -> Optional[str]:
    text = _get_text_from_element(el)
    if not text:
        return None
    m = TIME_RE.search(text)
    if m:
        return m.group(0)
    return None

def find_price_in_element(el) -> Optional[str]:
    """
    Find a price like '$114' (string preserved as in page).
    """
    text = _get_text_from_element(el)
    if not text:
        return None
    m = PRICE_RE.search(text)
    if m:
        return m.group(0)
    return None