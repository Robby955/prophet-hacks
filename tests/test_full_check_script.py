from pathlib import Path


def test_full_check_includes_brave_health_step() -> None:
    script = Path("scripts/full_check.sh").read_text()

    assert "#  11. Watcher process alive" in script
    assert 'echo "[5/11] Brave Search health"' in script
    assert '"$REPO_ROOT/scripts/brave_health.sh" --quiet' in script
    assert 'ok "Brave Search healthy"' in script
    assert 'fail "Brave Search unhealthy' in script
    assert 'echo "[11/11] watcher process alive"' in script


def test_full_check_expects_public_static_research_artifacts() -> None:
    script = Path("scripts/full_check.sh").read_text()

    assert 'echo "[10/11] public static artifact behavior"' in script
    assert '"$HOST/static/summary.html"' in script
    assert 'should be public' in script
    assert "0.118" in script
    assert "best-case" in script
    assert '"/static/summary.pdf serves 200"' in script


def test_full_check_skips_predict_by_default() -> None:
    script = Path("scripts/full_check.sh").read_text()

    assert 'SKIP_SMOKE_CALL=1' in script
    assert '--with-smoke' in script
    assert 'pass --with-smoke to call /predict once' in script
