from pathlib import Path


def test_first_call_drill_is_zero_spend_and_checks_expected_surfaces() -> None:
    script = Path("scripts/first_call_drill.sh").read_text()

    assert "No /predict call is made." in script
    assert 'curl -sS --max-time 10 "$HOST/healthz"' in script
    assert 'grep -q \'class="run-window"\'' in script
    assert "PIN only|stay behind|Detailed traces|Restricted observatory" in script
    assert "judge-facing static research HTML is public" in script
    assert "should be public for judges" in script
    assert "0\\\\.118|leakage|best-case" in script
    assert "/static/abstain_slider.html" in script
    assert "/static/pipeline_trace.html" in script
    assert '"$HOST/predictions" -H "x-dashboard-token: $TOKEN"' in script
    assert '"$HOST/demo/result/missing"' in script
    assert "pgrep -f watch_predictions" in script


def test_runbook_points_to_first_call_drill() -> None:
    runbook = Path("docs/RUNBOOK.md").read_text()

    assert "Dry-run drill before the first call" in runbook
    assert "./scripts/first_call_drill.sh --host https://agent.forecastingpath.com" in runbook
    assert "does **not** call `/predict`" in runbook
