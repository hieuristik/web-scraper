import json
import pathlib
import sys
from typing import Any, Dict
from .selenium_driver import AAScraper
from .merge import merge_and_dedup
from .utils import _dump

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

def search(params: Dict[str, Any]) -> Dict[str, Any]:
    scraper = AAScraper()
    try:
        scraper.setup()
        scraper.open_home()

        print("\nPASS 1: CASH PRICES")
        cash_flights = scraper.search_flights(params["origin"], params["destination"], params["date"], redeem_miles=False)

        print("\n🏠 Returning home...")
        try:
            scraper.driver.get("https://www.aa.com/")
        except Exception:
            pass

        print("\nPASS 2: AWARD PRICES")
        award_flights = scraper.search_flights(params["origin"], params["destination"], params["date"], redeem_miles=True)

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

        pathlib.Path("data/processed/output.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        _dump(output, "debug_output")
        return output
    except Exception as e:
        raise
    finally:
        scraper.close()

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    params = _cli(argv)
    print(f"AA Flight Scraper - {params['origin']} → {params['destination']} on {params['date']}")
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