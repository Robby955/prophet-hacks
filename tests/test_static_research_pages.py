from pathlib import Path


STATIC_PAGES = [
    Path("static/gallery_resolved.html"),
    Path("static/gallery_open.html"),
    Path("static/scatter_resolved.html"),
    Path("static/heatmap_resolved.html"),
    Path("static/abstain_slider.html"),
    Path("static/bootstrap_hist.html"),
    Path("static/pipeline_trace.html"),
]


def test_static_research_pages_have_orientation_panels() -> None:
    for path in STATIC_PAGES:
        text = path.read_text()

        assert "What you're looking at:" in text, path


def test_pipeline_trace_is_labeled_as_cached_example() -> None:
    text = Path("static/pipeline_trace.html").read_text()

    assert "Cached example:" in text
    assert "Najzer vs Ebster" in text
    assert "/demo/start" in text


def test_scatter_labels_only_large_outcome_events() -> None:
    script = Path("scripts/build_d1_scatter.py").read_text()

    assert "p.n_outcomes >= 8 ? String(p.n_outcomes) : ''" in script
    assert "textposition: pts.map" in script
