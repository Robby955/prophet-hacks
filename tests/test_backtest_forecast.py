import subprocess

import pytest

from scripts import backtest_forecast


def test_prophet_executable_prefers_local_venv(tmp_path, monkeypatch) -> None:
    local_prophet = tmp_path / ".venv" / "bin" / "prophet"
    local_prophet.parent.mkdir(parents=True)
    local_prophet.write_text("#!/usr/bin/env bash\n")
    monkeypatch.setattr(backtest_forecast.shutil, "which", lambda _name: "/usr/bin/prophet")

    assert backtest_forecast._prophet_executable(tmp_path) == str(local_prophet)


def test_prophet_executable_falls_back_to_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backtest_forecast.shutil, "which", lambda _name: "/usr/bin/prophet")

    assert backtest_forecast._prophet_executable(tmp_path) == "/usr/bin/prophet"


def test_prophet_executable_errors_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backtest_forecast.shutil, "which", lambda _name: None)

    with pytest.raises(FileNotFoundError, match="prophet CLI not found"):
        backtest_forecast._prophet_executable(tmp_path)


def test_evaluate_returns_error_when_prophet_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        backtest_forecast,
        "_prophet_executable",
        lambda: (_ for _ in ()).throw(FileNotFoundError("prophet CLI not found")),
    )

    result = backtest_forecast.evaluate(
        tmp_path / "predictions.json",
        tmp_path / "actuals.json",
    )

    assert result == {"error": "prophet CLI not found", "stdout": ""}


def test_evaluate_uses_resolved_prophet_path(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append((cmd, capture_output, text))
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="Predictions: 2\nMatched: 2\nBrier Score: 0.125\n",
            stderr="",
        )

    monkeypatch.setattr(backtest_forecast, "_prophet_executable", lambda: "/usr/bin/prophet")
    monkeypatch.setattr(backtest_forecast.subprocess, "run", fake_run)

    result = backtest_forecast.evaluate(
        tmp_path / "predictions.json",
        tmp_path / "actuals.json",
    )

    assert calls[0][0][0] == "/usr/bin/prophet"
    assert result["brier_score"] == 0.125
    assert result["n_matched"] == 2
