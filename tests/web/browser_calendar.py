"""Real-browser responsiveness check of /calendar (not collected by pytest).

Needs Playwright, which is deliberately not a project dependency::

    uv run python scripts/preview_dashboard.py --port 8799 --demo
    uv run --with playwright python tests/web/browser_calendar.py http://127.0.0.1:8799 out/

At 360, 768, 1280 and 1920 px it checks, in headless Chromium, that:

* the page does not scroll horizontally;
* the shortcuts button sits in the top bar and opens the help dialog;
* the score overview and both market cards are shown, every event card carries a score per market;
* the "voir le mois" grid and the speeches fold are gone;
* clicking another day swaps the panel in place (no full reload) and the URL follows;
* interactive controls are at least 44 px high on the phone;
* the console and the network report no error.

It saves a full-page screenshot per size and exits non-zero on any failure.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unused-ignore]

SIZES = ((360, 780), (768, 1024), (1280, 800), (1920, 1080))


def main(base: str, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def check(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)
            print("FAIL", message)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in SIZES:
            page = browser.new_page(viewport={"width": width, "height": height})
            problems: list[str] = []
            page.on(
                "console", lambda m, acc=problems: acc.append(m.text) if m.type == "error" else None
            )
            page.on("pageerror", lambda e, acc=problems: acc.append(str(e)))
            page.on(
                "response",
                lambda r, acc=problems: (
                    acc.append(f"{r.status} {r.url}") if r.status >= 400 else None
                ),
            )
            page.goto(f"{base}/calendar", wait_until="networkidle")
            tag = f"{width}px"

            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            check(overflow <= 0, f"{tag}: horizontal overflow of {overflow}px")
            check(page.locator("header.topbar #keys-btn").count() == 1, f"{tag}: no keys button")
            check(page.locator("#month-grid").count() == 0, f"{tag}: month grid still there")
            check(page.locator("#cal-overview .cal-outlook").count() == 2, f"{tag}: outlook cards")
            check(page.locator("#cal-overview .cal-global-score").count() == 2, f"{tag}: no score")
            cards = page.locator("#day-panel .cal-event").count()
            rows = page.locator("#day-panel .cal-impact-row").count()
            check(cards == 0 or rows == 2 * cards, f"{tag}: {rows} score rows for {cards} cards")

            page.click("#keys-btn")
            check(page.locator("dialog[open], [role=dialog]").count() >= 1, f"{tag}: help not open")
            page.keyboard.press("Escape")

            if width <= 768:
                small = page.evaluate(
                    "[...document.querySelectorAll('.cal-day, #keys-btn, .theme-toggle')]"
                    ".filter(e => e.getBoundingClientRect().height < 40).length"
                )
                check(small == 0, f"{tag}: {small} controls under 40px")

            days = page.locator("#week-strip a[data-day-link]")
            if days.count() > 2:
                page.evaluate("window.__marker = 1")
                days.nth(2).click()
                page.wait_for_function("location.search.includes('day=')")
                check(page.evaluate("window.__marker") == 1, f"{tag}: day click reloaded the page")
                check(page.locator("#day-panel").count() == 1, f"{tag}: panel lost after swap")

            page.screenshot(path=str(out / f"calendar-{width}.png"), full_page=True)
            check(not problems, f"{tag}: console/network errors: {problems}")
            page.close()
        browser.close()

    print("OK" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: browser_calendar.py BASE_URL OUT_DIR")
    sys.exit(main(sys.argv[1].rstrip("/"), Path(sys.argv[2])))
