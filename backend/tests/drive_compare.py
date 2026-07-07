"""Drive the Step-Response (compare) tab in headless Chromium: upload a
multi-session log, select several sessions, and verify the "best all-round
performance" heart badge marks exactly one session — the same one in every
per-axis metrics table."""

import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8323"
LOG_FILE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic.bbl"
OUT = "/tmp/pidtuner-shots"


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1400})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE)
        page.wait_for_selector("#upload-btn")
        with page.expect_file_chooser() as fc:
            page.click("#upload-btn")
        fc.value.set_files(LOG_FILE)
        page.wait_for_selector("#gyro-charts .chart-box", timeout=90000)

        page.click('[data-tab="compare"]')
        page.wait_for_selector("#compare-picker .picker-session", timeout=60000)

        # Select every session in the picker so there's something to rank.
        boxes = page.locator("#compare-picker .picker-session input[type=checkbox]")
        n = boxes.count()
        for i in range(n):
            cb = boxes.nth(i)
            if not cb.is_checked():
                cb.click()
                page.wait_for_timeout(400)
        page.wait_for_selector("#compare-charts .compare-table", timeout=60000)
        page.wait_for_timeout(500)

        tables = page.locator("#compare-charts .compare-table:not(.cfg-table)")
        n_tables = tables.count()
        n_selected = page.locator("#compare-picker input[type=checkbox]:checked").count()
        print(f"sessions selected={n_selected}  metrics tables={n_tables}")
        for i in range(n_tables):
            print(f"--- table {i} ---\n{tables.nth(i).inner_text()}")

        # The heart must mark exactly one session per table, and the SAME session
        # across all three axis tables (winner is an overall, not per-axis, call).
        winners = set()
        for i in range(n_tables):
            badges = tables.nth(i).locator(".best-badge")
            assert badges.count() == 1, f"table {i}: expected 1 heart, got {badges.count()}"
            row = tables.nth(i).locator("tr", has=page.locator(".best-badge")).first
            winners.add(row.locator("td").nth(1).inner_text().replace("❤️", "").strip())
        print("winner rows:", winners)
        assert len(winners) == 1, f"heart should mark one consistent session, got {winners}"

        page.screenshot(path=f"{OUT}/compare-best-badge.png", full_page=True)
        browser.close()

    if errors:
        print("CONSOLE ERRORS:")
        for e in errors:
            print("  ", e)
        sys.exit(1)
    print("OK: best-performance heart marks one consistent session across axes")


if __name__ == "__main__":
    main()
