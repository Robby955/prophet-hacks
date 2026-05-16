"""Tests for forecasting.dataset_loader — multi-shape input handling."""

import json
from pathlib import Path

import pytest

from forecasting.dataset_loader import LoadResult, load, load_many
from forecasting.schema import SchemaError


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"


# -- File-format support -------------------------------------------------


def test_load_binary_jsonl_fixture():
    result = load(FIXTURES / "toy_binary_events.jsonl")
    assert len(result.tasks) == 10
    assert all(t.is_binary for t in result.tasks)
    assert all(not t.is_resolved for t in result.tasks)


def test_load_resolved_jsonl_fixture():
    result = load(FIXTURES / "toy_resolved_events.jsonl")
    assert len(result.tasks) == 8
    assert all(t.is_resolved for t in result.tasks)


def test_load_multi_outcome_jsonl_fixture():
    result = load(FIXTURES / "toy_multi_outcome_events.jsonl")
    assert len(result.tasks) == 5
    assert not all(t.is_binary for t in result.tasks)


def test_load_bad_schema_strict_raises():
    with pytest.raises(SchemaError):
        load(FIXTURES / "toy_bad_schema_events.jsonl", strict=True)


def test_load_bad_schema_permissive_collects_errors():
    result = load(FIXTURES / "toy_bad_schema_events.jsonl", strict=False)
    assert result.errors
    # All 6 rows are bad
    assert len(result.errors) == 6
    assert len(result.tasks) == 0


# -- JSON list / wrapper support ----------------------------------------


def test_load_json_list(tmp_path: Path):
    events = [
        {"task_id": "t1", "title": "Q1", "outcomes": ["Yes", "No"]},
        {"task_id": "t2", "title": "Q2", "outcomes": ["Yes", "No"]},
    ]
    p = tmp_path / "events.json"
    p.write_text(json.dumps(events))
    result = load(p)
    assert len(result.tasks) == 2


def test_load_json_wrapper(tmp_path: Path):
    payload = {
        "tasks": [
            {"task_id": "t1", "title": "Q", "outcomes": ["Yes", "No"]},
            {"task_id": "t2", "title": "Q", "outcomes": ["Yes", "No"]},
        ]
    }
    p = tmp_path / "events.json"
    p.write_text(json.dumps(payload))
    result = load(p)
    assert len(result.tasks) == 2


def test_load_unrecognized_extension_raises(tmp_path: Path):
    p = tmp_path / "events.txt"
    p.write_text("nope")
    with pytest.raises(ValueError):
        load(p)


def test_load_missing_path_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "does-not-exist.json")


# -- Directory support --------------------------------------------------


def test_load_release_directory(tmp_path: Path):
    release_dir = tmp_path / "release-2026-05-12"
    release_dir.mkdir()
    (release_dir / "release.json").write_text(json.dumps(
        {"dataset": "hackathon-day", "release_id": "2026-05-12"}
    ))
    (release_dir / "tasks.jsonl").write_text(
        json.dumps({"task_id": "t1", "title": "Q", "outcomes": ["Yes", "No"]}) + "\n"
        + json.dumps({"task_id": "t2", "title": "Q", "outcomes": ["Yes", "No"]}) + "\n"
    )
    result = load(release_dir)
    assert len(result.tasks) == 2
    assert result.release_info["dataset"] == "hackathon-day"
    assert result.release_info["release_id"] == "2026-05-12"


def test_load_jsonl_directory(tmp_path: Path):
    """Sharded JSONL directory layout (events_part_000.jsonl, ...)."""
    d = tmp_path / "shards"
    d.mkdir()
    (d / "events_part_000.jsonl").write_text(
        json.dumps({"task_id": "t1", "title": "Q", "outcomes": ["Yes", "No"]}) + "\n"
    )
    (d / "events_part_001.jsonl").write_text(
        json.dumps({"task_id": "t2", "title": "Q", "outcomes": ["Yes", "No"]}) + "\n"
        + json.dumps({"task_id": "t3", "title": "Q", "outcomes": ["Yes", "No"]}) + "\n"
    )
    result = load(d)
    assert len(result.tasks) == 3


def test_load_empty_directory_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        load(tmp_path)  # empty dir, no jsonl, no release.json


# -- LoadResult summary --------------------------------------------------


def test_loadresult_summary_counts():
    result = load(FIXTURES / "toy_binary_events.jsonl")
    s = result.summary()
    assert s["total"] == 10
    assert s["binary"] == 10
    assert s["multi_outcome"] == 0
    assert s["resolved"] == 0
    assert s["unresolved"] == 10


def test_loadresult_summary_resolved_fixture():
    result = load(FIXTURES / "toy_resolved_events.jsonl")
    s = result.summary()
    assert s["resolved"] == 8


# -- load_many concatenation --------------------------------------------


def test_load_many_concatenates():
    result = load_many([
        FIXTURES / "toy_binary_events.jsonl",
        FIXTURES / "toy_resolved_events.jsonl",
    ])
    assert len(result.tasks) == 10 + 8
