from pathlib import Path

from scripts.check_bootstrap_seed_stability import main


def test_bootstrap_seed_stability_script_runs_with_small_resample(tmp_path) -> None:
    out = tmp_path / "seed-stability.json"

    code = main([
        "--seeds",
        "101,102,103",
        "--n-resamples",
        "200",
        "--tolerance",
        "1.0",
        "--out",
        str(out),
    ])

    assert code == 0
    payload = out.read_text()
    assert '"reports"' in payload
    assert payload.count('"seed"') == 3


def test_overnight_tasks_warns_not_to_edit_decisions() -> None:
    tasks = Path("docs/OVERNIGHT_TASKS.md").read_text()

    assert "do not touch" in tasks.lower()
    assert "docs/DECISIONS.md" in tasks
