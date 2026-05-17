from fastapi.testclient import TestClient
import time

import forecast_agent_server as server


def test_root_is_public_status_page() -> None:
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "ForecastingPath" in response.text
    assert "Public status" in response.text
    assert "Open console" in response.text


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


def test_root_uses_bounded_public_layout() -> None:
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'class="hero-shell"' in response.text
    assert 'class="hero-title"' in response.text
    assert 'class="run-window"' in response.text
    assert "<h1>ForecastingPath</h1>" not in response.text


def test_root_avoids_disclosure_notice_copy() -> None:
    client = TestClient(server.app)

    response = client.get("/")

    assert response.status_code == 200
    stale_public_copy = [
        "PIN only",
        "stay behind",
        "detailed research console",
        "Restricted observatory",
        "Detailed traces",
        "during active scoring",
    ]
    for term in stale_public_copy:
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


def test_review_requires_dashboard_auth_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    monkeypatch.setenv("DASHBOARD_PIN", "123456")
    client = TestClient(server.app, follow_redirects=False)

    response = client.get("/review", headers={"accept": "text/html"})

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert "next=%2Freview" in response.headers["location"]


def test_review_renders_private_judge_brief(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    client = TestClient(server.app)

    response = client.get("/review")

    assert response.status_code == 200
    assert "Judge review brief" in response.text
    assert "Demo script" in response.text
    assert "First Prophet Arena call" in response.text
    assert "Why not GPT-5.5?" in response.text
    assert "/static/summary.html" in response.text
    assert "/static/gallery_resolved.html" in response.text
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


def test_static_research_html_requires_dashboard_auth_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    monkeypatch.setenv("DASHBOARD_PIN", "123456")
    client = TestClient(server.app, follow_redirects=False)

    missing = client.get("/static/summary.html", headers={"accept": "text/html"})
    present = client.get(
        "/static/summary.html",
        headers={"x-dashboard-token": "secret-token"},
    )
    asset = client.get("/static/favicon.ico")

    assert missing.status_code == 303
    assert missing.headers["location"].startswith("/login")
    assert "next=%2Fstatic%2Fsummary.html" in missing.headers["location"]
    assert present.status_code == 200
    assert asset.status_code != 401
    assert asset.status_code != 303


def test_static_gallery_html_requires_dashboard_auth_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    missing = client.get("/static/gallery_resolved.html")
    present = client.get(
        "/static/gallery_resolved.html",
        headers={"authorization": "Bearer secret-token"},
    )

    assert missing.status_code == 401
    assert present.status_code == 200
    assert "Side-by-side gallery" in present.text


def test_static_experiment_html_requires_dashboard_auth_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token")
    client = TestClient(server.app)

    protected_paths = [
        "/static/abstain_slider.html",
        "/static/bootstrap_hist.html",
        "/static/heatmap_resolved.html",
        "/static/scatter_resolved.html",
    ]
    for path in protected_paths:
        missing = client.get(path)
        present = client.get(path, headers={"authorization": "Bearer secret-token"})

        assert missing.status_code == 401
        assert present.status_code == 200


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


def test_dashboard_links_private_research_views(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    client = TestClient(server.app)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Private research views" in response.text
    assert "/review" in response.text
    assert "/observatory" in response.text
    assert "/static/summary.html" in response.text
    assert "/static/gallery_resolved.html" in response.text
    assert "/static/gallery_open.html" in response.text


def test_dashboard_copy_matches_sse_refresh_behavior(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    client = TestClient(server.app)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "The page stays live through Server-Sent Events" in response.text
    assert "Page auto-refreshes every 30s" not in response.text


def test_dashboard_renders_first_call_triage_waiting(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    original_history = list(server._PREDICTION_HISTORY)
    server._PREDICTION_HISTORY.clear()
    try:
        client = TestClient(server.app)

        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "First-call triage" in response.text
        assert "Waiting for first Prophet Arena call" in response.text
        assert "Outcome count" in response.text
        assert "Do not change production variant" in response.text
    finally:
        server._PREDICTION_HISTORY.clear()
        server._PREDICTION_HISTORY.extend(original_history)


def test_dashboard_renders_first_call_trace_summary(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    original_history = list(server._PREDICTION_HISTORY)
    server._PREDICTION_HISTORY.clear()
    server._PREDICTION_HISTORY.appendleft(
        {
            "ts": "2026-05-17T00:01:00+00:00",
            "market_ticker": "LIVE-1",
            "title": "Which outcome wins?",
            "category": "Test",
            "p_yes": 0.4,
            "rationale": "trace summary fixture",
            "outcomes": ["A", "B", "C", "D"],
            "probabilities": [
                {"market": "A", "probability": 0.4},
                {"market": "B", "probability": 0.3},
                {"market": "C", "probability": 0.2},
                {"market": "D", "probability": 0.1},
            ],
            "evidence_urls": ["https://example.com/a", "https://example.com/b"],
            "trace": {
                "parse_path": "direct",
                "latency_ms": {"total": 3210},
                "warnings": ["schema repaired"],
            },
        }
    )
    try:
        client = TestClient(server.app)

        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "First-call triage" in response.text
        assert "PA activity observed" in response.text
        assert "4 outcomes" in response.text
        assert "3210 ms" in response.text
        assert "direct" in response.text
        assert "schema repaired" in response.text
        assert "2 evidence URLs" in response.text
    finally:
        server._PREDICTION_HISTORY.clear()
        server._PREDICTION_HISTORY.extend(original_history)
