"""The shell: top bar, three navigation forms, one theme button, no horizontal page scroll.

What is tested here is what a browser cannot be trusted to keep: the markup and the rules
that make the shell responsive and accessible. Pixel-level behaviour (no horizontal
overflow at 360 px, tap targets, stacked tables) is verified with a real browser; see
``docs/web/RESPONSIVE.md``. These tests pin the contract so it cannot rot silently.
"""

import re

from fastapi.testclient import TestClient


def _css(client: TestClient) -> str:
    served = client.get("/static/css/app.css")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/css")
    return served.text


def _js(client: TestClient) -> str:
    served = client.get("/static/js/app.js")
    assert served.status_code == 200
    return served.text


def _media(css: str, query: str) -> str:
    """The bodies of every ``@media`` block opening with ``query``, concatenated."""
    opening = f"@media {query} {{"
    bodies: list[str] = []
    position = css.find(opening)
    while position != -1:
        start = position + len(opening)
        depth, index = 1, start
        while depth:
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        bodies.append(css[start : index - 1])
        position = css.find(opening, index)
    assert bodies, query
    return "\n".join(bodies)


def test_the_page_declares_a_responsive_viewport(client: TestClient) -> None:
    body = client.get("/").text

    assert 'name="viewport" content="width=device-width, initial-scale=1' in body
    assert "viewport-fit=cover" in body  # safe areas for the phone tab bar
    assert 'lang="fr"' in body


def test_the_page_has_landmarks_and_one_h1(client: TestClient) -> None:
    body = client.get("/").text

    assert body.count("<h1") == 1
    assert "<header" in body
    assert "<main" in body
    assert "<footer" in body
    assert 'aria-label="Navigation principale"' in body
    assert 'aria-label="Navigation rapide"' in body
    assert 'class="skip-link" href="#main"' in body


def test_the_navigation_is_a_single_button_theme_control(client: TestClient) -> None:
    body = client.get("/").text

    assert body.count('id="theme-toggle"') == 1
    assert body.count('class="theme-icon') == 2  # the moon and the sun, in one button


def test_the_stylesheet_and_script_are_served_locally_and_read_only(client: TestClient) -> None:
    body = client.get("/").text

    assert '<link rel="stylesheet" href="/static/css/app.css">' in body
    assert '<script src="/static/js/app.js" defer></script>' in body
    for path in ("/static/css/app.css", "/static/js/app.js"):
        assert client.post(path).status_code == 405


def test_the_palette_is_monochrome_glass_with_trading_colours_as_signals(
    client: TestClient,
) -> None:
    css = _css(client)

    assert "--canvas: #0a0a0b" in css  # near-black, dark theme
    assert "--accent: #f4f4f5" in css  # the accent is white, not the old gold
    assert "#dcb55c" not in css
    assert ':root[data-theme="light"]' in css
    assert "backdrop-filter" in css  # the glass material
    assert "@media (prefers-reduced-transparency: reduce)" in css  # opaque on request
    # Red, green and amber exist only as meaning: no rule uses them as a surface accent.
    assert "--up:" in css and "--down:" in css and "--warn:" in css


def test_no_page_names_itself_read_only_in_the_chrome(seeded_client: TestClient) -> None:
    for path in ("/", "/positions", "/risk", "/system"):
        body = seeded_client.get(path).text
        assert 'class="tag"' not in body, path
        assert "lecture seule" not in body.lower(), path
    assert "Montants en EUR" not in seeded_client.get("/").text  # footer says it once, as data


def test_the_overview_state_is_a_dot_with_an_accessible_name(
    seeded_client: TestClient,
) -> None:
    body = seeded_client.get("/").text

    assert "Trading actif : aucun arrêt en cours." not in body
    dot = re.search(r'<span class="dot" data-state="(\w+)" role="img" aria-label="([^"]+)"', body)
    assert dot is not None
    assert dot.group(1) in {"ok", "warn", "bad"}
    assert dot.group(2)


def test_a_halt_turns_the_overview_dot_red(seeded_client: TestClient, seed_halt: None) -> None:
    body = seeded_client.get("/").text

    assert re.search(r'data-state="bad" role="img" aria-label="Trading arrêté"', body)


def test_the_dot_pulses_only_when_motion_is_welcome(client: TestClient) -> None:
    css = _css(client)

    assert "@keyframes dot-pulse" in css
    assert "@media (prefers-reduced-motion: no-preference)" in _media_any(css)
    # Colour is never the only cue: the three states have three shapes.
    assert 'data-state="warn"] { background: var(--warn); border-radius: 2px;' in css
    assert 'data-state="bad"] { background: var(--down); border-radius: 3px;' in css


def _media_any(css: str) -> str:
    start = css.index("@media (prefers-reduced-motion: no-preference)")
    return css[start : start + 120]


def test_every_table_has_a_caption(seeded_client: TestClient) -> None:
    for path in (
        "/",
        "/positions",
        "/trades",
        "/risk",
        "/system",
        "/strategies",
        "/scalping",
        "/ai-lab",
        "/reports",
    ):
        body = seeded_client.get(path).text
        tables = body.count("<table")
        captions = body.count('<caption class="sr-only">')
        assert tables == captions, (path, tables, captions)


