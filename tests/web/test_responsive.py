"""Every page is a complete, responsive, accessible document.

The checks that need a real layout engine (no horizontal overflow at 360 px, 44 px targets,
stacked tables) live in ``tests/web/browser_audit.py``; what is asserted here is the part a
server can prove: the document shape, the state markup, and that no placeholder value leaks.
"""

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tradingagent.web import format as display

PAGES = (
    "/",
    "/positions",
    "/trades",
    "/scalping",
    "/strategies",
    "/ai-lab",
    "/risk",
    "/system",
    "/reports",
)
LEAKS = ("nan", "NaN", "None", "Decimal(", "inf ", "undefined")


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_a_responsive_document(seeded_client: TestClient, path: str) -> None:
    response = seeded_client.get(path)
    body = response.text

    assert response.status_code == 200
    assert 'name="viewport"' in body
    assert body.count("<h1") == 1
    assert body.count("<main") == 1
    assert 'class="tabbar"' in body
    assert 'aria-current="page"' in body


@pytest.mark.parametrize("path", PAGES)
def test_no_placeholder_value_leaks_into_a_page(seeded_client: TestClient, path: str) -> None:
    text = re.sub(
        r"<script.*?</script>|<style.*?</style>|<[^>]+>",
        " ",
        seeded_client.get(path).text,
        flags=re.S,
    )

    for leak in LEAKS:
        found = re.search(rf"(?<![\w-]){re.escape(leak)}", text)
        assert not found, (
            path,
            leak,
            " ".join(text[max(0, found.start() - 90) : found.end() + 60].split()) if found else "",
        )


@pytest.mark.parametrize("path", PAGES)
def test_table_headers_are_scoped(seeded_client: TestClient, path: str) -> None:
    body = seeded_client.get(path).text

    assert not re.search(r"<th(?: [^>]*)?>", body.replace("<th scope=", "<th_ok "))


@pytest.mark.parametrize("path", PAGES)
def test_no_inline_style_or_fixed_width_breaks_the_layout(
    seeded_client: TestClient, path: str
) -> None:
    body = seeded_client.get(path).text

    assert "style=" not in body
    assert not re.search(r'width="\d{4,}"', body)


@pytest.mark.parametrize("status_path", ["/nope", "/trades/999999"])
def test_error_pages_keep_the_shell_and_a_way_out(client: TestClient, status_path: str) -> None:
    response = client.get(status_path)
    body = response.text

    assert response.status_code == 404
    assert 'name="viewport"' in body
    assert body.count("<h1") == 1
    assert 'class="tabbar"' in body
    assert 'class="actions"' in body
    assert "<main" in body


def test_the_filter_forms_collapse_to_full_width_fields_on_a_phone(client: TestClient) -> None:
    css = client.get("/static/css/app.css").text

    assert "@media (max-width: 559px)" in css
    assert ".filters .field" in css


def test_a_phone_still_has_two_columns_of_cards(client: TestClient) -> None:
    css = client.get("/static/css/app.css").text

    assert "minmax(min(100%, 9.5rem), 1fr)" in css
    assert "clamp(" in css


def test_the_equity_sparkline_is_a_real_accessible_chart(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text

    # The fixtures hold too few snapshots for a curve (a deliberate threshold): the card must
    # then simply not draw one, and never an empty frame.
    if 'class="spark"' in body:
        assert 'role="img"' in body
    assert '<svg class="spark"></svg>' not in body


@pytest.mark.parametrize(
    "value",
    [None, float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")],
)
def test_a_missing_or_non_finite_figure_reads_n_a(value: float | Decimal | None) -> None:
    assert display.number(value) == display.NA
    assert display.percent(value) == display.NA
    assert display.percent_points(value) == display.NA
    assert display.r_multiple(value) == display.NA
    assert display.ratio_class(value) == ""
    if not isinstance(value, float):
        assert display.money(value) == display.NA
    if isinstance(value, float) or value is None:
        assert display.ratio(value) == display.NA


def test_dates_render_as_time_elements_but_keep_their_text() -> None:
    from datetime import UTC, datetime

    moment = datetime(2026, 10, 7, 23, 51, 32, tzinfo=UTC)
    rendered = display.TemplateDisplay().precise(moment)

    assert str(rendered) == '<time datetime="2026-10-07T23:51:32Z">2026-10-07 23:51:32</time>'
    assert display.precise(moment) == "2026-10-07 23:51:32"
    assert str(display.TemplateDisplay().precise(None)) == display.NA
