"""Tests for domain models."""

import pytest
from pydantic import ValidationError

from nordic_hike_planner.models import DayPlan, Edge, Hut, Trip


@pytest.fixture
def hut_a() -> Hut:
    return Hut(
        id="a", name="Hut A", lat=60.0, lon=7.0,
        elevation_m=1000, capacity=20, operator="test",
        season_start_month=1, season_end_month=12,
    )


@pytest.fixture
def hut_b() -> Hut:
    return Hut(
        id="b", name="Hut B", lat=60.1, lon=7.1,
        elevation_m=1100, capacity=20, operator="test",
        season_start_month=1, season_end_month=12,
    )


class TestHut:
    def test_construction_valid(self, hut_a: Hut) -> None:
        assert hut_a.id == "a"
        assert hut_a.elevation_m == 1000

    def test_invalid_latitude_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Hut(
                id="a", name="A", lat=91.0, lon=0.0,
                elevation_m=0, capacity=0, operator="x",
                season_start_month=1, season_end_month=12,
            )

    def test_immutable(self, hut_a: Hut) -> None:
        with pytest.raises(ValidationError):
            hut_a.elevation_m = 9999  # type: ignore[misc]

    def test_summer_season_open_in_july(self, hut_a: Hut) -> None:
        summer_hut = hut_a.model_copy(update={"season_start_month": 6, "season_end_month": 9})
        assert summer_hut.is_open_in_month(7) is True
        assert summer_hut.is_open_in_month(12) is False

    def test_winter_wrap_season(self, hut_a: Hut) -> None:
        """Hut open November through March (wrap-around)."""
        winter_hut = hut_a.model_copy(update={"season_start_month": 11, "season_end_month": 3})
        assert winter_hut.is_open_in_month(1) is True
        assert winter_hut.is_open_in_month(12) is True
        assert winter_hut.is_open_in_month(6) is False


class TestEdge:
    def test_self_loop_rejected(self) -> None:
        with pytest.raises(ValidationError, match="endpoints must differ"):
            Edge(from_hut_id="a", to_hut_id="a", distance_km=5.0, elevation_gain_m=100)

    def test_zero_distance_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Edge(from_hut_id="a", to_hut_id="b", distance_km=0.0, elevation_gain_m=100)


class TestTrip:
    def _make_day(self, n: int, start: Hut, end: Hut) -> DayPlan:
        return DayPlan(
            day_number=n, start_hut=start, end_hut=end,
            distance_km=10.0, elevation_gain_m=100, estimated_hours=3.0,
        )

    def test_valid_trip(self, hut_a: Hut, hut_b: Hut) -> None:
        trip = Trip(
            days=[self._make_day(1, hut_a, hut_b)],
            total_distance_km=10.0, total_elevation_gain_m=100,
            total_estimated_hours=3.0,
        )
        assert len(trip.days) == 1

    def test_discontinuous_days_rejected(self, hut_a: Hut, hut_b: Hut) -> None:
        # Day 1 ends at B, day 2 starts at A → invalid
        day1 = self._make_day(1, hut_a, hut_b)
        day2 = self._make_day(2, hut_a, hut_b)
        with pytest.raises(ValidationError, match="day 1 ended"):
            Trip(
                days=[day1, day2],
                total_distance_km=20.0, total_elevation_gain_m=200,
                total_estimated_hours=6.0,
            )

    def test_non_sequential_day_numbering_rejected(self, hut_a: Hut, hut_b: Hut) -> None:
        day1 = self._make_day(1, hut_a, hut_b)
        day3 = self._make_day(3, hut_b, hut_a)
        with pytest.raises(ValidationError, match="sequential"):
            Trip(
                days=[day1, day3],
                total_distance_km=20.0, total_elevation_gain_m=200,
                total_estimated_hours=6.0,
            )