def test_the_phone_sheet_keeps_its_stacking_focus_trap_and_inert_page(
    client: TestClient,
) -> None:
    """The "Plus" sheet regressions that were fixed once: .shell must not form a stacking
    context on a phone (the sheet sits above the scrim), Tab stays in the sheet, and the page
    behind is inert while it is open."""
    css = _css(client)
    js = _js(client)
    phone = _media(css, "(max-width: 767px)")

    assert ".shell { z-index: auto; }" in phone
    sheet = re.search(r"nav\.side \{(.*?)\}", phone, re.S)
    assert sheet is not None and "z-index: 40" in sheet.group(1)
    assert re.search(r"\.drawer-scrim \{[^}]*z-index: 30", css)
    assert re.search(r"\.tabbar \{[^}]*z-index: 45", phone)
    assert 'event.key !== "Tab"' in js  # focus trap
    assert 'setAttribute("inert", "")' in js
    assert "header.topbar, main, footer" in js
    assert "restoreFocus" in js


def test_the_top_bar_is_sticky_and_full_width(client: TestClient) -> None:
    rule = re.search(r"\.topbar \{(.*?)\}", _css(client), re.S)

    assert rule is not None
    assert "position: sticky" in rule.group(1)


def test_a_phone_gets_a_bottom_tab_bar_and_a_sheet(client: TestClient) -> None:
    phone = _media(_css(client), "(max-width: 767px)")

    assert "position: fixed" in phone
    assert "grid-template-columns: repeat(5" in phone  # four destinations and "Plus"
    assert "env(safe-area-inset-bottom)" in phone
    assert "visibility: hidden" in phone  # a closed sheet is not in the tab order
    assert 'body[data-drawer="open"] nav.side' in phone
    body = client.get("/").text
    assert 'class="tabbar"' in body
    assert body.count('<nav class="side"') == 1


def test_a_tablet_gets_an_icon_rail_and_a_desktop_the_full_sidebar(client: TestClient) -> None:
    css = _css(client)

    assert "grid-template-columns: 72px minmax(0, 1fr)" in _media(css, "(min-width: 768px)")
    assert "grid-template-columns: 232px minmax(0, 1fr)" in _media(css, "(min-width: 1100px)")
    assert "position: sticky" in _media(css, "(min-width: 768px)")


def test_large_screens_get_a_wider_measure(client: TestClient) -> None:
    assert "max-width: 1760px" in _media(_css(client), "(min-width: 1800px)")


def test_the_drawer_has_a_control_and_a_way_out(client: TestClient) -> None:
    body = client.get("/").text

    assert 'id="menu-toggle"' in body
    assert 'aria-controls="side-nav"' in body
    assert 'aria-expanded="false"' in body
    assert "data-drawer-close" in body
    assert '"Escape"' in _js(client)  # Escape closes it


def test_touch_targets_are_at_least_44_pixels(client: TestClient) -> None:
    css = _css(client)

    assert "--touch: 44px" in css
    for selector in ("nav.side a", ".theme-toggle", '.filters input[type="search"]'):
        rule = re.search(rf"{re.escape(selector)} \{{(.*?)\}}", css, re.S)
        assert rule is not None, selector
        assert "var(--touch)" in rule.group(1), selector


def test_the_document_scrollbar_is_hidden_but_the_scrolling_stays(client: TestClient) -> None:
    css = _css(client)

    assert "scrollbar-width: none" in css
    assert "html::-webkit-scrollbar { width: 0; height: 0; }" in css
    assert "html { overflow" not in css
    assert "body { overflow" not in css


def test_a_wide_table_scrolls_in_its_frame_or_stacks_into_cards(client: TestClient) -> None:
    css = _css(client)

    assert "overflow-x: auto" in css
    assert ".table-wrap::-webkit-scrollbar-thumb" in css
    stacked = _media(css, "(max-width: 767px)")
    assert ".is-stack tbody td::before" in stacked
    assert "content: attr(data-label)" in stacked
    assert ".is-kv" in stacked  # key/value tables


def test_the_script_labels_cells_and_reaches_scrolling_tables(client: TestClient) -> None:
    js = _js(client)

    assert 'cell.setAttribute("data-label"' in js
    assert 'wrap.classList.add("is-stack")' in js
    assert "MutationObserver" in js  # rows pushed by the live feed are labelled too
    assert 'wrap.setAttribute("tabindex", "0")' in js
    assert "scrollWidth > wrap.clientWidth" in js
    assert ".table-wrap:focus-visible" in _css(client)


def test_motion_and_contrast_preferences_are_honoured(client: TestClient) -> None:
    css = _css(client)

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (forced-colors: active)" in css
    assert "@media (hover: none)" in css
    assert ":focus-visible" in css


def test_the_icons_are_an_inline_sprite_and_not_a_cdn(client: TestClient) -> None:
    body = client.get("/").text

    assert '<svg class="sprite" hidden' in body
    assert '<symbol id="i-gauge"' in body
    assert '<use href="#i-gauge"' in body
    assert 'src="http' not in body
    assert 'href="http' not in body
    css = _css(client)
    assert "url(http" not in css
    assert "@import" not in css


def test_the_icon_set_says_where_it_comes_from(client: TestClient) -> None:
    body = client.get("/").text

    assert "Lucide" in body
    assert "ISC" in body


def test_every_navigation_entry_carries_an_icon(client: TestClient) -> None:
    body = client.get("/").text
    nav = re.search(r'<nav class="side".*?</nav>', body, re.S)

    assert nav is not None
    links = re.findall(r"<a\b.*?</a>", nav.group(0), re.S)
    assert len(links) == 10
    for link in links:
        assert '<use href="#i-' in link, link


def test_a_meaningful_icon_can_carry_a_name(client: TestClient) -> None:
    body = client.get("/").text

    assert "aria-label=" in body
    assert 'aria-hidden="true"' in body


def test_dates_are_utc_time_elements_with_the_instant(seeded_client: TestClient) -> None:
    """Display and data stay UTC; the reader's local time is only a tooltip."""
    body = seeded_client.get("/system").text

    assert re.search(r'<time datetime="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z">', body)
    assert "Heure locale" in _js(seeded_client)
