from pathlib import Path

import pytest

from nordic_hike_planner.planner import AStarPlanner, PathNotFoundError
from nordic_hike_planner.repository import JsonHutRepository


def test_planner_finds_best_route() -> None:
    # 1. Setup the planner with our real data
    data_path = Path("data/hardangervidda.json")
    repo = JsonHutRepository(data_path)
    planner = AStarPlanner(repo)

    # 2. Execute the route search
    path, total_km, total_hours = planner.find_best_route("Finsehytta", "Kjeldebu")

    # 3. Verify the math is correct
    # It should hop from Finsehytta -> Krækkja -> Kjeldebu
    assert path == ["Finsehytta", "Krækkja", "Kjeldebu"]
    assert total_km == 40.0  # (24.0 km + 16.0 km)
    assert total_hours == 12.5  # (7.5 hours + 5.0 hours)

def test_planner_raises_error_for_unknown_hut() -> None:
    data_path = Path("data/hardangervidda.json")
    repo = JsonHutRepository(data_path)
    planner = AStarPlanner(repo)

    with pytest.raises(ValueError):
        planner.find_best_route("Finsehytta", "FakeHut")