import json, asyncio, re
from typing import Any, Dict, List
from playwright.async_api import async_playwright

NETWORK_KEEP = re.compile(r"(award|points|miles|fare|price|itinerary|offers?)", re.I)

async def search_and_capture(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns {"network_json": [ ... candidate JSON bodies ... ], "page_html": "<html ...>"}.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # dev: False; Docker: True
        context = await browser.new_context(
            locale="en-US",
            timezone_id="America/Los_Angeles",
        )
        page = await context.new_page()

        captured: List[Dict[str, Any]] = []
        async def on_response(resp):
            try:
                url = resp.url
                ct = resp.headers.get("content-type", "")
                if ("json" in ct.lower() or NETWORK_KEEP.search(url)):
                    j = await resp.json()
                    captured.append({"url": url, "json": j})
            except Exception:
                pass

        page.on("response", on_response)

        # 1) Go to AA.com home/search
        await page.goto("https://www.aa.com/", wait_until="domcontentloaded")

        # 2) Fill form (selectors will need verification on the live site)
        # Use visible role-based locators where possible; adjust to real ids/names.
        await page.get_by_label("From").fill(params["origin"])
        await page.get_by_label("To").fill(params["destination"])
        await page.get_by_label("Depart").click()
        await page.get_by_role("button", name=params["date"]).click()  # may require a date picker util
        await page.get_by_role("button", name="Search").click()

        # 3) Wait for results to load (container selector must be adjusted)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_selector("css=[data-test-id='resultsList'], css=[role='list']")

        html = await page.content()
        await context.close()
        await browser.close()

        return {"network_json": captured, "page_html": html}
