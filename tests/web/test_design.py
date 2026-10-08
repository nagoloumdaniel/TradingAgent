"""The design system's own guarantees: fonts, theme switch, and a watermark that never
costs a page.

These are not taste tests — taste is reviewed by looking. What is tested here is what
would silently break: an embedded font that stops being served, a theme switch that
disappears from the markup, and decoration that turns a clean 503 into a 500.
"""

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from tradingagent.web import queries
from tradingagent.web.app import create_app


def test_the_theme_switch_is_one_button_that_announces_its_state(client: TestClient) -> None:
    """One button, not two and not a menu: it carries both glyphs and its own state."""
    page = client.get("/")
    assert page.status_code == 200
    assert page.text.count('id="theme-toggle"') == 1
    assert "data-theme-set" not in page.text  # the old two-button group is gone
    assert 'class="theme-icon theme-icon-moon"' in page.text
    assert 'class="theme-icon theme-icon-sun"' in page.text
    assert "aria-pressed" in page.text


def test_the_theme_can_be_forced_from_the_url(client: TestClient) -> None:
    """A link may carry a theme, which is also what makes the light theme testable."""
    assert 'URLSearchParams(location.search).get("theme")' in client.get("/").text


def test_the_fonts_are_embedded_and_served_locally(client: TestClient) -> None:
    """No CDN and no network: the dashboard must look the same offline."""
    page = client.get("/")
    assert "@font-face" in page.text
    assert "/static/fonts/geist-latin.woff2" in page.text
    for name in ("geist-latin", "geist-mono-latin"):
        served = client.get(f"/static/fonts/{name}.woff2")
        assert served.status_code == 200, name
        assert served.content[:4] == b"wOF2", name
    # The licence ships with the font: OFL 1.1 requires it to travel with the files.
    assert client.get("/static/fonts/OFL.txt").status_code == 200


def test_the_brand_mark_is_still_wired(client: TestClient) -> None:
    page = client.get("/")
    assert 'src="/static/nexagold.png"' in page.text


def test_a_watermark_drawn_from_too_little_data_is_not_drawn(engine: Engine) -> None:
    """Three points is a streak across the cards, not a chart. Under the threshold the
    dashboard draws nothing rather than pretending."""
    mark = queries.watermark(engine)
    assert mark.equity_line == ""
    assert mark.candles == ()
    assert mark.width == queries.WATERMARK_WIDTH


def test_an_unmigrated_database_yields_an_empty_watermark_not_an_error() -> None:
    """The failure this guards against: decoration raising and turning a 503 into a 500."""
    broken = create_engine("sqlite://")
    try:
        mark = queries.watermark(broken)
    finally:
        broken.dispose()
    assert mark.equity_line == ""
    assert mark.candles == ()


def test_the_watermark_never_raises_on_a_database_that_lies(tmp_path) -> None:
    empty = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        try:
            queries.watermark(empty)
        except SQLAlchemyError:  # pragma: no cover - the point is that it does not happen
            raise AssertionError("the watermark must degrade, never raise") from None
    finally:
        empty.dispose()


def test_a_page_without_a_watermark_still_renders(engine: Engine) -> None:
    """The 503 and 404 paths do not go through `page()`: the base template must not assume
    the context key exists."""
    with TestClient(create_app(engine)) as client:
        assert client.get("/trades/999999").status_code == 404
