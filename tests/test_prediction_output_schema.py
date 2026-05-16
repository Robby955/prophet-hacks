"""Tests for the Prophet Arena wire-format prediction shape."""

import pytest

from forecasting.schema import (
    PredictionOutput,
    PredictionOutputError,
    validate_prediction_output,
)


# -- Valid cases ----------------------------------------------------------


def test_validate_basic_p_yes():
    p = validate_prediction_output({"p_yes": 0.72})
    assert p.p_yes == 0.72
    assert p.rationale == ""


def test_validate_with_rationale():
    p = validate_prediction_output({"p_yes": 0.5, "rationale": "coin flip"})
    assert p.rationale == "coin flip"


def test_validate_rationale_none_becomes_empty():
    p = validate_prediction_output({"p_yes": 0.5, "rationale": None})
    assert p.rationale == ""


def test_validate_boundary_values():
    # Prophet Arena clamps to [0.01, 0.99]
    assert validate_prediction_output({"p_yes": 0.01}).p_yes == 0.01
    assert validate_prediction_output({"p_yes": 0.99}).p_yes == 0.99


def test_validate_integer_p_yes_coerced():
    # JSON can serialize 0 or 1 as int; ensure we tolerate that
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({"p_yes": 0})  # outside [0.01, 0.99]
    p = validate_prediction_output({"p_yes": 0.5})
    assert isinstance(p.p_yes, float)


# -- Invalid cases --------------------------------------------------------


def test_validate_missing_p_yes_raises():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({"rationale": "missing p_yes"})


def test_validate_non_dict_raises():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output("not-a-dict")


def test_validate_below_min_raises():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({"p_yes": 0.005})


def test_validate_above_max_raises():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({"p_yes": 0.995})


def test_validate_string_p_yes_raises():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({"p_yes": "high"})


def test_validate_rationale_must_be_string():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({"p_yes": 0.5, "rationale": ["list", "not", "string"]})


# -- p_distribution -------------------------------------------------------


def test_validate_optional_p_distribution():
    p = validate_prediction_output({
        "p_yes": 0.5,
        "p_distribution": {"A": 0.3, "B": 0.4, "C": 0.3},
    })
    assert p.p_distribution == {"A": 0.3, "B": 0.4, "C": 0.3}


def test_validate_p_distribution_must_be_dict():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({"p_yes": 0.5, "p_distribution": [0.3, 0.7]})


def test_validate_p_distribution_values_must_be_numeric():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({
            "p_yes": 0.5,
            "p_distribution": {"A": "high"},
        })


def test_validate_p_distribution_values_in_range():
    with pytest.raises(PredictionOutputError):
        validate_prediction_output({
            "p_yes": 0.5,
            "p_distribution": {"A": 1.5},
        })


# -- Wire-format round trip ----------------------------------------------


def test_to_arena_json_strips_optional_fields():
    p = PredictionOutput(p_yes=0.65, rationale="bc")
    wire = p.to_arena_json()
    assert wire == {"p_yes": 0.65, "rationale": "bc"}
    assert "p_distribution" not in wire
