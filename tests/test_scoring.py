"""Tests for Naismith's rule and the cost function."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nordic_hike_planner.models import Edge
from nordic_hike_planner.scoring import (
    DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M,
    edge_cost,
    naismith_hours,
)


class TestNaismithHours:
    def test_flat_walk_5km_takes_one_hour(self) -> None:
        # By definition: 5 km flat = 1 hour
        assert naismith_hours(5.0, 0) == pytest.approx(1.0)

    def test_600m_ascent_adds_one_hour(self) -> None:
        # 5 km flat + 600m ascent = 2 hours
        assert naismith_hours(5.0, 600) == pytest.approx(2.0)

    def test_zero_distance_and_ascent(self) -> None:
        assert naismith_hours(0.0, 0) == 0.0

    def test_negative_distance_rejected(self) -> None:
        with pytest.raises(ValueError, match="distance_km must be non-negative"):
            naismith_hours(-1.0, 0)

    def test_negative_ascent_rejected(self) -> None:
        with pytest.raises(ValueError, match="ascent_m must be non-negative"):
            naismith_hours(5.0, -100)

    def test_realistic_hardangervidda_leg(self) -> None:
        # Finse → Geiterygghytta: 16 km, 280 m gain
        # 16/5 + 280/600 = 3.2 + 0.467 ≈ 3.67 hours
        result = naismith_hours(16.0, 280)
        assert result == pytest.approx(3.667, abs=0.01)

    @given(
        distance=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        ascent=st.integers(min_value=0, max_value=3000),
    )
    def test_naismith_is_monotonic_in_distance(
        self, distance: float, ascent: int
    ) -> None:
        """Adding distance never decreases the time estimate."""
        assert naismith_hours(distance + 1.0, ascent) >= naismith_hours(
            distance, ascent
        )

    @given(
        distance=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        ascent=st.integers(min_value=0, max_value=3000),
    )
    def test_naismith_is_monotonic_in_ascent(
        self, distance: float, ascent: int
    ) -> None:
        """Adding ascent never decreases the time estimate."""
        assert naismith_hours(distance, ascent + 100) >= naismith_hours(
            distance, ascent
        )


class TestEdgeCost:
    @pytest.fixture
    def edge(self) -> Edge:
        return Edge(
            from_hut_id="a", to_hut_id="b",
            distance_km=10.0, elevation_gain_m=500,
        )

    def test_default_weight_matches_constant(self, edge: Edge) -> None:
        # Confirm the default arg matches the published constant
        with_default = edge_cost(edge)
        explicit = edge_cost(edge, DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M)
        assert with_default == explicit

    def test_flat_edge_cost_equals_distance(self) -> None:
        flat = Edge(
            from_hut_id="a", to_hut_id="b",
            distance_km=15.0, elevation_gain_m=0,
        )
        assert edge_cost(flat) == pytest.approx(15.0)

    def test_elevation_adds_penalty(self, edge: Edge) -> None:
        # 10 km + 500m ascent at weight 6
        # = 10 + 6 * 500/1000 = 10 + 3 = 13
        assert edge_cost(edge, elevation_weight=6.0) == pytest.approx(13.0)

    def test_zero_weight_ignores_elevation(self, edge: Edge) -> None:
        # With weight 0, only distance matters
        assert edge_cost(edge, elevation_weight=0.0) == pytest.approx(10.0)

    def test_higher_weight_increases_cost(self, edge: Edge) -> None:
        low = edge_cost(edge, elevation_weight=2.0)
        high = edge_cost(edge, elevation_weight=10.0)
        assert high > low

    def test_negative_weight_rejected(self, edge: Edge) -> None:
        with pytest.raises(ValueError, match="elevation_weight must be non-negative"):
            edge_cost(edge, elevation_weight=-1.0)

    @given(
        distance=st.floats(min_value=0.1, max_value=100.0, allow_nan=False),
        ascent=st.integers(min_value=0, max_value=3000),
        weight=st.floats(min_value=0.0, max_value=20.0, allow_nan=False),
    )
    def test_cost_is_non_negative(
        self, distance: float, ascent: int, weight: float
    ) -> None:
        """Cost can never be negative for valid inputs."""
        edge = Edge(
            from_hut_id="a", to_hut_id="b",
            distance_km=distance, elevation_gain_m=ascent,
        )
        assert edge_cost(edge, weight) >= 0.0
        assert math.isfinite(edge_cost(edge, weight))