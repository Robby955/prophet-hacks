"""Tests for forecasting.source_scoring — credibility hierarchy, staleness,
ensemble, disagreement metric."""

from datetime import datetime, timedelta, timezone

from forecasting.source_scoring import (
    Source,
    base_credibility,
    ensemble_source_quality,
    score_source,
    source_disagreement,
    staleness_multiplier,
)


def _now(offset_hours: float = 0.0) -> datetime:
    return datetime(2026, 5, 15, 18, 0, tzinfo=timezone.utc) + timedelta(
        hours=offset_hours
    )


def test_base_credibility_hierarchy():
    assert (
        base_credibility("official")
        > base_credibility("primary")
        > base_credibility("news")
        > base_credibility("analysis")
        > base_credibility("blog")
        > base_credibility("social")
    )


def test_base_credibility_unknown_fallback():
    assert base_credibility("not-a-real-type") == base_credibility("unknown")


def test_staleness_multiplier_fresh_source():
    forecast_time = _now()
    published = _now(-1.0)
    m = staleness_multiplier(published, forecast_time, horizon_to_resolution=72.0)
    assert m > 0.95


def test_staleness_multiplier_old_source():
    forecast_time = _now()
    published = _now(-200.0)  # 200 hours old
    m = staleness_multiplier(published, forecast_time, horizon_to_resolution=72.0)
    assert m < 0.5
    assert m >= 0.3  # floor


def test_score_source_combines_base_and_staleness():
    s = Source(
        url="u1",
        source_type="official",
        published_at=_now(-1.0),
        retrieved_at=_now(),
    )
    forecast_time = _now()
    score = score_source(s, forecast_time=forecast_time, horizon_to_resolution=72.0)
    # Should be close to 1.0 (official + fresh)
    assert score > 0.9


def test_ensemble_empty_returns_zero():
    assert ensemble_source_quality([]) == 0.0


def test_ensemble_one_good_source():
    sources = [
        Source(
            url="u1",
            source_type="official",
            published_at=_now(-1.0),
            retrieved_at=_now(),
        )
    ]
    q = ensemble_source_quality(
        sources, forecast_time=_now(), horizon_to_resolution=72.0
    )
    assert q > 0.9


def test_ensemble_mixed_quality():
    sources = [
        Source(url="u-official", source_type="official", published_at=_now(-1)),
        Source(url="u-social", source_type="social", published_at=_now(-1)),
    ]
    q = ensemble_source_quality(sources, forecast_time=_now())
    # Weighted mean leans on the better source's higher weight
    assert q > 0.5
    assert q < 1.0


def test_source_disagreement_unanimous_yields_zero():
    sources = [
        Source(url="u1", source_type="news", supports_yes=True, supports_no=False),
        Source(url="u2", source_type="news", supports_yes=True, supports_no=False),
    ]
    assert source_disagreement(sources) == 0.0


def test_source_disagreement_split_yields_high():
    sources = [
        Source(url="u1", source_type="news", supports_yes=True, supports_no=False),
        Source(url="u2", source_type="news", supports_yes=False, supports_no=True),
    ]
    d = source_disagreement(sources)
    # Equal weights -> minor = major -> 2*minor / total = 1.0
    assert d == 1.0
