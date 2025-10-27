import argparse, json, pathlib, asyncio
from datetime import date
from .models import SearchMetadata, FlightItem, SearchResult
from .cpp import cpp_cents_per_point
from .playwright_flow import search_and_capture
from .parse_aa import parse_from_network, parse_from_dom


"""
CLI to test web scraper application
"""
def main():
    """
    Required arguments
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", required=True)
    ap.add_argument("--destination", required=True)
    ap.add_argument("--date", required=True)      # YYYY-MM-DD
    ap.add_argument("--passengers", type=int, default=1)
    ap.add_argument("--cabin", default="economy")
    ap.add_argument("--output", default="out.json")
    args = ap.parse_args()

    meta = SearchMetadata(
        origin=args.origin,
        destination=args.destination,
        date=date.fromisoformat(args.date),
        passengers=args.passengers,
        cabin_class=args.cabin,
    )

    payload = asyncio.run(search_and_capture({
        "origin": meta.origin, "destination": meta.destination, "date": meta.date.isoformat()
    }))
    flights = parse_from_network(payload["network_json"])
    if not flights:
        flights = parse_from_dom(payload["page_html"])

    items = []
    for f in flights:
        cpp = cpp_cents_per_point(f["cash_price_usd"], f["taxes_fees_usd"], f["points_required"])
        items.append(FlightItem(
            flight_number=f["flight_number"],
            departure_time=f["departure_time"],
            arrival_time=f["arrival_time"],
            points_required=f["points_required"],
            cash_price_usd=f["cash_price_usd"],
            taxes_fees_usd=f["taxes_fees_usd"],
            cpp=cpp,
        ))

    result = SearchResult(search_metadata=meta, flights=items, total_results=len(items))
    pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))
    print(f"✅ Wrote {args.output} with {len(items)} flights")

if __name__ == "__main__":
    main()
