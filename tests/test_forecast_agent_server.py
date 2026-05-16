from fastapi.testclient import TestClient
import time

import forecast_agent_server as server


def test_root_is_public_status_page() -> None:
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "ForecastingPath" in response.text
    # Monitor status text was reworded to fit on the same line; only the
    # access state words matter for the test.
    assert ("restricted" in response.text) or ("public" in response.text)


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


def test_demo_routes_require_dashboard_auth(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    start = client.post("/demo/start")
    stream = client.get("/demo/stream/missing")
    result = client.get("/demo/result/missing")

    assert start.status_code == 401
    assert stream.status_code == 401
    assert result.status_code == 401


def test_demo_start_caps_active_runs(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    with server._DEMO_RUN_LOCK:
        original_runs = dict(server._DEMO_RUNS)
        server._DEMO_RUNS.clear()
        for idx in range(server._DEMO_MAX_ACTIVE_RUNS):
            server._DEMO_RUNS[f"active-{idx}"] = {
                "run_id": f"active-{idx}",
                "status": "running",
                "created_at": "2026-05-16T00:00:00+00:00",
                "updated_at": "2026-05-16T00:00:00+00:00",
                "events": [],
                "result": None,
                "error": None,
            }
    try:
        client = TestClient(server.app)

        response = client.post("/demo/start", headers={"x-dashboard-token": "secret-token"})

        assert response.status_code == 429
        assert response.json()["detail"] == "too many active demo runs"
    finally:
        with server._DEMO_RUN_LOCK:
            server._DEMO_RUNS.clear()
            server._DEMO_RUNS.update(original_runs)


def test_demo_start_runs_pipeline_and_exposes_result(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")

    def fake_run(run_id: str) -> None:
        server._record_demo_event(run_id, "build_event", "running", "synthetic event ready")
        server._record_demo_event(run_id, "forecast", "running", "calling forecast variant")
        server._finish_demo_run(
            run_id,
            {
                "p_yes": 0.7,
                "rationale": "test rationale",
                "probabilities": [
                    {"market": "Yes", "probability": 0.7},
                    {"market": "No", "probability": 0.3},
                ],
                "_trace": {"latency_ms": {"total": 12}},
            },
        )

    monkeypatch.setattr(server, "_run_demo_pipeline", fake_run, raising=False)
    client = TestClient(server.app)

    started = client.post("/demo/start", headers={"x-dashboard-token": "secret-token"})

    assert started.status_code == 200
    body = started.json()
    assert body["run_id"]
    assert body["stream_url"] == f"/demo/stream/{body['run_id']}"
    assert body["result_url"] == f"/demo/result/{body['run_id']}"

    result = {}
    for _ in range(30):
        result_response = client.get(
            body["result_url"],
            headers={"x-dashboard-token": "secret-token"},
        )
        assert result_response.status_code == 200
        result = result_response.json()
        if result["status"] == "completed":
            break
        time.sleep(0.01)

    assert result["status"] == "completed"
    assert result["result"]["rationale"] == "test rationale"
    assert [event["stage"] for event in result["events"]] == [
        "queued",
        "build_event",
        "forecast",
        "completed",
    ]


def test_demo_stream_returns_sse_events(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")

    def fake_run(run_id: str) -> None:
        server._record_demo_event(run_id, "forecast", "running", "calling forecast variant")
        server._finish_demo_run(
            run_id,
            {
                "p_yes": 0.6,
                "rationale": "stream test",
                "probabilities": [{"market": "Yes", "probability": 0.6}],
            },
        )

    monkeypatch.setattr(server, "_run_demo_pipeline", fake_run, raising=False)
    client = TestClient(server.app)
    started = client.post("/demo/start", headers={"x-dashboard-token": "secret-token"})
    run_id = started.json()["run_id"]

    stream = client.get(
        f"/demo/stream/{run_id}",
        headers={"x-dashboard-token": "secret-token"},
    )

    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: demo" in stream.text
    assert '"stage": "completed"' in stream.text


def test_dashboard_contains_demo_console(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    client = TestClient(server.app)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Run pipeline demo" in response.text
    assert "/demo/start" in response.text
    assert "/demo/stream/" in response.text
    assert "/demo/result/" in response.text
