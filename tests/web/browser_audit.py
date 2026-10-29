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
* new charts are drawn, hover gives a tooltip, folds are closed on phone / open on desktop,
  and the overview height at 390 px is printed and capped at 3500 px;
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
SIZES = ((360, 740), (390, 844), (768, 1024), (1280, 800), (1920, 1080))
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


def check_charts(page, label: str, failures: list[str]) -> None:
    """The overview charts draw, answer to hover and keyboard, and the period selector works."""
    page.goto(page.url.split("?")[0], wait_until="load")
    page.wait_for_timeout(400)
    if page.locator("[data-chart=equity] svg.eq").count() != 1:
        failures.append(f"{label}: equity chart not drawn")
        return
    for kind in ("pnl-bars", "markets"):
        if page.locator(f"[data-chart={kind}] svg").count() < 1:
            failures.append(f"{label}: {kind} chart not drawn")
    path_all = page.locator("[data-chart=equity] svg.eq path.line").get_attribute("d")
    page.locator("[data-chart=equity] svg.eq").scroll_into_view_if_needed()
    box = page.locator("[data-chart=equity] svg.eq").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
    page.wait_for_timeout(200)
    if page.locator("[data-chart=equity] .chart-tip.on").count() != 1:
        failures.append(f"{label}: no tooltip on hover")
    page.locator("[data-chart=equity] .seg-btn", has_text="1J").click()
    page.wait_for_timeout(300)
    path_day = page.locator("[data-chart=equity] svg.eq path.line").get_attribute("d")
    pressed = page.locator("[data-chart=equity] .seg-btn[aria-pressed=true]").inner_text()
    if pressed != "1J" or path_day == path_all:
        failures.append(f"{label}: period selector did not change the curve ({pressed})")
    page.locator("[data-chart=equity] svg.eq").focus()
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(100)
    if page.locator("[data-chart=equity] .chart-tip.on").count() != 1:
        failures.append(f"{label}: no tooltip from the keyboard")
    if not page.locator("[data-chart=equity] svg.eq").get_attribute("aria-valuetext"):
        failures.append(f"{label}: slider has no value text")
    page.keyboard.press("g")
    page.keyboard.press("t")
    page.wait_for_url("**/trades")
    page.wait_for_timeout(300)
    if page.locator("[data-chart=pnl-bars] svg").count() < 1:
        failures.append(f"{label}: trades bars not drawn")


PAGE_CHARTS = {
    "/positions": ("exposure", "hbars"),
    "/risk": ("gauges", "refusals"),
    "/strategies": ("strategies",),
    "/scalping": ("buckets",),
    "/ai-lab": ("donut",),
    "/system": ("heartbeats", "latencies"),
}


def check_pages(page, label: str, path: str, failures: list[str]) -> None:
    """Every new chart is drawn (or says why not); one hover gives a tooltip or a note."""
    for kind in PAGE_CHARTS.get(path, ()):
        box = page.locator(f"[data-chart={kind}]").first
        if box.count() == 0:
            failures.append(f"{label}: {kind} container missing")
            continue
        if box.get_attribute("aria-busy") == "true":
            failures.append(f"{label}: {kind} still loading")
    if path == "/scalping":
        svg = page.locator("[data-chart=buckets] svg.bars")
        if svg.count():
            box = svg.bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] / 2)
            page.wait_for_timeout(150)
            if page.locator("[data-chart=buckets] .chart-tip.on").count() != 1:
                failures.append(f"{label}: no tooltip on the bucket chart")
    if path == "/trades/1" and page.locator("svg.replay-chart .lvl").count() == 0:
        failures.append(f"{label}: replay levels not drawn")


LAYOUT_PROBE = """() => {
  const holes = [];
  document.querySelectorAll('.dash').forEach((row) => {
    const mods = Array.from(row.children).filter((el) => el.offsetParent);
    const tops = {};
    mods.forEach((el) => { const t = Math.round(el.getBoundingClientRect().top);
      (tops[t] = tops[t] || []).push(el); });
    Object.values(tops).forEach((line) => {
      const heights = line.map((el) => el.getBoundingClientRect().height);
      if (line.length > 1 && Math.max(...heights) - Math.min(...heights) > 2) {
        holes.push(line[0].className);
      }
    });
  });
  const long = [];
  document.querySelectorAll('.table-wrap:not(.is-kv)').forEach((wrap) => {
    if (!wrap.querySelector('thead th')) return;
    const rows = Array.from(wrap.querySelectorAll('tbody tr'))
      .filter((r) => r.offsetParent && !r.querySelector('td.empty'));
    if (rows.length > 7) long.push(wrap.id || wrap.closest('.mod')?.id || 'table');
  });
  return { holes, long };
}"""


def check_layout(page, label: str, failures: list[str]) -> None:
    """On desktop modules of one row share a height, and no table shows more than 7 rows."""
    probe = page.evaluate(LAYOUT_PROBE)
    if probe["holes"]:
        failures.append(f"{label}: unequal module heights {probe['holes'][:3]}")
    if probe["long"]:
        failures.append(f"{label}: table not capped {probe['long'][:3]}")


def check_folds(page, label: str, phone: bool, failures: list[str]) -> None:
    """Folds are closed on a phone, open elsewhere, and open on activation."""
    folds = page.locator("details.fold")
    total = folds.count()
    if not total:
        return
    opened = page.locator("details.fold[open]").count()
    keep = page.locator("details.fold[data-keep]").count()
    if phone and opened > keep:
        failures.append(f"{label}: {opened - keep} fold(s) open by default on a phone")
    if not phone and opened != total:
        failures.append(f"{label}: folds closed on desktop ({opened}/{total})")
    if phone:
        first = page.locator("details.fold:not([data-keep]) > summary").first
        if first.count():
            first.scroll_into_view_if_needed()
            first.click()
            page.wait_for_timeout(120)
            if first.get_attribute("aria-expanded") != "true":
                failures.append(f"{label}: fold did not open (aria-expanded)")


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
                check_pages(page, label, path, failures)
                if width >= 1280:
                    check_layout(page, label, failures)
                if width in (390, 1280):
                    check_folds(page, label, width < 768, failures)
                if width == 390 and path == "/":
                    page.goto(base + path, wait_until="load")
                    page.wait_for_timeout(300)
                    height = page.evaluate("document.documentElement.scrollHeight")
                    print(f"overview page height at 390px: {height}px")
                    if height > 3500:
                        failures.append(f"{label}: overview too long on phone ({height}px)")
                if width == 360 and path == "/trades" and probe["stacked"] == 0:
                    failures.append(f"{label}: wide table not stacked")
                if width in (390, 1280) and path == "/":
                    check_charts(page, f"{label} charts", failures)
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
