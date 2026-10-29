"""Real-browser responsive audit of the dashboard (not collected by pytest).

Needs Playwright, which is deliberately not a project dependency::

    uv run python scripts/preview_dashboard.py --port 8799 --demo
    uv run --with playwright python tests/web/browser_audit.py http://127.0.0.1:8799 out/

For every page at 360, 768, 1280 and 1920 px it checks, in a headless Chromium, that:

* the document does not scroll horizontally (``scrollWidth <= clientWidth``);
* nothing outside a table frame pokes past the viewport;
* every interactive control is at least 44 x 44 px on the phone and tablet sizes;
* the console and the network report no error (the 404 probe is expected to log one);
* on the phone, wide tables really are stacked cards (``.is-stack``);
* on the phone, the "Plus" sheet opens above the scrim, traps focus, makes the page inert and
  closes on Escape with the focus back on its button;
* the overview's state dot is exposed to assistive technology (role and accessible name).

It saves a full-page screenshot per page and size, then exits non-zero on any failure.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unused-ignore]

PAGES = (
    "/",
    "/positions",
    "/trades",
    "/trades/1",
    "/risk",
    "/strategies",
    "/scalping",
    "/ai-lab",
    "/reports",
    "/system",
    "/nope",
)
SIZES = ((360, 740), (768, 1024), (1280, 800), (1920, 1080))
PROBE = """() => {
  const root = document.documentElement;
  const width = root.clientWidth;
  const pokes = [];
  document.querySelectorAll('body *').forEach((el) => {
    const box = el.getBoundingClientRect();
    const fixed = getComputedStyle(el).position === 'fixed';
    if (box.width && box.right > width + 1 && !fixed
        && !el.closest('.table-wrap, .atmosphere, nav.side')) {
      pokes.push(el.tagName + '.' + el.className);
    }
  });
  const small = [];
  document.querySelectorAll('a, button, select, input, summary').forEach((el) => {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const skip = el.classList.contains('custom-select-native')
      || el.classList.contains('skip-link') || el.closest('.table-wrap');
    if (box.width && el.offsetParent && style.visibility !== 'hidden' && !skip
        && (box.height < 43.5 || box.width < 43.5)) {
      small.push(el.tagName + '.' + el.className + ' ' + Math.round(box.width)
        + 'x' + Math.round(box.height));
    }
  });
  return {
    scrollWidth: root.scrollWidth, clientWidth: width, pokes, small,
    stacked: document.querySelectorAll('.table-wrap.is-stack').length,
  };
}"""


DRAWER_PROBE = """() => {
  const nav = document.getElementById('side-nav');
  const first = nav.querySelector('a');
  const box = first.getBoundingClientRect();
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return {
    visible: getComputedStyle(nav).visibility === 'visible',
    topmost: !!hit && nav.contains(hit),
    inert: document.querySelector('main').hasAttribute('inert'),
    expanded: document.getElementById('menu-toggle').getAttribute('aria-expanded'),
  };
}"""


def check_drawer(page, label: str, failures: list[str]) -> None:
    """The phone sheet: opens above the scrim, traps Tab, closes on Escape."""
    page.goto(page.url.split("?")[0], wait_until="load")
    page.click("#menu-toggle")
    page.wait_for_timeout(350)
    state = page.evaluate(DRAWER_PROBE)
    if not (state["visible"] and state["topmost"] and state["inert"]):
        failures.append(f"{label}: drawer not usable when open {state}")
    if state["expanded"] != "true":
        failures.append(f"{label}: aria-expanded not true {state}")
    for _ in range(14):
        page.keyboard.press("Tab")
    if not page.evaluate(
        "document.getElementById('side-nav').contains(document.activeElement)"
        " || document.activeElement.id === 'menu-toggle'"
    ):
        failures.append(f"{label}: focus escaped the sheet")
    page.keyboard.press("Escape")
    page.wait_for_timeout(350)
    closed = page.evaluate(
        "({v: getComputedStyle(document.getElementById('side-nav')).visibility,"
        " f: document.activeElement.id, i: document.querySelector('main').hasAttribute('inert')})"
    )
    if closed["v"] != "hidden" or closed["f"] != "menu-toggle" or closed["i"]:
        failures.append(f"{label}: drawer did not close cleanly {closed}")


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "http://127.0.0.1:8799"
    out = Path(argv[2] if len(argv) > 2 else "browser-audit")
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in SIZES:
            context = browser.new_context(
                viewport={"width": width, "height": height}, has_touch=width < 800
            )
            page = context.new_page()
            errors: list[str] = []
            page.on("console", lambda m, e=errors: e.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda x, e=errors: e.append(str(x)))
            for path in PAGES:
                errors.clear()
                response = page.goto(base + path, wait_until="load")
                page.wait_for_timeout(250)
                probe = page.evaluate(PROBE)
                name = (path.strip("/").replace("/", "_") or "overview") + f"-{width}.png"
                page.screenshot(path=str(out / name), full_page=True)
                label = f"{width}px {path}"
                if probe["scrollWidth"] > probe["clientWidth"]:
                    failures.append(f"{label}: horizontal page scroll {probe}")
                if probe["pokes"]:
                    failures.append(f"{label}: overflow {probe['pokes'][:3]}")
                if width < 1280 and probe["small"]:
                    failures.append(f"{label}: small targets {probe['small'][:3]}")
                expected = 404 if path in ("/nope",) else 200
                if response is None or response.status != expected:
                    failures.append(f"{label}: status {response and response.status}")
                real = [e for e in errors if not (expected == 404 and "404" in e)]
                if real:
                    failures.append(f"{label}: console {real[:2]}")
                if width == 360 and path == "/trades" and probe["stacked"] == 0:
                    failures.append(f"{label}: wide table not stacked")
                if width == 360 and path == "/":
                    check_drawer(page, f"{label} drawer", failures)
                    dot = page.evaluate(
                        "(() => { const d = document.querySelector('.card .dot');"
                        " return d ? [d.getAttribute('role'), d.getAttribute('aria-label')]"
                        " : null; })()"
                    )
                    if not dot or dot[0] != "img" or not dot[1]:
                        failures.append(f"{label}: state dot not accessible {dot}")
                print(label, "ok" if not failures or label not in failures[-1] else "FAIL")
            context.close()
        browser.close()
    for failure in failures:
        print("FAIL", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
