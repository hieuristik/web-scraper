from __future__ import annotations
from typing import Any, Dict, List
from bs4 import BeautifulSoup

def parse_from_network(blobs):
    flights: List[dict] = []
    # Inspect one captured JSON to learn the exact shape, then adapt below:
    for item in blobs:
        j = item.get("json") or {}
        # Pseudocode: navigate to itineraries/offers, extract
        # for offer in j["data"]["offers"]:
        #   flights.append({...})
    return flights

def parse_from_dom(html):
    flights: List[dict] = []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("[data-test-id='resultCard']") or soup.select("article, li")
    for c in cards:
        # Heuristics—replace with exact selectors after inspecting the HTML:
        flight_no = (c.select_one("[data-test-id='flightNumber']") or c.find(text=lambda t: "AA" in t)).get_text(strip=True)
        depart = (c.select_one("[data-test-id='departTime']") or c.select_one(".depart-time")).get_text(strip=True)
        arrive = (c.select_one("[data-test-id='arrivalTime']") or c.select_one(".arrive-time")).get_text(strip=True)
        # Points, cash, taxes: AA often shows as “12,500 miles” and “$289” + “$5.60”
        # You’ll need regex cleanups:
        # points = int(re.sub(r"[^\d]", "", points_text))
        # cash = float(re.sub(r"[^0-9.]", "", cash_text))
        # taxes = float(re.sub(r"[^0-9.]", "", taxes_text))
        # flights.append({...})
    return flights
