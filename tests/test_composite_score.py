from scripts.composite_score import longshot_floor


def test_composite_score_longshot_floor_matches_production_cap() -> None:
    assert longshot_floor(2) == 0.10
    assert longshot_floor(3) == 0.10
    assert longshot_floor(10) == 0.05
