import json, pathlib, os
from src.fetch import fetch_text
from src.parse_bs4 import parse_titles_and_links

URL = os.environ.get("SCRAPE_URL", "https://example.org")

"""
TESTING SCRIPT TO CHECK INCREMENTAL PROGRESS
"""
def main():
    html = fetch_text(URL) # get textual html content
    pathlib.Path("data/raw").mkdir(parents=True, exist_ok=True)
    pathlib.Path("data/processed").mkdir(parents=True, exist_ok=True)

    # save raw html data
    (pathlib.Path("data/raw") / "page.html").write_text(html, encoding="utf-8")

    # parse and save JSON
    res = parse_titles_and_links(html)
    (pathlib.Path("data/processed") / "out.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("Scrape Complete: Information available in data/processed/out.json")


if __name__ == "__main__":
    main()

