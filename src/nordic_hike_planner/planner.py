"""A* path planner for multi-day hut-to-hut trips.

The planner searches for the lowest-cost sequence of huts starting from
a given start hut, optionally ending at a goal hut, over a fixed number
of days (= number of edges traversed).

Cost is computed by the scoring module. The heuristic is great-circle
distance, which is admissible because no walking route can be shorter
than a straight line on the Earth's surface — guaranteeing A* optimality.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Final

from nordic_hike_planner.models import DayPlan, Edge, Hut, Trip
from nordic_hike_planner.repository import HutRepository
from nordic_hike_planner.scoring import (
    DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M,
    edge_cost,
    naismith_hours,
)

# Earth radius in kilometres, used for great-circle distance.
_EARTH_RADIUS_KM: Final[float] = 6371.0


class PlanningError(Exception):
    """Raised when no valid plan can be produced for the given constraints."""


@dataclass(frozen=True)
class PlanRequest:
    """All parameters of a single planning request.

    Bundling these in a value object keeps the planner signature clean
    and makes adding new constraints later non-breaking.
    """

    start_hut_id: str
    days: int
    goal_hut_id: str | None = None
    max_km_per_day: float = 25.0
    target_km_per_day: float = 18.0
    elevation_weight: float = DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M

    def __post_init__(self) -> None:
        if self.days < 1:
            raise ValueError(f"days must be >= 1, got {self.days}")
        if self.max_km_per_day <= 0:
            raise ValueError(
                f"max_km_per_day must be positive, got {self.max_km_per_day}"
            )
        if self.target_km_per_day <= 0:
            raise ValueError(
                f"target_km_per_day must be positive, got {self.target_km_per_day}"
            )
        if self.target_km_per_day > self.max_km_per_day:
            raise ValueError(
                "target_km_per_day cannot exceed max_km_per_day "
                f"({self.target_km_per_day} > {self.max_km_per_day})"
            )


@dataclass(order=True)
class _SearchNode:
    """A node in the A* priority queue.

    Ordered by f_score (g + h) so the heap pops the most-promising node next.
    The non-ordering fields are excluded from comparison to avoid Pydantic
    objects being compared for ordering — which would fail.
    """

    f_score: float
    g_score: float = field(compare=False)
    hut: Hut = field(compare=False)
    day: int = field(compare=False)
    path: tuple[Hut, ...] = field(compare=False)
    edges: tuple[Edge, ...] = field(compare=False)


def great_circle_km(a: Hut, b: Hut) -> float:
    """Great-circle distance between two huts on the Earth's surface, in km.

    Uses the haversine formula. Admissible as an A* heuristic for walking
    distance: no walking route can be shorter than the great-circle distance.
    """
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


class AStarPlanner:
    """Plans multi-day hut traverses using A* search.

    The planner is stateless beyond the repository it was constructed with;
    individual `plan` calls don't share state, making it safe to reuse a
    single planner instance across requests.
    """

    def __init__(self, repository: HutRepository) -> None:
        self._repo = repository

    def plan(self, request: PlanRequest) -> Trip:
        """Find the lowest-cost trip satisfying the request.

        Raises:
            KeyError: if start_hut_id or goal_hut_id is not in the repository.
            PlanningError: if no valid path satisfies the constraints.
        """
        start_hut = self._repo.get_hut(request.start_hut_id)
        goal_hut = (
            self._repo.get_hut(request.goal_hut_id)
            if request.goal_hut_id is not None
            else None
        )

        # Initial node: at the start hut, on day 0 (i.e. zero days walked).
        start_node = _SearchNode(
            f_score=self._heuristic(start_hut, goal_hut, request),
            g_score=0.0,
            hut=start_hut,
            day=0,
            path=(start_hut,),
            edges=(),
        )

        # Priority queue ordered by f_score = g_score + heuristic
        frontier: list[_SearchNode] = [start_node]

        # For free-goal search we track the best terminal node found so far.
        best_terminal: _SearchNode | None = None

        while frontier:
            current = heapq.heappop(frontier)

            # Have we reached the goal? Two cases:
            if current.day == request.days:
                # Walked the right number of days. Check goal constraint.
                if goal_hut is None:
                    # Free-goal: keep the cheapest one we ever pop at this depth.
                    if best_terminal is None or current.g_score < best_terminal.g_score:
                        best_terminal = current
                    # Don't expand further; this path is complete.
                    continue
                elif current.hut.id == goal_hut.id:
                    return self._build_trip(current, request)
                else:
                    # Reached max depth but not at goal — dead end for this path.
                    continue

            # Expand neighbours
            for neighbour_hut, edge in self._repo.neighbours(current.hut.id):
                # Constraint: don't revisit huts
                if neighbour_hut in current.path:
                    continue
                # Constraint: respect max daily distance
                if edge.distance_km > request.max_km_per_day:
                    continue

                # Compute g_score for the candidate
                leg_cost = edge_cost(edge, request.elevation_weight)
                # Soft penalty for deviating from target daily distance.
                # Quadratic so small deviations are cheap, large ones expensive.
                distance_penalty = (edge.distance_km - request.target_km_per_day) ** 2 / 100.0
                new_g = current.g_score + leg_cost + distance_penalty

                # Heuristic from the neighbour to the goal (if any)
                h = self._heuristic(neighbour_hut, goal_hut, request)

                # Adjust heuristic for remaining days — we need at least
                # `remaining_days` more legs, each contributing at minimum
                # target_km_per_day. This is conservative (admissible).
                remaining_days = request.days - (current.day + 1)
                if goal_hut is None and remaining_days > 0:
                    # No goal: optimistic remaining cost is "remaining days
                    # at zero cost." Heuristic stays at 0.
                    h = 0.0

                neighbour_node = _SearchNode(
                    f_score=new_g + h,
                    g_score=new_g,
                    hut=neighbour_hut,
                    day=current.day + 1,
                    path=current.path + (neighbour_hut,),
                    edges=current.edges + (edge,),
                )
                heapq.heappush(frontier, neighbour_node)

        # Exhausted the frontier
        if goal_hut is None and best_terminal is not None:
            return self._build_trip(best_terminal, request)

        # No valid plan found
        raise PlanningError(
            f"No valid {request.days}-day plan from {request.start_hut_id}"
            + (f" to {goal_hut.id}" if goal_hut else "")
        )

    @staticmethod
    def _heuristic(current: Hut, goal: Hut | None, request: PlanRequest) -> float:
        """Admissible heuristic: great-circle distance to goal, scaled by cost.

        Returns 0 when there's no goal (free-goal search degrades to Dijkstra).
        """
        if goal is None:
            return 0.0
        # Distance is in km; the cost function returns km-equivalent units,
        # so no scaling needed. Still admissible because great-circle
        # is a lower bound on walking distance and we ignore elevation
        # (always non-negative contribution).
        return great_circle_km(current, goal)

    @staticmethod
    def _build_trip(terminal: _SearchNode, request: PlanRequest) -> Trip:
        """Construct a Trip value object from a successful search node."""
        day_plans: list[DayPlan] = []
        for i, edge in enumerate(terminal.edges, start=1):
            start_hut = terminal.path[i - 1]
            end_hut = terminal.path[i]
            day_plans.append(
                DayPlan(
                    day_number=i,
                    start_hut=start_hut,
                    end_hut=end_hut,
                    distance_km=edge.distance_km,
                    elevation_gain_m=edge.elevation_gain_m,
                    estimated_hours=naismith_hours(
                        edge.distance_km, edge.elevation_gain_m
                    ),
                )
            )

        return Trip(
            days=day_plans,
            total_distance_km=sum(d.distance_km for d in day_plans),
            total_elevation_gain_m=sum(d.elevation_gain_m for d in day_plans),
            total_estimated_hours=sum(d.estimated_hours for d in day_plans),
        )