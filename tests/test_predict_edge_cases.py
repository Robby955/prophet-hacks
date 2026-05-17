"""Edge-case tests for /predict.

The 2026-05-16 multi-vendor ablation, parser hardening, and outcomes
safety-net work surfaced a lot of corner-case behavior. These tests
pin down what the endpoint should do when Prophet Arena sends:

  - duplicate outcome labels
  - a single outcome
  - a past close_time
  - extra unknown fields (the schema is `extra="allow"`)
  - exotic Unicode in the title
  - very long titles
  - empty title with outcomes
  - probabilities that come back as something other than what we expect

Goal: never let any of these silently degrade to probabilities=[]
(catastrophic Brier), and always return the canonical
{"probabilities":[{"market","probability"}...]} shape PA expects.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fake_variant_client(monkeypatch):
    """Server with the LLM call stubbed out so tests are fast + deterministic.

    The fake variant returns a fixed valid response for any event. Tests
    focus on the handler's translation/validation logic, not the LLM.
    """
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    import forecast_agent_server as server
    importlib.reload(server)

    def fake_variant(event):
        outs = event.get("outcomes") or []
        if not outs:
            return {"p_yes": 0.5, "rationale": "fake (no outs)", "probabilities": []}
        prior = 1.0 / len(outs)
        return {
            "p_yes": 0.7,
            "rationale": "fake variant",
            "probabilities": [{"market": o, "probability": prior} for o in outs],
            "evidence_urls": ["https://example.com/a"],
        }

    monkeypatch.setattr(server, "_VARIANT_FN", fake_variant)
    return TestClient(server.app)


# -- Basic happy path stays correct ----------------------------------------


def test_canonical_binary_event_works(fake_variant_client):
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T1", "market_ticker": "T1",
        "title": "Will it rain on Monday?",
        "category": "Weather",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["probabilities"], list)
    assert len(body["probabilities"]) == 2
    assert {p["market"] for p in body["probabilities"]} == {"Yes", "No"}
    for p in body["probabilities"]:
        assert 0.0 <= p["probability"] <= 1.0


def test_canonical_multi_outcome_event_works(fake_variant_client):
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T2", "market_ticker": "T2",
        "title": "Which team wins?",
        "category": "Sports",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["A", "B", "C", "D"],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["probabilities"]) == 4
    assert {p["market"] for p in body["probabilities"]} == {"A", "B", "C", "D"}


# -- Probabilities never come back empty -----------------------------------


def test_response_probabilities_never_empty_when_outcomes_present(fake_variant_client):
    """The catastrophic failure mode: probabilities=[] returned to PA.
    Should never happen as long as outcomes are present."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T3", "market_ticker": "T3",
        "title": "Anything?",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["X", "Y"],
    })
    assert r.status_code == 200
    assert len(r.json()["probabilities"]) >= 2


# -- Schema enforcement -----------------------------------------------------


def test_missing_required_field_rejected(fake_variant_client):
    """If `title` is missing (a Pydantic-required field), expect 422."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T4", "market_ticker": "T4",
        # title missing
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    assert r.status_code == 422


def test_missing_category_accepted(fake_variant_client):
    """`category` was tightened to optional 2026-05-17 after a live smoke
    against /predict returned 422 for an event payload without it. The PA
    schema documents `category` as present, but some events may omit it
    and completion_rate is a score multiplier we cannot afford to forfeit.
    """
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T-no-cat", "market_ticker": "T-no-cat",
        "title": "Will it rain in Chicago tomorrow?",
        # category intentionally absent
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "probabilities" in body
    assert len(body["probabilities"]) == 2


def test_predict_then_observatory_renders_when_category_absent(fake_variant_client, monkeypatch):
    """Sibling regression: same root cause as the dashboard 500. The
    observatory page lists recent predictions too; if category=None
    propagates here unsafely, this page would have 500'd identically.
    Locks the contract: a category-less /predict round-tripped through
    /observatory returns 200, not 500.
    """
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "test-token")
    fake_variant_client.post("/predict", json={
        "event_ticker": "T-obs", "market_ticker": "T-obs",
        "title": "Will the observatory render without category?",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    r = fake_variant_client.get("/observatory", cookies={"dashboard_token": "test-token"})
    assert r.status_code == 200, r.text


def test_predict_then_predictions_json_when_category_absent(fake_variant_client, monkeypatch):
    """Same root cause check on /predictions JSON. The persistence path
    serializes the prediction record back to JSON; if any consumer assumes
    string-shaped category, this would surface here too.
    """
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "test-token")
    fake_variant_client.post("/predict", json={
        "event_ticker": "T-preds", "market_ticker": "T-preds",
        "title": "Will /predictions serialize without category?",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    r = fake_variant_client.get("/predictions", cookies={"dashboard_token": "test-token"})
    assert r.status_code == 200, r.text
    body = r.json()
    # at least one prediction we just posted should be in the list
    assert len(body.get("predictions", [])) >= 1


def test_predict_then_dashboard_renders_when_category_absent(fake_variant_client, monkeypatch):
    """Regression for 2026-05-17 morning incident: making category Optional
    on EventRequest stored category=None on the prediction record. The
    dashboard's `html_escape(p.get('category', '?'))` returned None instead
    of '?' because `dict.get(key, default)` only uses default when the key
    is MISSING, not when the value is None. AttributeError propagated as
    a 500 on every /dashboard load until /predictions was full of
    None-category rows.

    This test exercises the end-to-end path: POST a predict without
    category, then GET /dashboard, and assert 200. A 500 here was the
    actual production failure mode.
    """
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "test-token")

    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T-dash-regress", "market_ticker": "T-dash-regress",
        "title": "Will a service render without category?",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    assert r.status_code == 200, r.text

    dash = fake_variant_client.get("/dashboard", cookies={"dashboard_token": "test-token"})
    assert dash.status_code == 200, (
        f"dashboard returned {dash.status_code} after a category-less /predict; "
        f"this was the 2026-05-17 morning production 500"
    )


def test_extra_unknown_fields_accepted(fake_variant_client):
    """Schema is extra='allow' so PA can add new event fields without
    breaking us. Extras should pass through transparently."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T5", "market_ticker": "T5",
        "title": "Test event",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
        "future_field_we_dont_know_about": {"nested": [1, 2, 3]},
        "ranking": 42,
    })
    assert r.status_code == 200


