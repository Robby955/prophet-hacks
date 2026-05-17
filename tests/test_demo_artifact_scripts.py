from pathlib import Path


def test_capture_demo_assets_uses_clean_local_server_and_no_predict_calls() -> None:
    script = Path("scripts/capture_demo_assets.sh").read_text()

    assert "PROPHET_PREDICTION_STORE_PATH" in script
    assert "PROPHET_AGENT_VARIANT" in script
    assert "forecastingpath-walkthrough.webm" in script
    assert "01-public-root-desktop.png" in script
    assert "01b-public-root-mobile.png" in script
    assert "08-review-brief.png" in script
    assert 'goto "$BASE_URL/predict"' not in script
    assert 'goto "$BASE_URL/demo/start"' not in script
    assert 'curl "$BASE_URL/predict"' not in script


def test_submission_onepager_reads_report_without_modifying_submission() -> None:
    script = Path("scripts/build_submission_onepager.py").read_text()

    assert "submission" in script
    assert "REPORT.md" in script
    assert "DEFAULT_OUT = ROOT / \"output\" / \"pdf\"" in script
    assert "write_text" not in script
    assert "open(" not in script
