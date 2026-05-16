"""Tests for the PIN-based auth flow added 2026-05-16 so Rob can share
the dashboard URL and let a visitor enter a PIN to access it, instead
of putting a long random token in the URL.

Surface:
  GET /login           HTML form
  POST /login          validate PIN, set cookie, redirect
  POST /logout         clear cookie
  GET /dashboard       redirects to /login if not authed AND request
                       is HTML AND DASHBOARD_PIN is set; else 401
  GET /predictions     always 401 JSON if not authed (API endpoint)
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def configured_client(monkeypatch):
    """Server with PIN + token configured."""
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token-xyz")
    monkeypatch.setenv("DASHBOARD_PIN", "424242")
    import forecast_agent_server as server
    importlib.reload(server)
    return TestClient(server.app, follow_redirects=False)


@pytest.fixture
def unconfigured_client(monkeypatch):
    """Server with NO auth at all."""
    monkeypatch.delenv("DASHBOARD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    import forecast_agent_server as server
    importlib.reload(server)
    return TestClient(server.app)


# -- /login renders form ----------------------------------------------------


def test_login_get_renders_form(configured_client):
    r = configured_client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text
    assert 'name="pin"' in r.text


def test_login_get_already_authed_redirects(configured_client):
    r = configured_client.get(
        "/login",
        cookies={"dashboard_token": "secret-token-xyz"},
    )
    assert r.status_code == 303
    assert r.headers["location"] in ("/dashboard", "/")


# -- POST /login -----------------------------------------------------------


def test_login_post_correct_pin_sets_cookie(configured_client):
    r = configured_client.post("/login", data={"pin": "424242", "next": "/compare"})
    assert r.status_code == 303
    assert r.headers["location"] == "/compare"
    cookie = r.headers.get("set-cookie", "")
    assert "dashboard_token=secret-token-xyz" in cookie
    assert "HttpOnly" in cookie


def test_login_post_wrong_pin_returns_error(configured_client):
    r = configured_client.post("/login", data={"pin": "000000", "next": "/dashboard"})
    assert r.status_code == 200
    assert "Wrong PIN" in r.text


def test_login_post_rejects_open_redirect(configured_client):
    """Should ignore an attacker-supplied off-site next= and use /dashboard."""
    r = configured_client.post("/login", data={"pin": "424242", "next": "https://evil.example/"})
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_login_post_rejects_protocol_relative(configured_client):
    r = configured_client.post("/login", data={"pin": "424242", "next": "//evil.example/"})
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"


def test_login_brute_force_rate_limited(configured_client):
    """After many bad attempts the response should be a lockout error,
    not a wrong-PIN error."""
    for _ in range(15):
        configured_client.post("/login", data={"pin": "000000", "next": "/dashboard"})
    r = configured_client.post("/login", data={"pin": "000000", "next": "/dashboard"})
    assert r.status_code == 200
    # one of the two rate-limit phrasings:
    assert ("Too many" in r.text) or ("Slow down" in r.text)


# -- Logout clears the cookie ----------------------------------------------


def test_logout_clears_cookie(configured_client):
    r = configured_client.post("/logout")
    assert r.status_code == 303
    cookie = r.headers.get("set-cookie", "")
    assert "dashboard_token=" in cookie
    # max-age 0 or empty value or expires in the past
    assert ('Max-Age=0' in cookie) or ('expires=' in cookie.lower())


# -- HTML routes redirect, JSON routes 401 ---------------------------------


def test_dashboard_unauthed_redirects_to_login(configured_client):
    """HTML-accepting browser → redirect to /login."""
    r = configured_client.get("/dashboard", headers={"accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
    assert "next=" in r.headers["location"]


def test_compare_unauthed_redirects_to_login(configured_client):
    r = configured_client.get("/compare", headers={"accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_compare_open_unauthed_redirects_to_login(configured_client):
    r = configured_client.get("/compare-open", headers={"accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_predictions_unauthed_returns_401_json(configured_client):
    """API endpoint should NOT redirect; should return 401."""
    r = configured_client.get("/predictions",
                              headers={"accept": "application/json"})
    assert r.status_code == 401


def test_events_unauthed_returns_401_json(configured_client):
    r = configured_client.get("/events",
                              headers={"accept": "application/json"})
    assert r.status_code == 401


def test_dashboard_authed_via_cookie(configured_client):
    """Once a user has the cookie (after PIN login), dashboard renders."""
    r = configured_client.get(
        "/dashboard",
        cookies={"dashboard_token": "secret-token-xyz"},
    )
    assert r.status_code == 200
    assert "The Oracles" in r.text


def test_dashboard_authed_via_query_token(configured_client):
    """Legacy ?token=… path still works (so old bookmarks don't break)."""
    r = configured_client.get("/dashboard?token=secret-token-xyz")
    assert r.status_code == 200


# -- No PIN configured: HTML routes 401 instead of redirect (graceful) ----


def test_no_pin_configured_returns_401_on_html(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "secret-token-xyz")
    monkeypatch.delenv("DASHBOARD_PIN", raising=False)
    import forecast_agent_server as server
    importlib.reload(server)
    client = TestClient(server.app, follow_redirects=False)
    r = client.get("/dashboard", headers={"accept": "text/html"})
    # Without PIN configured, no /login redirect target → 401 is correct.
    assert r.status_code == 401


# -- Public landing /  is still public --------------------------------------


def test_root_remains_public(configured_client):
    r = configured_client.get("/")
    assert r.status_code == 200
    assert "ForecastingPath" in r.text
