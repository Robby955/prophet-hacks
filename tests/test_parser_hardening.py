"""Tests for the JSON-parse hardening + fuzzy outcome matching added to
forecast_track.py on 2026-05-16 after the multi-vendor ablation showed
3 of 4 alternative LLMs failing on schema-strict parsing.

Failure modes covered:
  - trailing commas before } / ]
  - smart-quote unicode
  - prose before/after the JSON
  - probability map embedded in broken outer JSON
  - outcome labels with different casing / whitespace / punctuation
"""

from __future__ import annotations

import pytest

from forecast_track import (
    _clean_loose_json,
    _match_outcome_label,
    _parse_multi_outcome_json,
)


# -- _clean_loose_json ---------------------------------------------------


def test_clean_loose_json_strips_trailing_commas():
    assert _clean_loose_json('{"a": 1, "b": 2,}') == '{"a": 1, "b": 2}'
    assert _clean_loose_json('[1, 2, 3,]') == '[1, 2, 3]'


def test_clean_loose_json_strips_trailing_comma_with_whitespace():
    assert _clean_loose_json('{"a": 1,\n}') == '{"a": 1\n}'


def test_clean_loose_json_converts_smart_quotes():
    s = "{“a”: 1, ‘b’: 2}"
    cleaned = _clean_loose_json(s)
    assert '"a"' in cleaned
    assert "'b'" in cleaned


# -- _parse_multi_outcome_json -------------------------------------------


def test_parser_handles_clean_input():
    out = _parse_multi_outcome_json('{"probabilities": {"Yes": 0.6, "No": 0.4}, "rationale": "x"}')
    assert out["probabilities"] == {"Yes": 0.6, "No": 0.4}


def test_parser_strips_code_fence():
    text = '```json\n{"probabilities": {"Yes": 0.5, "No": 0.5}, "rationale": "x"}\n```'
    out = _parse_multi_outcome_json(text)
    assert out["probabilities"]["Yes"] == 0.5


def test_parser_repairs_trailing_comma():
    """Gemini 3.1 Pro failure mode from 2026-05-16 ablation."""
    text = '```json\n{\n  "probabilities": {\n    "PSG": 0.85,\n    "Lille": 0.05,\n  },\n  "rationale": "Ligue 1"\n}\n```'
    out = _parse_multi_outcome_json(text)
    assert out["probabilities"]["PSG"] == 0.85
    assert out["probabilities"]["Lille"] == 0.05


def test_parser_repairs_smart_quotes():
    text = '{“probabilities”: {“Yes”: 0.7, “No”: 0.3}, “rationale”: “x”}'
    out = _parse_multi_outcome_json(text)
    assert out["probabilities"]["Yes"] == 0.7


def test_parser_finds_embedded_json():
    text = 'Here is my forecast:\n{"probabilities": {"A": 0.4, "B": 0.6}, "rationale": "thinking"}\nDone.'
    out = _parse_multi_outcome_json(text)
    assert out["probabilities"]["A"] == 0.4


def test_parser_last_resort_extracts_probabilities_map():
    """When the outer JSON is irreparably broken but the inner map is fine."""
    text = 'I will write: "probabilities": {"X": 0.3, "Y": 0.7} as my answer.'
    out = _parse_multi_outcome_json(text)
    assert out["probabilities"]["X"] == 0.3
    assert out["probabilities"]["Y"] == 0.7


def test_parser_raises_on_irreparable_garbage():
    with pytest.raises(ValueError):
        _parse_multi_outcome_json("this is not JSON at all, just prose")


# -- _match_outcome_label ------------------------------------------------


def test_match_exact():
    assert _match_outcome_label("Yes", ["Yes", "No"]) == "Yes"


def test_match_case_insensitive():
    assert _match_outcome_label("yes", ["Yes", "No"]) == "Yes"
    assert _match_outcome_label("NO", ["Yes", "No"]) == "No"


def test_match_strips_whitespace():
    assert _match_outcome_label(" Yes ", ["Yes", "No"]) == "Yes"


def test_match_normalizes_punctuation():
    assert _match_outcome_label("Yes!", ["Yes", "No"]) == "Yes"
    assert _match_outcome_label("Yes.", ["Yes", "No"]) == "Yes"


def test_match_returns_none_for_unknown():
    assert _match_outcome_label("Maybe", ["Yes", "No"]) is None


def test_match_returns_none_for_empty():
    assert _match_outcome_label("", ["Yes", "No"]) is None


def test_match_handles_multi_outcome_labels():
    cands = ["Kansas City Chiefs", "Philadelphia Eagles", "Other"]
    assert _match_outcome_label("kansas city chiefs", cands) == "Kansas City Chiefs"
    assert _match_outcome_label("Philadelphia Eagles", cands) == "Philadelphia Eagles"
    assert _match_outcome_label("San Francisco 49ers", cands) is None


def test_match_does_not_fuzzy_substring():
    """We deliberately do NOT do Levenshtein/substring fuzzy. 'Chief' should
    NOT match 'Kansas City Chiefs' -- silently mapping wrong outcome is worse
    than uniform prior."""
    cands = ["Kansas City Chiefs", "Philadelphia Eagles"]
    assert _match_outcome_label("Chiefs", cands) is None
    assert _match_outcome_label("Chief", cands) is None
