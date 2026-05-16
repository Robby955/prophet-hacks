"""Tests for forecasting.expert_pool — Hedge-style multiplicative updates."""

from forecasting.expert_pool import ExpertPool


def test_register_normalizes_weights():
    pool = ExpertPool()
    pool.register("a")
    pool.register("b")
    pool.register("c")
    weights = pool.get_weights()
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_predict_weighted_average():
    pool = ExpertPool()
    pool.register("a", 0.5)
    pool.register("b", 0.5)
    p = pool.predict({"a": 0.2, "b": 0.8})
    assert abs(p - 0.5) < 1e-9


def test_update_decreases_weight_of_bad_expert():
    pool = ExpertPool(eta=1.0, min_weight=0.0)
    pool.register("good")
    pool.register("bad")
    initial = pool.get_weights()
    # Good expert gets a perfect prediction; bad expert gets a worst-case Brier
    pool.update({"good": 0.0, "bad": 1.0})
    after = pool.get_weights()
    assert after["good"] > initial["good"]
    assert after["bad"] < initial["bad"]


def test_min_weight_floor_holds():
    pool = ExpertPool(eta=100.0, min_weight=0.05)
    pool.register("good")
    pool.register("bad")
    for _ in range(10):
        pool.update({"good": 0.0, "bad": 1.0})
    assert pool.get_weights()["bad"] >= 0.05 - 1e-9


def test_initialize_from_history_lower_brier_higher_weight():
    pool = ExpertPool(eta=1.0)
    pool.register("good")
    pool.register("bad")
    pool.initialize_from_history({"good": 0.05, "bad": 0.30})
    w = pool.get_weights()
    assert w["good"] > w["bad"]
    assert abs(sum(w.values()) - 1.0) < 1e-9