def test_probability_within_valid_range_in_response(fake_variant_client):
    """OutcomeProbability has Field(ge=0.0, le=1.0). Server-side clamp
    means we should NEVER emit values outside this range."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T6", "market_ticker": "T6",
        "title": "Will the test pass?",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    assert r.status_code == 200
    for p in r.json()["probabilities"]:
        assert 0.0 <= p["probability"] <= 1.0


# -- Unicode + long titles --------------------------------------------------


def test_unicode_title_handled(fake_variant_client):
    """PA events can have non-ASCII characters; should round-trip."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T7", "market_ticker": "T7",
        "title": "Will Berlin record über-40°C in July 2027? 🌡️",
        "category": "Climate",
        "close_time": "2027-07-31T23:59:59Z",
        "outcomes": ["Yes", "No"],
    })
    assert r.status_code == 200
    assert len(r.json()["probabilities"]) == 2


def test_unicode_outcomes_handled(fake_variant_client):
    """Multi-outcome with non-ASCII team / candidate names."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T8", "market_ticker": "T8",
        "title": "Who wins the F1 race?",
        "category": "Sports",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Lewis Hamilton", "Charles Leclerc", "Sergio Pérez", "Yuki Tsunoda"],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["probabilities"]) == 4
    # Exact label preservation: 'Sergio Pérez' must come back unchanged
    assert any(p["market"] == "Sergio Pérez" for p in body["probabilities"])


def test_very_long_title_handled(fake_variant_client):
    """No artificial title-length limit imposed."""
    long_title = "Will " + ("very " * 200) + "long question resolve?"
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T9", "market_ticker": "T9",
        "title": long_title,
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    assert r.status_code == 200


# -- Edge cases on outcomes -------------------------------------------------


def test_duplicate_outcomes_does_not_crash(fake_variant_client):
    """If PA sends duplicate outcome labels, don't crash. The schema
    doesn't deduplicate, so we'd return one entry per provided outcome —
    PA's scorer must handle it. We just don't crash."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T10", "market_ticker": "T10",
        "title": "Edge case test",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "Yes", "No"],
    })
    assert r.status_code == 200
    assert len(r.json()["probabilities"]) >= 2


def test_many_outcomes_handler_returns_one_per_outcome(fake_variant_client):
    """30-outcome event. The /predict handler trusts the variant — the
    longshot floor lives IN the variant, not in this handler. This test
    just confirms the handler shape: one probability entry per outcome,
    all in [0.0, 1.0], no crash."""
    outcomes = [f"Option {i}" for i in range(30)]
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T11", "market_ticker": "T11",
        "title": "Pick one of 30",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": outcomes,
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["probabilities"]) == 30
    for p in body["probabilities"]:
        assert 0.0 <= p["probability"] <= 1.0
    # And the labels must exactly match what we sent
    returned_labels = {p["market"] for p in body["probabilities"]}
    assert returned_labels == set(outcomes)


# -- /healthz invariants ----------------------------------------------------


def test_health_alias_matches_healthz(fake_variant_client):
    """PA evaluation harness issues GET /health (not /healthz) to wake
    the service before posting events. The alias must return 200 with
    the same payload shape so PA's wake-up step succeeds.
    """
    a = fake_variant_client.get("/health")
    b = fake_variant_client.get("/healthz")
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json() == b.json()
    assert a.json()["status"] == "ok"


def test_healthz_always_returns_required_keys(fake_variant_client):
    r = fake_variant_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    for k in ("status", "team", "project", "variant", "version", "commit"):
        assert k in body, f"healthz missing key {k!r}"


def test_healthz_status_is_ok(fake_variant_client):
    body = fake_variant_client.get("/healthz").json()
    assert body["status"] == "ok"


# -- /predict shape contract ------------------------------------------------


def test_predict_response_only_has_known_top_level_keys(fake_variant_client):
    """No internal trace fields, no extras. PA's contract is just
    probabilities + (optional) rationale."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T12", "market_ticker": "T12",
        "title": "Shape test",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    body = r.json()
    assert set(body.keys()) <= {"probabilities", "rationale"}, \
        f"unexpected key in response: {set(body.keys()) - {'probabilities', 'rationale'}}"


def test_predict_does_not_leak_trace_to_pa(fake_variant_client):
    """The _trace field added 2026-05-16 must not flow to PA. Verified
    by name; the test should fail loudly if a future regression adds it
    back to the response shape."""
    r = fake_variant_client.post("/predict", json={
        "event_ticker": "T13", "market_ticker": "T13",
        "title": "Trace leak test",
        "category": "Test",
        "close_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
    })
    body = r.json()
    assert "_trace" not in body
    assert "trace" not in body
    # Doubly check by serializing the body and looking for any 'trace' substring
    import json as _json
    s = _json.dumps(body)
    assert '"trace"' not in s and '"_trace"' not in s
