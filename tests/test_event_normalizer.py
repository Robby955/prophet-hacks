"""Tests for forecasting.normalize — schema-tolerant normalization."""

import pytest

from forecasting.normalize import (
    detect_schema_version,
    normalize_iter,
    normalize_one,
    unwrap_payload,
)
from forecasting.schema import SchemaError, infer_yes_label


# -- Required field enforcement ------------------------------------------


def test_normalize_requires_task_id():
    with pytest.raises(SchemaError):
        normalize_one({"title": "x", "outcomes": ["Yes", "No"]})


def test_normalize_requires_title():
    with pytest.raises(SchemaError):
        normalize_one({"task_id": "t1", "outcomes": ["Yes", "No"]})


def test_normalize_requires_outcomes():
    with pytest.raises(SchemaError):
        normalize_one({"task_id": "t1", "title": "Will X happen?"})


def test_normalize_requires_two_outcomes():
    with pytest.raises(SchemaError):
        normalize_one({"task_id": "t1", "title": "Q", "outcomes": ["Yes"]})


def test_normalize_resolved_value_must_be_in_outcomes():
    with pytest.raises(SchemaError):
        normalize_one(
            {
                "task_id": "t1",
                "title": "Q",
                "outcomes": ["Yes", "No"],
                "resolved_outcome": {"value": ["Maybe"]},
            }
        )


# -- Happy path ----------------------------------------------------------


def test_normalize_basic_binary():
    t = normalize_one(
        {
            "task_id": "KX-001",
            "title": "Will X happen?",
            "outcomes": ["Yes", "No"],
            "source": "kalshi",
            "metadata": {"category": "sports"},
        }
    )
    assert t.task_id == "KX-001"
    assert t.is_binary
    assert t.yes_label == "Yes"
    assert t.category == "sports"
    assert t.raw_event_hash  # non-empty


def test_normalize_resolved_binary():
    t = normalize_one(
        {
            "task_id": "KX-002",
            "title": "Q",
            "outcomes": ["Yes", "No"],
            "resolved_outcome": {
                "value": ["Yes"],
                "resolved_at": "2026-05-15T12:00:00Z",
                "source": "KX-002",
            },
        }
    )
    assert t.is_resolved
    assert t.binary_outcome_int() == 1


def test_normalize_multi_outcome():
    t = normalize_one(
        {
            "task_id": "MULTI-1",
            "title": "Q",
            "outcomes": ["A", "B", "C"],
        }
    )
    assert not t.is_binary
    assert t.yes_label is None
    assert t.binary_outcome_int() is None


def test_normalize_alternate_id_fields():
    """Some payloads use 'id' or 'event_id' or 'market_id' instead of 'task_id'."""
    for key in ("id", "event_id", "market_id"):
        t = normalize_one({key: "abc", "title": "Q", "outcomes": ["Yes", "No"]})
        assert t.task_id == "abc"


def test_normalize_alternate_title_fields():
    for key in ("question", "name"):
        t = normalize_one({"task_id": "t1", key: "Q", "outcomes": ["Yes", "No"]})
        assert t.title == "Q"


def test_normalize_dedup_outcomes():
    t = normalize_one(
        {"task_id": "t1", "title": "Q", "outcomes": ["A", "B", "A"]}
    )
    assert list(t.outcomes) == ["A", "B"]


# -- YES label inference -------------------------------------------------


def test_infer_yes_label_explicit():
    assert infer_yes_label(["Yes", "No"]) == "Yes"
    assert infer_yes_label(["No", "Yes"]) == "Yes"


def test_infer_yes_label_above_below():
    assert infer_yes_label(["Above", "Below"]) == "Above"


def test_infer_yes_label_ambiguous():
    assert infer_yes_label(["Mahomes", "Allen"]) is None


def test_infer_yes_label_only_for_binary():
    assert infer_yes_label(["A", "B", "C"]) is None


# -- Payload unwrapping --------------------------------------------------


def test_unwrap_bare_list():
    raws = unwrap_payload([{"task_id": "t1", "title": "Q", "outcomes": ["Yes", "No"]}])
    assert len(raws) == 1


def test_unwrap_tasks_wrapper():
    raws = unwrap_payload({"tasks": [{"task_id": "t1"}, {"task_id": "t2"}]})
    assert len(raws) == 2


def test_unwrap_events_wrapper():
    raws = unwrap_payload({"events": [{"task_id": "t1"}]})
    assert len(raws) == 1


def test_unwrap_single_event_dict():
    raws = unwrap_payload({"task_id": "t1", "title": "Q", "outcomes": ["Yes", "No"]})
    assert len(raws) == 1


def test_unwrap_rejects_garbage():
    with pytest.raises(SchemaError):
        unwrap_payload("not-a-dict-or-list")


# -- Raw hash invariants -------------------------------------------------


def test_raw_hash_is_stable_across_key_order():
    a = {"task_id": "t1", "title": "Q", "outcomes": ["Yes", "No"], "source": "kalshi"}
    b = {"source": "kalshi", "outcomes": ["Yes", "No"], "title": "Q", "task_id": "t1"}
    ta = normalize_one(a)
    tb = normalize_one(b)
    assert ta.raw_event_hash == tb.raw_event_hash


def test_raw_hash_differs_on_meaningful_change():
    a = {"task_id": "t1", "title": "Q", "outcomes": ["Yes", "No"]}
    b = {"task_id": "t1", "title": "Q changed", "outcomes": ["Yes", "No"]}
    assert normalize_one(a).raw_event_hash != normalize_one(b).raw_event_hash


# -- Schema-version detection --------------------------------------------


def test_detect_dataset_v1():
    assert detect_schema_version(
        {"task_id": "t1", "outcomes": ["Yes", "No"]}
    ) == "dataset-v1"


def test_detect_pastcast_v1():
    assert detect_schema_version(
        {"event_id": "e1", "market_implied_p_yes": 0.5}
    ) == "pastcast-v1"


def test_detect_wrapper():
    assert detect_schema_version({"tasks": []}) == "wrapper-tasks"
    assert detect_schema_version({"events": []}) == "wrapper-events"
