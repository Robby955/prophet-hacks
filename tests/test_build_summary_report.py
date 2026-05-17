import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import build_summary_report


def test_summary_report_uses_endpoint_brier_for_production_headline() -> None:
    summary = build_summary_report._compute_summary()

    prod = summary["per_model"]["Opus 4.7 (production)"]

    assert prod["mean_brier"] == pytest.approx(
        build_summary_report.DECOMPOSITION["phase2_opus_new_floor"],
        abs=0.001,
    )
    assert prod["mean_brier"] < summary["baselines"]["random_binary"]
    assert prod["multi_mean"] is None
