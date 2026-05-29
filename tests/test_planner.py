"""Tests for the A* planner."""

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from nordic_hike_planner.models import Hut
from nordic_hike_planner.planner import (
    AStarPlanner,
    PlanningError,
    PlanRequest,
    great_circle_km,
)
from nordic_hike_planner.repository import JsonHutRepository

TINY_DATASET = Path("tests/data/tiny_test_dataset.json")
HARDANGERVIDDA = Path("data/hardangervidda.json")


@pytest.fixture
def tiny_planner() -> AStarPlanner:
    return AStarPlanner(JsonHutRepository(TINY_DATASET))


@pytest.fixture
def real_planner() -> AStarPlanner:
    return AStarPlanner(JsonHutRepository(HARDANGERVIDDA))


class TestPlanRequest:
    def test_valid_request_constructed(self) -> None:
        req = PlanRequest(start_hut_id="finse", days=3)
        assert req.days == 3
        assert req.goal_hut_id is None

    def test_zero_days_rejected(self) -> None:
        with pytest.raises(ValueError, match="days must be >= 1"):
            PlanRequest(start_hut_id="finse", days=0)

    def test_target_exceeding_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            PlanRequest(
                start_hut_id="finse", days=3,
                target_km_per_day=30.0, max_km_per_day=25.0,
            )


class TestGreatCircleDistance:
    def test_zero_for_same_point(self) -> None:
        hut = Hut(
            id="x", name="X", lat=60.0, lon=7.0,
            elevation_m=1000, capacity=10, operator="test",
            season_start_month=1, season_end_month=12,
        )
        assert great_circle_km(hut, hut) == pytest.approx(0.0)

    def test_known_distance(self) -> None:
        # Finse to Haukeliseter is roughly 85 km as the crow flies
        finse = Hut(
            id="finse", name="Finse", lat=60.6028, lon=7.5050,
            elevation_m=1222, capacity=196, operator="private",
            season_start_month=1, season_end_month=12,
        )
        haukeli = Hut(
            id="haukeli", name="Haukeli", lat=59.8136, lon=7.4928,
            elevation_m=990, capacity=180, operator="DNT",
            season_start_month=1, season_end_month=12,
        )
        # ~88 km — allow some slack since lat/lon coords are approximate
        assert 80.0 < great_circle_km(finse, haukeli) < 95.0

    def test_symmetric(self) -> None:
        a = Hut(
            id="a", name="A", lat=60.0, lon=7.0,
            elevation_m=1000, capacity=10, operator="test",
            season_start_month=1, season_end_month=12,
        )
        b = Hut(
            id="b", name="B", lat=61.0, lon=8.0,
            elevation_m=1000, capacity=10, operator="test",
            season_start_month=1, season_end_month=12,
        )
        assert great_circle_km(a, b) == pytest.approx(great_circle_km(b, a))


class TestPlannerBasic:
    def test_single_day_plan(self, tiny_planner: AStarPlanner) -> None:
        # 1 day from A — should pick the cheapest neighbour
        trip = tiny_planner.plan(PlanRequest(start_hut_id="a", days=1))
        assert len(trip.days) == 1
        assert trip.days[0].start_hut.id == "a"

    def test_two_day_plan_with_goal(self, tiny_planner: AStarPlanner) -> None:
        trip = tiny_planner.plan(
            PlanRequest(start_hut_id="a", days=2, goal_hut_id="c")
        )
        assert len(trip.days) == 2
        assert trip.days[0].start_hut.id == "a"
        assert trip.days[-1].end_hut.id == "c"

    def test_two_day_plan_prefers_cheaper_path(
        self, tiny_planner: AStarPlanner
    ) -> None:
        """A→C direct is 25 km/200m. A→B→C is 10+10=20 km/200m.
        Over 2 days, A→B→C is cheaper. Planner should prefer it."""
        trip = tiny_planner.plan(
            PlanRequest(start_hut_id="a", days=2, goal_hut_id="c")
        )
        # Should go A → B → C
        path_ids = [trip.days[0].start_hut.id, trip.days[0].end_hut.id, trip.days[1].end_hut.id]
        assert path_ids == ["a", "b", "c"]


class TestPlannerConstraints:
    def test_no_revisit(self, tiny_planner: AStarPlanner) -> None:
        """No hut should appear twice in the same trip."""
        trip = tiny_planner.plan(PlanRequest(start_hut_id="a", days=3))
        visited = [trip.days[0].start_hut.id] + [d.end_hut.id for d in trip.days]
        assert len(visited) == len(set(visited)), f"Hut revisited in {visited}"

    def test_max_distance_enforced(self, tiny_planner: AStarPlanner) -> None:
        # All tiny-dataset edges are ≥10 km. Max of 5 km/day → no valid plan.
        with pytest.raises(PlanningError):
            tiny_planner.plan(
                PlanRequest(start_hut_id="a", days=2, max_km_per_day=5.0)
            )

    def test_unreachable_goal_raises(self, tiny_planner: AStarPlanner) -> None:
        # In 1 day, A can't reach D (no direct edge)
        with pytest.raises(PlanningError):
            tiny_planner.plan(
                PlanRequest(start_hut_id="a", days=1, goal_hut_id="d")
            )

    def test_unknown_start_raises(self, tiny_planner: AStarPlanner) -> None:
        with pytest.raises(KeyError, match="Unknown hut"):
            tiny_planner.plan(PlanRequest(start_hut_id="ghost", days=1))


class TestPlannerOnRealData:
    """Sanity checks against the Hardangervidda dataset."""

    def test_finse_to_haukeliseter_5_days(self, real_planner: AStarPlanner) -> None:
        trip = real_planner.plan(
            PlanRequest(start_hut_id="finse", days=5, goal_hut_id="haukeliseter")
        )
        assert len(trip.days) == 5
        assert trip.days[0].start_hut.id == "finse"
        assert trip.days[-1].end_hut.id == "haukeliseter"
        # Total should be reasonable for a 5-day Hardangervidda traverse
        assert 70.0 < trip.total_distance_km < 130.0

    def test_free_goal_3_days_from_finse(self, real_planner: AStarPlanner) -> None:
        trip = real_planner.plan(PlanRequest(start_hut_id="finse", days=3))
        assert len(trip.days) == 3
        assert trip.days[0].start_hut.id == "finse"


class TestPlannerProperties:
    """Property-based checks: invariants the planner must always satisfy."""

    @given(days=st.integers(min_value=1, max_value=4))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
    def test_trip_length_matches_request(
        self, tiny_planner: AStarPlanner, days: int
    ) -> None:
        """A successful plan always has exactly `days` legs."""
        try:
            trip = tiny_planner.plan(PlanRequest(start_hut_id="a", days=days))
        except PlanningError:
            return  # OK to fail for some configurations
        assert len(trip.days) == days

    @given(days=st.integers(min_value=1, max_value=4))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
    def test_trip_is_continuous(
        self, tiny_planner: AStarPlanner, days: int
    ) -> None:
        """Each day starts where the previous day ended."""
        try:
            trip = tiny_planner.plan(PlanRequest(start_hut_id="a", days=days))
        except PlanningError:
            return
        for prev, curr in zip(trip.days, trip.days[1:], strict=False):
            assert prev.end_hut.id == curr.start_hut.id