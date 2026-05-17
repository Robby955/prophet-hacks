import json

import pytest

from scripts import bootstrap_brier_ci as ci


def test_load_prediction_map_accepts_submission_dict(tmp_path) -> None:
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps({
        "timestamp": "2026-05-16T00:00:00Z",
        "predictions": [
            {"market_ticker": "A", "p_yes": 0.2},
            {"event_ticker": "B", "p_yes": 0.8},
        ],
    }))

    assert ci.load_prediction_map(path) == {"A": 0.2, "B": 0.8}


def test_paired_losses_compute_baseline_minus_model_improvement() -> None:
    model = {"A": 0.1, "B": 0.8}
    baseline = {"A": 0.25, "B": 0.5}
    actuals = {"A": 0.0, "B": 1.0}

    records = ci.paired_losses(model, baseline, actuals)

    assert [r.market_ticker for r in records] == ["A", "B"]
    assert records[0].model_loss == pytest.approx(0.01)
    assert records[0].baseline_loss == pytest.approx(0.0625)
    assert records[0].improvement == pytest.approx(0.0525)
    assert records[1].improvement == pytest.approx(0.21)


def test_summarize_bootstrap_ci_is_seeded_and_reports_relative_reduction() -> None:
    records = [
        ci.PairedLoss("A", 0.1, 0.25, 0, 0.01, 0.0625),
        ci.PairedLoss("B", 0.8, 0.5, 1, 0.04, 0.25),
        ci.PairedLoss("C", 0.6, 0.7, 1, 0.16, 0.09),
    ]

    summary = ci.summarize(records, n_resamples=500, seed=7, ci_level=0.90)

    assert summary["n_events"] == 3
    assert summary["model_mean_brier"] == pytest.approx(0.07)
    assert summary["baseline_mean_brier"] == pytest.approx(0.1341666667)
    assert summary["mean_improvement"] == pytest.approx(0.0641666667)
    assert summary["relative_brier_reduction"] == pytest.approx(0.4782608696)
    assert summary["ci_level"] == 0.90
    assert summary["n_resamples"] == 500
    assert summary["seed"] == 7
    assert len(summary["mean_improvement_ci"]) == 2
    assert summary["mean_improvement_ci"][0] <= summary["mean_improvement"]
    assert summary["mean_improvement_ci"][1] >= summary["mean_improvement"]


def test_summarize_rejects_empty_pairs() -> None:
    with pytest.raises(ValueError, match="no paired predictions"):
        ci.summarize([], n_resamples=10, seed=1)
