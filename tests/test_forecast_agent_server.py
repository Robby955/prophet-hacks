from fastapi.testclient import TestClient

import forecast_agent_server as server


def test_root_is_public_status_page() -> None:
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "ForecastingPath" in response.text
    # Monitor status text was reworded to fit on the same line; only the
    # access state words matter for the test.
    assert ("restricted" in response.text) or ("public" in response.text)


def test_root_does_not_expose_competition_internals() -> None:
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "ForecastingPath" in response.text
    assert "View observatory" in response.text
    sensitive_terms = [
        server._VARIANT_NAME,
        "claude-opus",
        "Claude Opus",
        "Brave Search",
        "Kalshi",
        "min(0.10",
        "GPT-5.5",
        "Gemini",
        "DECISIONS.md",
        "View source",
    ]
    for term in sensitive_terms:
        assert term not in response.text


def test_observatory_requires_dashboard_auth_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    monkeypatch.setenv("DASHBOARD_PIN", "123456")
    client = TestClient(server.app, follow_redirects=False)

    response = client.get("/observatory", headers={"accept": "text/html"})

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert "next=%2Fobservatory" in response.headers["location"]


def test_observatory_renders_private_research_console(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    client = TestClient(server.app)

    response = client.get("/observatory")

    assert response.status_code == 200
    assert "ForecastingPath Observatory" in response.text
    assert "GPT-5.5 was tried" in response.text
    assert "0.0920" in response.text
    assert "single-binary" in response.text
    assert "Public surface" in response.text
    assert server._VARIANT_NAME in response.text


def test_prediction_store_round_trips_latest_first(tmp_path) -> None:
    store_path = tmp_path / "predictions.jsonl"
    older = {
        "ts": "2026-05-17T00:00:00+00:00",
        "market_ticker": "OLD",
        "title": "Older event",
        "probabilities": [{"market": "Yes", "probability": 0.4}],
    }
    newer = {
        "ts": "2026-05-17T00:01:00+00:00",
        "market_ticker": "NEW",
        "title": "Newer event",
        "probabilities": [{"market": "Yes", "probability": 0.6}],
    }

    server._append_prediction_record(older, path=store_path)
    store_path.write_text(store_path.read_text() + "not json\n")
    server._append_prediction_record(newer, path=store_path)

    rows = server._load_prediction_history_from_disk(path=store_path, limit=2)

    assert [r["market_ticker"] for r in rows] == ["NEW", "OLD"]


def test_predict_persists_trace_and_predictions_reload_after_restart(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "live-predictions.jsonl"
    monkeypatch.setenv("PROPHET_PREDICTION_STORE_PATH", str(store_path))
    server._PREDICTION_HISTORY.clear()

    def fake_variant(event: dict) -> dict:
        return {
            "p_yes": 0.72,
            "rationale": "trace persisted",
            "probabilities": [
                {"market": "Yes", "probability": 0.72},
                {"market": "No", "probability": 0.28},
            ],
            "_trace": {
                "parse_path": "strict-json",
                "latency_ms": {"brave": 110, "llm": 900, "total": 1234},
                "warnings": ["schema repaired"],
                "brave_query": "Will this test persist?",
            },
        }

    monkeypatch.setattr(server, "_VARIANT_FN", fake_variant)
    client = TestClient(server.app)

    response = client.post(
        "/predict",
        json={
            "event_ticker": "persist-event",
            "market_ticker": "persist-market",
            "title": "Will the persisted trace reload?",
            "category": "Test",
            "close_time": "2026-12-31T23:59:59Z",
            "outcomes": ["Yes", "No"],
        },
    )
    assert response.status_code == 200

    server._PREDICTION_HISTORY.clear()
    persisted = client.get("/predictions").json()

    assert persisted["count"] == 1
    assert persisted["persisted_count"] == 1
    assert persisted["predictions"][0]["market_ticker"] == "persist-market"
    assert persisted["predictions"][0]["trace"]["parse_path"] == "strict-json"
    assert persisted["predictions"][0]["trace"]["latency_ms"]["total"] == 1234
    assert persisted["predictions"][0]["variant"] == server._VARIANT_NAME


def test_observatory_renders_persisted_prediction_trace(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "observatory-predictions.jsonl"
    monkeypatch.setenv("PROPHET_PREDICTION_STORE_PATH", str(store_path))
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    server._PREDICTION_HISTORY.clear()
    server._append_prediction_record(
        {
            "ts": "2026-05-17T00:01:00+00:00",
            "event_ticker": "obs-event",
            "market_ticker": "OBS-MKT",
            "title": "Will observatory show trace details?",
            "category": "Test",
            "p_yes": 0.62,
            "outcomes": ["Yes", "No"],
            "probabilities": [
                {"market": "Yes", "probability": 0.62},
                {"market": "No", "probability": 0.38},
            ],
            "rationale": "visible only behind auth",
            "evidence_urls": [],
            "variant": server._VARIANT_NAME,
            "commit": "abc12345",
            "trace": {
                "parse_path": "strict-json",
                "latency_ms": {"total": 987},
                "warnings": ["schema repaired"],
            },
        },
        path=store_path,
    )
    client = TestClient(server.app)

    response = client.get("/observatory")

    assert response.status_code == 200
    assert "Recent persisted predictions" in response.text
    assert "OBS-MKT" in response.text
    assert "strict-json" in response.text
    assert "987 ms" in response.text
    assert "schema repaired" in response.text


def test_healthz_reports_served_variant() -> None:
    client = TestClient(server.app)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["team"]
    assert body["variant"] == server._VARIANT_NAME
    assert "commit" in body


def test_build_commit_prefers_deploy_env(monkeypatch) -> None:
    monkeypatch.setenv("PROPHET_BUILD_COMMIT_SHA", "1234567890abcdef")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567890")

    assert server._build_commit_sha() == "12345678"


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


def test_favicon_serves_real_icon_when_static_present() -> None:
    """Favicon used to be a 204 stub; commit 84d2584 added a real icon
    served from static/favicon.ico. Test both shapes: if the static dir
    exists, expect a 200 with the icon; otherwise the 204 fallback path."""
    client = TestClient(server.app)

    response = client.get("/favicon.ico")

    if server._STATIC_DIR.exists() and (server._STATIC_DIR / "favicon.ico").exists():
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/x-icon"
        assert len(response.content) > 0
    else:
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


def test_events_stream_requires_dashboard_auth_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    response = client.get("/events")

    assert response.status_code == 401


def test_compare_renders_reliability_diagram(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)

    def fake_compare_data() -> dict:
        return {
            "models": ["Opus 4.7 (production)"],
            "summary": {
                "Opus 4.7 (production)": {"mean_brier": 0.05, "n": 3},
            },
            "events": [
                {
                    "ticker": "A",
                    "title": "Event A",
                    "category": "Sports",
                    "n_outcomes": 2,
                    "winner": "Yes",
                    "outcomes": ["Yes", "No"],
                    "models": {
                        "Opus 4.7 (production)": {
                            "p_yes": 0.80,
                            "brier": 0.04,
                            "rationale": "confident",
                            "probs": [],
                        },
                    },
                },
                {
                    "ticker": "B",
                    "title": "Event B",
                    "category": "Politics",
                    "n_outcomes": 2,
                    "winner": "No",
                    "outcomes": ["Yes", "No"],
                    "models": {
                        "Opus 4.7 (production)": {
                            "p_yes": 0.30,
                            "brier": 0.09,
                            "rationale": "lean no",
                            "probs": [],
                        },
                    },
                },
            ],
        }

    monkeypatch.setattr(server, "_load_compare_data", fake_compare_data)
    client = TestClient(server.app)

    response = client.get("/compare")

    assert response.status_code == 200
    assert "Reliability diagram" in response.text
    assert "class='reliability-chart'" in response.text
    assert "Perfect calibration" in response.text


def test_compare_open_renders_model_agreement_matrix(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    client = TestClient(server.app)

    response = client.get("/compare-open")

    assert response.status_code == 200
    assert "Model agreement matrix" in response.text
    assert "Opus 4.7 prod" in response.text
    assert "Opus 4.6" in response.text
    assert "Sonnet 4.6" in response.text
    assert "GPT-5.2" in response.text
