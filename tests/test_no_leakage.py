"""Schema-level check that the bundled sample_tasks.jsonl is leakage-clean."""

import json
from pathlib import Path

import pytest

from evaluation.no_leakage_check import assert_no_leakage, check_no_leakage


ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "offline" / "sample_tasks.jsonl"


def _load(path: Path):
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def test_sample_dataset_has_no_leakage():
    if not SAMPLE.exists():
        pytest.skip("sample_tasks.jsonl not present")
    rows = _load(SAMPLE)
    violations = check_no_leakage(rows)
    assert violations == [], "\n".join(str(v) for v in violations)


def test_sample_dataset_has_required_fields():
    if not SAMPLE.exists():
        pytest.skip("sample_tasks.jsonl not present")
    rows = _load(SAMPLE)
    assert len(rows) >= 5
    for row in rows:
        for field in ("event_id", "market_id", "question", "forecast_time",
                      "resolution_time", "market_implied_p_yes", "outcome"):
            assert field in row, f"missing {field} in {row.get('event_id')}"
        assert row["outcome"] in (0, 1)
        assert 0.0 < row["market_implied_p_yes"] < 1.0


def test_sample_dataset_covers_longshots_and_favorites():
    if not SAMPLE.exists():
        pytest.skip("sample_tasks.jsonl not present")
    rows = _load(SAMPLE)
    prices = [r["market_implied_p_yes"] for r in rows]
    assert any(p < 0.10 for p in prices), "needs a sub-$0.10 longshot to exercise the Kalshi guard"
    assert any(p > 0.85 for p in prices), "needs a >$0.85 favorite to exercise favorites_no_shrink"


def test_assert_no_leakage_passes_on_clean():
    if not SAMPLE.exists():
        pytest.skip("sample_tasks.jsonl not present")
    rows = _load(SAMPLE)
    assert_no_leakage(rows)  # should not raise
