"""The report dialog: the detail endpoint behind "Voir" and the page that opens it."""

from fastapi.testclient import TestClient


def test_the_detail_of_a_report_carries_its_window_figures(seeded_client: TestClient) -> None:
    response = seeded_client.get("/reports/1/detail")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["content"]
    assert body["kpis"]["trades"] == len(body["cumulative"])
    assert round(sum(d["value"] for d in body["days"]), 2) == round(body["kpis"]["net"], 2)


def test_an_unknown_report_is_a_404_not_an_empty_dialog(seeded_client: TestClient) -> None:
    assert seeded_client.get("/reports/9999/detail").status_code == 404


def test_the_page_opens_reports_in_a_dialog_and_shows_no_default_body(
    seeded_client: TestClient,
) -> None:
    html = seeded_client.get("/reports").text
    assert 'id="report-dialog"' in html
    assert "data-report=" in html
    assert 'id="h-opened"' not in html
    assert 'id="h-exports"' not in html
