"""Drive the app in headless Chromium: upload a log, screenshot both tabs."""

import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8321"
LOG_FILE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic.bbl"
OUT = "/tmp/pidtuner-shots"


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE)
        page.wait_for_selector("#upload-btn")

        with page.expect_file_chooser() as fc:
            page.click("#upload-btn")
        fc.value.set_files(LOG_FILE)

        # wait for charts to appear after upload+decode
        page.wait_for_selector("#gyro-charts .chart-box", timeout=60000)
        page.wait_for_timeout(800)
        page.screenshot(path=f"{OUT}/gyro-tab.png", full_page=True)
        n_gyro = page.locator("#gyro-charts .chart-box").count()
        n_series = page.locator("#gyro-charts .u-legend .u-series").count()
        print(f"gyro tab: {n_gyro} charts, {n_series} legend series")

        # session selector present?
        opts = page.locator("#session-select option").count()
        print(f"session selector: {opts} sessions")

        # switch to step-response tab
        page.click('[data-tab="step"]')
        page.wait_for_selector("#step-charts .chart-box", timeout=60000)
        page.wait_for_timeout(500)
        page.screenshot(path=f"{OUT}/step-tab.png", full_page=True)
        n_step = page.locator("#step-charts .chart-box").count()
        metrics = page.locator("#step-charts .metrics").first.inner_text()
        print(f"step tab: {n_step} charts; roll metrics: {metrics}")

        # zoom interaction: wheel over first gyro chart
        page.click('[data-tab="gyro"]')
        chart = page.locator("#gyro-charts .chart-el .u-over").first
        box = chart.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        for _ in range(5):
            page.mouse.wheel(0, -120)
        page.wait_for_timeout(400)
        page.screenshot(path=f"{OUT}/gyro-zoomed.png", full_page=True)
        print("zoomed screenshot taken")

        browser.close()

    if errors:
        print("CONSOLE ERRORS:")
        for e in errors:
            print("  ", e)
        sys.exit(1)
    print("no console errors")


if __name__ == "__main__":
    main()
