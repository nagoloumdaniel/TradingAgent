"""The shell: floating glass navigation, a frozen sidebar, one theme button, no scrollbars.

What is tested here is what a browser cannot be trusted to keep: the markup and the rules
that make the shell accessible (focus ring, drawer state, named icon), and the two decisions
that are easy to undo by accident — the *scrollbar* is hidden while the *scrolling* stays,
and the icon set is inline rather than fetched from a CDN.
"""

import re

from fastapi.testclient import TestClient


def _css(body: str) -> str:
    match = re.search(r"<style>(.*?)</style>", body, re.S)
    assert match is not None
    return match.group(1)


def test_the_navigation_is_a_single_button_theme_control(client: TestClient) -> None:
    body = client.get("/").text

    assert body.count('id="theme-toggle"') == 1
    assert body.count('class="theme-icon') == 2  # the moon and the sun, in one button


def test_the_sidebar_is_frozen_on_desktop(client: TestClient) -> None:
    css = _css(client.get("/").text)
    rule = re.search(r"nav\.side \{(.*?)\}", css, re.S)

    assert rule is not None
    assert "position: sticky" in rule.group(1)
    assert "max-height" in rule.group(1)


def test_the_sidebar_becomes_a_drawer_on_a_phone(client: TestClient) -> None:
    css = _css(client.get("/").text)

    assert "@media (max-width: 880px)" in css
    assert "position: fixed" in css
    assert "transform: translateX(-102%)" in css
    assert "visibility: hidden" in css  # a closed drawer is not in the tab order
    assert 'body[data-drawer="open"] nav.side' in css


def test_the_drawer_has_a_control_and_a_way_out(client: TestClient) -> None:
    body = client.get("/").text

    assert 'id="menu-toggle"' in body
    assert 'aria-controls="side-nav"' in body
    assert 'aria-expanded="false"' in body
    assert "data-drawer-close" in body
    assert '"Escape"' in body  # Escape closes it


def test_the_document_scrollbar_is_hidden_but_the_scrolling_stays(client: TestClient) -> None:
    css = _css(client.get("/").text)

    assert "html { scrollbar-width: none; }" in css
    assert "html::-webkit-scrollbar { width: 0; height: 0; }" in css
    # Nothing sets `overflow: hidden` on the document: the page still scrolls.
    assert "html { overflow" not in css
    assert "body { overflow" not in css


def test_a_real_scroll_area_keeps_a_discreet_bar(client: TestClient) -> None:
    """Hiding the bar of a wide table would put its right-hand columns out of reach."""
    css = _css(client.get("/").text)

    assert "overflow-x: auto" in css
    assert ".table-wrap::-webkit-scrollbar-thumb" in css
    assert "scrollbar-width: thin" in css


def test_an_overflowing_table_is_reachable_from_the_keyboard(client: TestClient) -> None:
    body = client.get("/").text

    assert 'wrap.setAttribute("tabindex", "0")' in body
    assert "scrollWidth > wrap.clientWidth" in body
    assert ".table-wrap:focus-visible" in _css(body)


def test_the_icons_are_an_inline_sprite_and_not_a_cdn(client: TestClient) -> None:
    body = client.get("/").text

    assert '<svg class="sprite" hidden' in body
    assert '<symbol id="i-gauge"' in body
    assert '<use href="#i-gauge"' in body
    # No external asset is fetched: no remote script, stylesheet, image or font.
    assert 'src="http' not in body
    assert 'href="http' not in body
    assert "url(http" not in _css(body)
    assert "@import" not in _css(body)


def test_the_icon_set_says_where_it_comes_from(client: TestClient) -> None:
    """A permissive licence still asks to be credited, like the OFL fonts are."""
    body = client.get("/").text

    assert "Lucide" in body
    assert "ISC" in body


def test_every_navigation_entry_carries_an_icon(client: TestClient) -> None:
    body = client.get("/").text
    nav = re.search(r'<nav class="side".*?</nav>', body, re.S)

    assert nav is not None
    links = re.findall(r"<a\b.*?</a>", nav.group(0), re.S)
    assert len(links) == 9
    for link in links:
        assert '<use href="#i-' in link, link


def test_a_meaningful_icon_can_carry_a_name(client: TestClient) -> None:
    """Decorative icons are hidden; an icon that means something takes a label."""
    body = client.get("/").text

    assert 'role="img" aria-label=' in body or "aria-label=" in body
    assert 'aria-hidden="true"' in body
