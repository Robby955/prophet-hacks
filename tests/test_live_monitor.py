"""Tests for monitor.live_monitor.

Exercises the pure aggregation layer against synthetic JSONL. The
rendering layer (matplotlib + jinja2) is covered by a single smoke test
that asserts the rendered HTML contains the expected cards.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from monitor import live_monitor as lm


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_aggregate_counts_buys_skips_and_notional():
    rows = [
        {"timestamp": "2026-05-15T15:00:00+00:00", "action": "BUY",
         "side": "YES", "yes_edge": 0.12, "no_edge": -0.02,
         "notional": 100.0, "p_yes": 0.72, "model_provider": "ensemble",
         "tick_id": "t1", "skip_reason": None, "cost_estimate_usd": 0.01},
        {"timestamp": "2026-05-15T15:00:01+00:00", "action": "SKIP",
         "side": None, "yes_edge": 0.04, "no_edge": -0.04,
         "notional": 0.0, "p_yes": 0.54,
         "skip_reason": "edge below threshold (yes=0.04, no=-0.04)",
         "tick_id": "t1", "model_provider": "ensemble",
         "cost_estimate_usd": 0.01},
        {"timestamp": "2026-05-15T15:00:02+00:00", "action": "BUY",
         "side": "NO", "yes_edge": -0.10, "no_edge": 0.10,
         "notional": 50.0, "p_yes": 0.35, "model_provider": "ensemble",
         "tick_id": "t2", "cost_estimate_usd": 0.02},
    ]
    agg = lm.aggregate(rows)
    assert agg.total_records == 3
    assert agg.buy_count == 2
    assert agg.skip_count == 1
    assert abs(agg.total_notional - 150.0) < 1e-6
    assert abs(agg.total_cost_usd - 0.04) < 1e-6
    assert agg.skip_reasons.most_common(1)[0][0].startswith("edge below threshold")
    assert agg.tick_ids == {"t1", "t2"}
    # edges_taken: YES edge for the YES BUY (0.12), NO edge for the NO BUY (0.10)
    assert sorted(agg.edges_taken) == [0.10, 0.12]


def test_tolerates_malformed_lines(tmp_path):
    p = tmp_path / "trace" / "exp" / "tick.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        '{"action": "BUY", "notional": 10}\n'
        'this is not json\n'
        '{"action": "SKIP", "skip_reason": "x"}\n',
        encoding="utf-8",
    )
    records = list(lm.iter_records(tmp_path / "trace"))
    assert len(records) == 2
    agg = lm.aggregate(records)
    assert agg.buy_count == 1
    assert agg.skip_count == 1


def test_empty_trace_directory(tmp_path):
    agg = lm.aggregate(lm.iter_records(tmp_path / "trace"))
    assert agg.total_records == 0
    assert agg.buy_count == 0
    assert agg.skip_count == 0
    assert agg.total_notional == 0.0


def test_v2_optional_fields_flow_through():
    """Disagreement stdev and domain are only present on v2 traces; the
    aggregator must skip cleanly when they are missing and accumulate when
    they are present."""
    rows = [
        {"action": "BUY", "p_yes": 0.7},  # legacy row, no v2 fields
        {"action": "SKIP", "p_yes": 0.5, "disagreement_stdev": 0.15,
         "domain": "elections"},
        {"action": "BUY", "p_yes": 0.65, "disagreement_stdev": 0.08,
         "domain": "sports"},
    ]
    agg = lm.aggregate(rows)
    assert agg.disagreements == [0.15, 0.08]
    assert agg.domains["elections"] == 1
    assert agg.domains["sports"] == 1
    assert agg.domains["unknown"] == 1


def test_tick_latency_quantiles_compute_p50_p95():
    base = datetime(2026, 5, 15, 15, 0, 0, tzinfo=timezone.utc)
    # 1s gaps for first 19 records, then a 30s gap before the 20th
    timestamps = [base + timedelta(seconds=i) for i in range(20)]
    timestamps.append(timestamps[-1] + timedelta(seconds=30))
    result = lm.tick_latency_quantiles(timestamps)
    assert result["n"] == 21
    assert abs(result["p50"] - 1.0) < 1e-6
    # p95 of 20 gaps (19 ones plus the 30) should land on the 30s outlier
    assert result["p95"] >= 1.0


def test_render_html_contains_cards_and_charts(tmp_path):
    """Smoke test: end-to-end render should not raise and must mention the
    headline cards plus the rendered charts."""
    rows = [
        {"timestamp": "2026-05-15T15:00:00+00:00", "action": "BUY",
         "side": "YES", "yes_edge": 0.12, "no_edge": -0.02,
         "notional": 100.0, "p_yes": 0.72, "model_provider": "ensemble",
         "tick_id": "t1"},
        {"timestamp": "2026-05-15T15:00:01+00:00", "action": "SKIP",
         "yes_edge": 0.04, "no_edge": -0.04, "notional": 0.0,
         "p_yes": 0.54, "skip_reason": "edge below threshold",
         "tick_id": "t1", "model_provider": "ensemble"},
    ]
    trace_dir = tmp_path / "trace" / "exp"
    _write_jsonl(trace_dir / "tick.jsonl", rows)
    agg = lm.aggregate(lm.iter_records(tmp_path / "trace"))
    html = lm.render_html(agg, tmp_path / "trace", refresh_seconds=5)
    assert "prophet-hacks live monitor" in html
    assert "BUY" in html
    assert "Notional deployed" in html
    assert "data:image/svg+xml;base64," in html  # at least one chart embedded
    assert "auto-refresh 5s" in html


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
