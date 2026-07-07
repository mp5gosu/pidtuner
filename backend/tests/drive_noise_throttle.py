"""Drive the Noise-vs-Throttle tab in headless Chromium: upload a log, open the
tab, exercise the signal switcher / scale slider / <100 Hz toggle / hover
readout, and screenshot. Fails on any console/page error."""

import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8322"
LOG_FILE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synthetic.bbl"
OUT = "/tmp/pidtuner-shots"


def main():
    os.makedirs(OUT, exist_ok=True)
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE)
        page.wait_for_selector("#upload-btn")
        with page.expect_file_chooser() as fc:
            page.click("#upload-btn")
        fc.value.set_files(LOG_FILE)
        page.wait_for_selector("#gyro-charts .chart-box", timeout=90000)

        page.click('[data-tab="noise"]')
        page.wait_for_selector("#noise-charts .chart-box canvas", timeout=60000)
        page.wait_for_timeout(500)

        boxes = page.locator("#noise-charts .chart-box")
        n = boxes.count()
        seg = page.locator("#noise-controls .nt-seg-btn")
        print(f"heatmaps={n}  signal buttons={seg.count()}  "
              f"active={page.locator('#noise-controls .nt-seg-btn.active').inner_text()!r}")
        assert n == 3, f"expected 3 heatmaps, got {n}"
        page.screenshot(path=f"{OUT}/noise-throttle-raw.png", full_page=True)

        # switch to filtered
        seg.filter(has_text="filtered").click()
        page.wait_for_timeout(300)
        print("switched to:", page.locator("#noise-controls .nt-seg-btn.active").inner_text())
        page.screenshot(path=f"{OUT}/noise-throttle-filtered.png", full_page=True)

        # <100 Hz band toggle
        page.locator("#noise-controls input[type=checkbox]").check()
        page.wait_for_timeout(300)
        page.screenshot(path=f"{OUT}/noise-throttle-100hz.png", full_page=True)
        page.locator("#noise-controls input[type=checkbox]").uncheck()

        # scale slider
        slider = page.locator("#noise-controls input[type=range]")
        slider.fill("-1")
        page.wait_for_timeout(200)
        print("scale ->", page.locator("#noise-controls .nt-scale-val").inner_text())
        slider.fill("0")

        # hover readout over the first heatmap
        cv = boxes.nth(0).locator("canvas")
        bb = cv.bounding_box()
        page.mouse.move(bb["x"] + bb["width"] * 0.55, bb["y"] + bb["height"] * 0.5)
        page.wait_for_timeout(200)
        tip = boxes.nth(0).locator(".nt-tip")
        tip_txt = tip.inner_text() if tip.is_visible() else "(hidden)"
        print("hover tooltip:", repr(tip_txt))
        assert "Hz" in tip_txt and "°/s" in tip_txt, "hover readout should show Hz and °/s"

        # maximize the first heatmap
        boxes.nth(0).locator(".max-btn").click()
        page.wait_for_timeout(300)
        page.screenshot(path=f"{OUT}/noise-throttle-max.png", full_page=True)

        browser.close()

    if errors:
        print("CONSOLE ERRORS:")
        for e in errors:
            print("  ", e)
        sys.exit(1)
    print("OK: 3 heatmaps, signal switch, band toggle, scale, hover readout, maximize; no console errors")


if __name__ == "__main__":
    main()
