import pytest
from pydantic import ValidationError
from nordic_hike_planner.models import DayPlan, Edge, Hut, Trip

@pytest.fixture
def hut_a() -> Hut:
    return Hut(id="a", name="Hut A", lat=60.0, lon=7.0, elevation_m=1000,
               capacity=20, operator="test", season_start_month=1, season_end_month=12)

def test_hut_immutability(hut_a: Hut) -> None:
    with pytest.raises(ValidationError):
        hut_a.elevation_m = 9999  # type: ignore[misc]

def test_winter_wrap_season(hut_a: Hut) -> None:
    winter_hut = hut_a.model_copy(update={"season_start_month": 11, "season_end_month": 3})
    assert winter_hut.is_open_in_month(1) is True
    assert winter_hut.is_open_in_month(6) is False