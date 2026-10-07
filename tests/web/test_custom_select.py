"""The custom dropdowns: a styled control over a native ``<select>`` that still submits.

The dashboard has no JavaScript test runner, so what is asserted here is the contract that
would silently rot: the native control with its ``name`` and its ``label`` is still in the
markup (so the form works with JavaScript off), it is marked for enhancement, and the
enhancer still carries the keyboard and ARIA behaviour the brief requires.
"""

import pytest
from fastapi.testclient import TestClient

FILTERS = ("market", "strategy", "mode")


@pytest.mark.parametrize("name", FILTERS)
def test_each_filter_is_still_a_submitting_native_select(
    seeded_client: TestClient, name: str
) -> None:
    body = seeded_client.get("/trades").text

    assert f'<select id="{name}" name="{name}"' in body
    assert f'<label for="{name}">' in body
    assert "data-custom-select>" in body


def test_the_enhancement_is_marked_on_every_filter(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades").text

    # `data-custom-select>` and not `data-custom-select`: the enhancer's own selector
    # (`select[data-custom-select]`) lives in the base template and must not be counted.
    assert body.count("data-custom-select>") == len(FILTERS)


def test_the_form_still_filters_without_javascript(seeded_client: TestClient) -> None:
    """The markup alone must carry the whole filter: GET, the action, and a submit button."""
    body = seeded_client.get("/trades").text

    assert '<form class="filters" method="get" action="/trades">' in body
    assert '<button type="submit">Filtrer</button>' in body


@pytest.mark.parametrize(
    "marker",
    [
        "select[data-custom-select]",  # the enhancer's selector
        '"aria-haspopup", "listbox"',  # the button announces a listbox
        '"aria-expanded"',  # open state is announced
        '"Escape"',  # Escape closes
        '"ArrowDown"',  # arrows move
        '"ArrowUp"',
        '"Home"',
        '"End"',
        '"role", "listbox"',  # the options are options, not divs
        '"role", "option"',
        '"aria-selected"',
        "custom-select-native",  # the native select is hidden, never removed
        '"aria-activedescendant"',
    ],
)
def test_the_enhancer_keeps_its_accessibility_contract(
    seeded_client: TestClient, marker: str
) -> None:
    assert marker in seeded_client.get("/trades").text


def test_the_custom_control_is_styled_by_the_design_system(client: TestClient) -> None:
    body = client.get("/positions").text

    assert ".custom-select-button" in body
    assert ".custom-select-list" in body
    assert ".custom-select-option" in body
    assert ":focus-visible" in body  # the ring still applies to the button


def test_no_filter_is_a_clickable_div_alone(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades").text

    assert '<div class="custom-select"' not in body  # built at enhancement time, not in HTML
    assert 'role="button"' not in body
