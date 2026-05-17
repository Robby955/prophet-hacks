from pathlib import Path


def test_portfolio_artifacts_document_preserves_failure_case() -> None:
    text = Path("docs/QUANT_PORTFOLIO_ARTIFACTS.md").read_text()

    required_phrases = [
        "even if the final Prophet Arena score is weak",
        "If live score disappoints",
        "docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md",
        "docs/ADVERSARIAL_REVIEW.md",
        "scripts/capture_demo_assets.sh",
        "output/playwright/",
        "Do not use these claims in public writeups",
        "market-beating",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_retrospective_template_matches_forecasting_track() -> None:
    text = Path("docs/POST_EVENT_RETROSPECTIVE_TEMPLATE.md").read_text()

    required_phrases = [
        "Team Brier",
        "Market Brier",
        "Endpoint completion rate",
        "Payload shape received",
        "Latency p50 / p95 / max",
        "Brave retrieval coverage",
        "Offline versus live gap",
        "./scripts/post_event_orchestrator.sh --actuals",
    ]

    for phrase in required_phrases:
        assert phrase in text

    stale_trading_terms = [
        "Total notional traded",
        "Final bankroll",
        "Sharpe-equivalent",
    ]
    for phrase in stale_trading_terms:
        assert phrase not in text


def test_handoff_points_to_review_and_artifact_docs() -> None:
    text = Path("docs/HANDOFF.md").read_text()

    required_phrases = [
        "docs/ADVERSARIAL_REVIEW.md",
        "docs/QUANT_PORTFOLIO_ARTIFACTS.md",
        "docs/DEMO_CAPTURE_GUIDE.md",
        "Open PR #12 review",
        "live health verified at commit `7c3f04e9`",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_status_yaml_contains_current_live_and_portfolio_state() -> None:
    text = Path("docs/STATUS.yaml").read_text()

    required_phrases = [
        'version: 5',
        'live_commit: "7c3f04e9"',
        "current_head_note:",
        "docs/QUANT_PORTFOLIO_ARTIFACTS.md",
        "output/playwright/forecast-demo/",
        "Open GitHub PR #12 changes forecast/server behavior",
        "Do not claim market-beating performance",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_agent_status_records_artifact_preservation_work() -> None:
    text = Path("docs/AGENT_STATUS.md").read_text()

    required_phrases = [
        "codex/post-event-artifact-preservation",
        "weak leaderboard result",
        "forecasting endpoint instead of stale trading-track bankroll fields",
        "No production forecast code",
    ]

    for phrase in required_phrases:
        assert phrase in text
