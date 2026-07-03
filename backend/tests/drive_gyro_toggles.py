"""Drive the gyro-signal-toggle UI in a real browser and verify behaviour."""

import sys

from playwright.sync_api import sync_playwright

URL = "http://localhost:8000"
LOG = "/mnt/fastlane/dev/playground/pidtuner/example_logs/btfl_003.bbl"


def shown_counts(page):
    """Per-chart count of visible series (excluding the x series), gyro tab only.

    __uplots also holds the compare/step-response charts, so filter to the
    instances whose root lives inside #gyro-charts.
    """
    return page.evaluate(
        """() => [...window.__uplots]
            .filter(u => document.getElementById('gyro-charts').contains(u.root))
            .map(u => u.series.slice(1).filter(s => s.show).length)"""
    )


def toggle_labels(page):
    return page.eval_on_selector_all(
        "#gyro-controls .signal-toggle",
        "els => els.map(e => e.textContent.trim())",
    )


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1600})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(URL)
        page.set_input_files("#file-input", LOG)
        # wait until the three gyro charts exist
        page.wait_for_function("() => (window.__uplots?.size ?? 0) >= 3", timeout=30000)
        page.wait_for_timeout(500)

        labels = toggle_labels(page)
        print("toggle checkboxes:", labels)

        before = shown_counts(page)
        print("visible series per chart (default):", before)

        # find and enable the Setpoint toggle
        sp = page.locator("#gyro-controls .signal-toggle", has_text="Setpoint")
        n_sp = sp.count()
        print("setpoint toggle present:", n_sp == 1)
        sp.locator("input").check()
        page.wait_for_timeout(300)
        after_sp = shown_counts(page)
        print("visible series per chart (+setpoint):", after_sp)

        # enable P-Term too, then screenshot
        page.locator("#gyro-controls .signal-toggle", has_text="P-Term").locator("input").check()
        page.wait_for_timeout(300)
        after_p = shown_counts(page)
        print("visible series per chart (+P-Term):", after_p)

        page.screenshot(path="/tmp/gyro_toggles.png", full_page=True)

        # turn Gyro (raw) OFF and reset the first chart, check no crash
        page.locator("#gyro-controls .signal-toggle", has_text="Gyro (raw)").locator("input").uncheck()
        page.wait_for_timeout(200)
        after_off = shown_counts(page)
        print("visible series per chart (-Gyro raw):", after_off)

        # double-click first chart overlay to reset/refit
        page.locator(".u-over").first.dblclick()
        page.wait_for_timeout(200)

        print("console/page errors:", errors if errors else "none")

        # assertions
        ok = True
        expect_labels = {"Gyro (raw)", "Gyro (filtered)", "Setpoint", "PID-Error",
                         "P-Term", "I-Term"}
        if not expect_labels.issubset(set(labels)):
            print("FAIL: missing toggle labels", expect_labels - set(labels)); ok = False
        if before != [2, 2, 2]:
            print("FAIL: default should show 2 series (filtered+unfiltered) per chart"); ok = False
        if not all(a == b + 1 for a, b in zip(after_sp, before)):
            print("FAIL: enabling setpoint should add 1 series per chart"); ok = False
        if not all(a == b + 1 for a, b in zip(after_p, after_sp)):
            print("FAIL: enabling P-Term should add 1 series per chart"); ok = False
        if not all(a == b - 1 for a, b in zip(after_off, after_p)):
            print("FAIL: disabling Gyro raw should remove 1 series per chart"); ok = False
        if errors:
            print("FAIL: console errors present"); ok = False

        print("RESULT:", "PASS" if ok else "FAIL")
        browser.close()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
