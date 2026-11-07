"""Browser acceptance check against a running disposable preview (real market API).
BASE_URL=http://127.0.0.1:8017 python tests/browser_check.py
"""

import os
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = os.environ.get("BASE_URL", "http://127.0.0.1:8017")
OUT = Path(os.environ.get("SCREENSHOT_DIR", "/tmp/mstr-observatory-preview"))
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(
        viewport={"width": 1440, "height": 1100}, device_scale_factor=1
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(URL)
    page.wait_for_function(
        "document.querySelector('#nav').textContent !== '—'", timeout=60000
    )
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(OUT / "desktop.png"), full_page=True)
    assert page.locator(".scenario").count() == 3
    assert page.locator("#sensitivity-table tbody tr").count() == 5
    assert page.locator("#source-rows tr").count() == 8
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Parameter").click()
    page.locator('input[name="preferred_stock"]').fill("14")
    assert (
        page.locator('select[name="evidence.preferred_stock.status"]').input_value()
        == "unverified"
    )
    assert (
        page.locator('input[name="evidence.preferred_stock.source_url"]').input_value()
        == ""
    )
    page.get_by_role("button", name="Muat snapshot resmi 7 Nov").click()
    page.wait_for_function(
        "document.querySelector('input[name=preferred_stock]').value === '13.136'"
    )
    page.locator('input[name="converted_debt"]').fill("1")
    page.get_by_role("button", name="Simpan & hitung ulang").click()
    page.wait_for_function(
        "document.querySelector('#form-error').textContent.includes('Conversion')"
    )
    page.locator('input[name="conversion_new_shares"]').fill("10")
    page.get_by_role("button", name="Simpan & hitung ulang").click()
    page.wait_for_function(
        "!document.querySelector('#settings-dialog').open", timeout=60000
    )
    page.get_by_role("button", name="Parameter").click()
    page.get_by_role("button", name="Muat snapshot resmi 7 Nov").click()
    page.wait_for_function(
        "document.querySelector('input[name=converted_debt]').value === '0'"
    )
    page.get_by_role("button", name="Simpan & hitung ulang").click()
    page.wait_for_function(
        "!document.querySelector('#settings-dialog').open", timeout=60000
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(OUT / "mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Parameter").click()
    assert page.locator("#settings-dialog").is_visible()
    page.screenshot(path=str(OUT / "mobile-settings.png"))
    page.get_by_role("button", name="Tutup parameter").click()
    # Read-only error-state simulation: this is UI evidence, not a live-provider failure.
    data = page.request.get(URL + "/api/valuation").json()
    data["scheduler"]["last_error"] = "SYNTHETIC UI CHECK: source unavailable"
    page.route("**/api/valuation", lambda route: route.fulfill(json=data))
    page.reload()
    page.wait_for_function(
        "document.querySelector('#notice').textContent.includes('SYNTHETIC UI CHECK')"
    )
    assert page.locator("#dashboard").is_visible()
    data["valuation"] = None
    page.reload()
    page.wait_for_selector("#empty", state="visible")
    assert page.locator("#source-rows tr").count() == 6
    assert not errors, errors
    browser.close()
print(
    "PASS: live data render; desktop/mobile overflow; evidence invalidation; snapshot load; invalid/valid conversion save; cache error and empty states. Screenshots:",
    OUT,
)
