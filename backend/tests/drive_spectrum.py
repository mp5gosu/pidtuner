"""Drive the Noise-Spectrum tab in headless Chromium: upload a log, then
exercise the per-chart frequency markers (add / independence / drag / label /
delete-one / clear-all)."""

import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8322"
LOG_FILE = sys.argv[1] if len(sys.argv) > 1 else "../example_logs/btfl_002.bbl"
OUT = "/tmp/pidtuner-shots"


def main():
    import os
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

        page.click('[data-tab="spectrum"]')
        page.wait_for_selector("#spectrum-charts .chart-box", timeout=60000)
        page.wait_for_timeout(700)

        charts = page.locator("#spectrum-charts .chart-box")
        n_charts = charts.count()
        c0, c1, c2 = charts.nth(0), charts.nth(1), charts.nth(2)
        pills = lambda c: c.locator(".spec-marker-pill")

        print(f"charts={n_charts}  starter markers per chart:",
              pills(c0).count(), pills(c1).count(), pills(c2).count())
        page.screenshot(path=f"{OUT}/spectrum-markers.png", full_page=True)

        # add two more markers on chart 0; the others must be unaffected (decoupled)
        c0.get_by_role("button", name="+ Marker").click()
        c0.get_by_role("button", name="+ Marker").click()
        page.wait_for_timeout(150)
        print("after +2 on chart0:", pills(c0).count(), pills(c1).count(), pills(c2).count())
        assert pills(c0).count() == 3, "chart0 should have 3 markers"
        assert pills(c1).count() == 1 and pills(c2).count() == 1, "other charts must be unaffected"

        # clear chart 0 only
        c0.get_by_role("button", name="Clear").click()
        page.wait_for_timeout(150)
        print("after clear chart0:", pills(c0).count(), pills(c1).count(), pills(c2).count())
        assert pills(c0).count() == 0, "chart0 markers should be cleared"
        assert pills(c1).count() == 1, "clear must not touch other charts"

        # add one, drag it, and confirm the frequency readout changes
        c0.get_by_role("button", name="+ Marker").click()
        page.wait_for_timeout(100)
        pill = pills(c0).first
        txt_before = pill.inner_text()
        pb = pill.bounding_box()
        over = c0.locator(".u-over").bounding_box()
        page.mouse.move(pb["x"] + pb["width"] / 2, pb["y"] + pb["height"] / 2)
        page.mouse.down()
        page.mouse.move(over["x"] + over["width"] * 0.6, pb["y"] + pb["height"] / 2, steps=8)
        page.mouse.up()
        page.wait_for_timeout(120)
        txt_after = pill.inner_text()
        print(f"drag: {txt_before!r} -> {txt_after!r}")
        assert "Hz" in txt_after and txt_after != txt_before, "drag should change the Hz readout"

        # label it via double-click
        pill.dblclick()
        page.wait_for_timeout(80)
        inp = c0.locator(".spec-rename")
        inp.fill("Motor")
        inp.press("Enter")
        page.wait_for_timeout(100)
        labeled = pills(c0).first.inner_text()
        print(f"labeled: {labeled!r}")
        assert labeled.startswith("Motor"), "label should show on the pill"
        page.screenshot(path=f"{OUT}/spectrum-labeled.png", full_page=True)

        # delete the single marker via its close button
        c0.locator(".spec-marker-close").first.click()
        page.wait_for_timeout(100)
        print("after ×:", pills(c0).count())
        assert pills(c0).count() == 0, "× should remove the marker"

        assert n_charts == 3, f"expected 3 charts, got {n_charts}"
        browser.close()

    if errors:
        print("CONSOLE ERRORS:")
        for e in errors:
            print("  ", e)
        sys.exit(1)
    print("OK: markers add/drag/label/delete/clear, charts independent, no console errors")


if __name__ == "__main__":
    main()
