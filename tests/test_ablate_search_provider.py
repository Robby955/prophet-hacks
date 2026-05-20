"""Unit tests for the pure helpers in ablate_search_provider.

Importing this module also imports ``forecast_track`` (which pulls in
anthropic/httpx/dotenv) -- none of that does any network at import time, so it
is fine in tests. If the import is unavailable for some environmental reason we
skip with a clear reason rather than failing the suite.

Covered pure helpers:
  * ``_norm``            -- coerce provider rows into {title,url,snippet,domain}
  * ``_cutoff_freshness``-- "<start>to<end>" range ending the day before close
  * ``_is_suspect``      -- leakage-marker detection on a URL path

NOTE: the task brief referenced a ``_prob_vector`` helper, but that function
does not live in ablate_search_provider (its probability-vector logic is inline
in ``predict_one``, which requires the network/LLM). The genuinely pure helpers
above are tested instead.
"""

from __future__ import annotations

import pytest

ab = pytest.importorskip(
    "scripts.ablate_search_provider",
    reason="ablate_search_provider import failed (forecast_track / deps unavailable)",
)


# --------------------------------------------------------------------------
# _norm
# --------------------------------------------------------------------------
def test_norm_coerces_rows_and_extracts_domain() -> None:
    rows = [{"url": "https://www.espn.com/game/1", "title": "Preview", "snippet": "s"}]
    out = ab._norm(rows)
    assert out == [
        {
            "title": "Preview",
            "url": "https://www.espn.com/game/1",
            "snippet": "s",
            "domain": "espn.com",
        }
    ]


def test_norm_accepts_alternate_snippet_keys() -> None:
    assert ab._norm([{"url": "http://a.io/p", "description": "desc"}])[0]["snippet"] == "desc"
    assert ab._norm([{"url": "http://a.io/p", "content": "body"}])[0]["snippet"] == "body"


def test_norm_drops_rows_without_url() -> None:
    assert ab._norm([{"title": "no url"}, {"url": ""}]) == []


def test_norm_truncates_long_fields() -> None:
    row = {"url": "http://a.io/p", "title": "T" * 500, "snippet": "S" * 800}
    out = ab._norm([row])[0]
    assert len(out["title"]) == 200
    assert len(out["snippet"]) == 400


# --------------------------------------------------------------------------
# _cutoff_freshness
# --------------------------------------------------------------------------
def test_cutoff_freshness_ends_day_before_close() -> None:
    # close 2026-05-19 -> end 2026-05-18, start 120 days earlier (2026-01-18).
    assert ab._cutoff_freshness({"close_time": "2026-05-19T20:00:00Z"}) == "2026-01-18to2026-05-18"


def test_cutoff_freshness_handles_explicit_offset() -> None:
    assert (
        ab._cutoff_freshness({"close_time": "2026-05-19T20:00:00+00:00"}) == "2026-01-18to2026-05-18"
    )


def test_cutoff_freshness_missing_close_time_is_none() -> None:
    assert ab._cutoff_freshness({}) is None


def test_cutoff_freshness_bad_close_time_is_none() -> None:
    assert ab._cutoff_freshness({"close_time": "not-a-date"}) is None


# --------------------------------------------------------------------------
# _is_suspect
# --------------------------------------------------------------------------
def test_is_suspect_flags_result_markers_in_path() -> None:
    assert ab._is_suspect("https://espn.com/game/who-won-the-final") is True
    assert ab._is_suspect("https://example.com/2026/results") is True


def test_is_suspect_ignores_clean_paths() -> None:
    assert ab._is_suspect("https://espn.com/preview/matchup") is False
