from fastapi.testclient import TestClient

import forecast_agent_server as server


def test_root_is_public_status_page() -> None:
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "ForecastPath" in response.text
    assert "Live monitoring is restricted" in response.text


def test_healthz_reports_served_variant() -> None:
    client = TestClient(server.app)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["team"]
    assert body["variant"] == server._VARIANT_NAME


def test_predict_returns_probability_shape(monkeypatch) -> None:
    def fake_variant(event: dict) -> dict:
        return {
            "p_yes": 0.7,
            "rationale": "test forecast",
            "probabilities": [
                {"market": "Yes", "probability": 0.7},
                {"market": "No", "probability": 0.3},
            ],
        }

    monkeypatch.setattr(server, "_VARIANT_FN", fake_variant)
    client = TestClient(server.app)

    response = client.post(
        "/predict",
        json={
            "event_ticker": "test-event",
            "market_ticker": "test-market",
            "title": "Will this test pass?",
            "category": "Test",
            "close_time": "2026-12-31T23:59:59Z",
            "outcomes": ["Yes", "No"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "probabilities": [
            {"market": "Yes", "probability": 0.7},
            {"market": "No", "probability": 0.3},
        ],
        "rationale": "test forecast",
    }


def test_favicon_is_empty_no_content() -> None:
    client = TestClient(server.app)

    response = client.get("/favicon.ico")

    assert response.status_code == 204
    assert response.content == b""


def test_dashboard_allows_local_access_without_token(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    client = TestClient(server.app)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "The Oracles" in response.text


def test_dashboard_requires_token_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    response = client.get("/dashboard")

    assert response.status_code == 401


def test_dashboard_accepts_query_token_and_sets_cookie(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    response = client.get("/dashboard?token=secret-token")

    assert response.status_code == 200
    assert "dashboard_token=secret-token" in response.headers["set-cookie"]


def test_predictions_require_dashboard_auth_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    missing = client.get("/predictions")
    present = client.get("/predictions", headers={"x-dashboard-token": "secret-token"})

    assert missing.status_code == 401
    assert present.status_code == 200
