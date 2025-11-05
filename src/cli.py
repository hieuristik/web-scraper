import json
import pathlib
import sys
import os
from typing import Any, Dict
from .selenium_driver import AAScraper
from .merge import merge_and_dedup
from .utils import _dump, convert_to_24h_time

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

        # keep counts for console only (do not include in final JSON)
        cash_count = len(cash_flights) if cash_flights else 0
        award_count = len(award_flights) if award_flights else 0

        merged = merge_and_dedup(cash_flights, award_flights)
        merged_count = len(merged)

        # Prepare the final JSON structure (per the required format)
        output = {
            "search_metadata": {
                "origin": params["origin"],
                "destination": params["destination"],
                "date": params["date"],
                "passengers": params.get("passengers", 1),
                "cabin_class": params.get("cabin", "economy")
            },
            "flights": [],
            "total_results": merged_count
        }

        # Convert times to 24-hour format for final output
        for f in merged:
            departure_24 = convert_to_24h_time(f.get("departure_time")) or f.get("departure_time")
            arrival_24 = convert_to_24h_time(f.get("arrival_time")) or f.get("arrival_time")
            entry = {
                "flight_number": f.get("flight_number"),
                "departure_time": departure_24,
                "arrival_time": arrival_24,
                "cash_price_usd": f.get("cash_price_usd"),
                "taxes_fees_usd": f.get("taxes_fees_usd"),
                "points_required": f.get("points_required"),
                "cpp": f.get("cpp")
            }
            output["flights"].append(entry)

        # Write atomic output to data/processed/output.json
        out_dir = pathlib.Path("data") / "processed"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "output.json"
        tmp_path = out_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2, ensure_ascii=False)
        # atomic replace
        os.replace(str(tmp_path), str(out_path))

        # keep debug dump as before (data/debug/output.json)
        _dump(output, "output")

        # Print counts to console (not included in JSON)
        print(f"\nCounts -> Cash: {cash_count}, Award: {award_count}, Merged: {merged_count}")
        print(f"Final output written to: {out_path.resolve()}")

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
            dep = flight.get("departure_time")
            arr = flight.get("arrival_time")
            if cp is None or pts is None:
                print(f"  {flight.get('flight_number')} - partial data")
            else:
                print(f"  {flight['flight_number']}: {dep} → {arr} | ${cp:.2f} or {pts:,} pts → CPP: {cpp}")
if __name__ == "__main__":
    main()