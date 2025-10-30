import asyncio, re
from playwright.async_api import Locator, Page, TimeoutError as PWTimeout

async def fill_airport(page: Page, input_locator: Locator, code: str, city_hint: str | None = None):
    """Type an airport code, then select it from AA's autocomplete robustly."""
    await input_locator.click()
    try:
        await input_locator.fill("")  # clear any default like SEA
    except PWTimeout:
        pass

    # Type slowly so the site fires autocomplete requests
    await input_locator.type(code, delay=70)

    # Wait for dropdown to show (AA often uses this UL)
    dropdown = page.locator("ul.ui-autocomplete").first
    listbox  = page.locator("[role='listbox']").first

    # give the suggestions up to ~3s to appear
    try:
        await asyncio.wait_for(asyncio.shield(dropdown.wait_for(state="visible", timeout=1500)), timeout=2.0)
    except Exception:
        try:
            await listbox.wait_for(state="visible", timeout=1500)
        except Exception:
            pass  # we'll try keyboard fallback

    # Try multiple ways to click a matching entry
    patterns = []
    if city_hint:
        patterns.append(re.compile(rf"\b{re.escape(code)}\b.*{re.escape(city_hint)}", re.I))
        patterns.append(re.compile(rf"{re.escape(city_hint)}.*\b{re.escape(code)}\b", re.I))
    patterns.append(re.compile(rf"\b{re.escape(code)}\b", re.I))

    # 1) role=option variant
    for pat in patterns:
        opt = page.get_by_role("option", name=pat).first
        try:
            await opt.click(timeout=800)
            return
        except PWTimeout:
            pass

    # 2) classic jQuery UI autocomplete list
    for pat in patterns:
        li = dropdown.locator("li a", has_text=pat).first
        try:
            await li.click(timeout=800)
            return
        except PWTimeout:
            pass

    # 3) Fallback: press ArrowDown then Enter to accept top suggestion
    for _ in range(2):
        try:
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            return
        except Exception:
            await asyncio.sleep(0.1)

    # 4) Last resort: type space/backspace to re-trigger, then Enter
    await input_locator.type(" ")
    await page.keyboard.press("Backspace")
    await page.keyboard.press("Enter")
