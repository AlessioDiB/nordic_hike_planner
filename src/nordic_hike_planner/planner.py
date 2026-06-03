"""A* path planner for multi-day hut-to-hut trips.

The planner searches for the lowest-cost sequence of huts starting from
a given start hut, optionally ending at a goal hut, over a fixed number
of days (= number of edges traversed).

Cost is computed by the scoring module. The heuristic is great-circle
distance, which is admissible because no walking route can be shorter
than a straight line on the Earth's surface — guaranteeing A* optimality.

Why A* and not Dijkstra?
    Both find the optimal path on a weighted graph. The difference is
    that A* uses a heuristic to bias exploration toward the goal,
    typically exploring far fewer nodes for the same answer. With an
    admissible heuristic, A* sacrifices nothing in optimality.

    When the user doesn't specify a goal, A* degrades to Dijkstra
    (heuristic returns 0) and the search bounds itself by trip length.
"""

from __future__ import annotations

# heapq is Python's stdlib binary min-heap. It's the standard tool for
# priority queues — O(log n) push/pop, no dependencies.
import heapq
import math

# We use dataclasses (rather than Pydantic) for the search node because
# it's a tight internal type with no need for validation, and dataclass
# with order=True gives us automatic priority queue ordering for free.
from dataclasses import dataclass, field
from typing import Final

from nordic_hike_planner.models import DayPlan, Edge, Hut, Trip
from nordic_hike_planner.repository import HutRepository
from nordic_hike_planner.scoring import (
    DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M,
    edge_cost,
    naismith_hours,
)

# Earth radius in kilometres, used for great-circle (haversine) distance.
# Final[float] signals to type checkers that this is a constant — not
# meant to be reassigned anywhere in the module.
_EARTH_RADIUS_KM: Final[float] = 6371.0


class PlanningError(Exception):
    """Raised when no valid plan can be produced for the given constraints.

    Distinct from KeyError (unknown hut) so the API can map them to
    different HTTP status codes — 404 for missing resource, 422 for
    "request is well-formed but unsatisfiable".
    """


@dataclass(frozen=True)
class PlanRequest:
    """All parameters of a single planning request.

    Bundling these in a value object keeps the planner signature clean
    and makes adding new constraints later non-breaking — adding a new
    field doesn't change call sites that don't use it.

    Why a dataclass rather than a Pydantic model?
        PlanRequest is internal to the planner — the API has its own
        wire-format model (PlanRequestBody in api.py) that translates
        into this. Keeping this layer dataclass-light avoids unnecessary
        validation overhead on every API call.
    """

    # Required parameters: must be specified by every caller.
    start_hut_id: str
    days: int

    # Optional parameters: have sensible defaults so the simplest call
    # (start + days) works without ceremony.
    goal_hut_id: str | None = None
    max_km_per_day: float = 25.0
    target_km_per_day: float = 18.0
    elevation_weight: float = DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M

    def __post_init__(self) -> None:
        """Validate parameter combinations after construction.

        Frozen dataclasses still run __post_init__, which is where we
        check cross-field invariants that field-level validators can't.
        The biggest one: target_km_per_day must not exceed max_km_per_day,
        because that would be a contradictory request the planner can't
        possibly satisfy.

        Failing here (rather than partway through search) means callers
        get clear errors immediately, and the planner itself can trust
        its inputs.
        """
        # Each check is a single contract violation with a clear message.
        # Separate checks (not a combined assert) so the error tells the
        # user exactly which constraint they violated.
        if self.days < 1:
            raise ValueError(f"days must be >= 1, got {self.days}")
        if self.max_km_per_day <= 0:
            raise ValueError(f"max_km_per_day must be positive, got {self.max_km_per_day}")
        if self.target_km_per_day <= 0:
            raise ValueError(f"target_km_per_day must be positive, got {self.target_km_per_day}")
        # Cross-field invariant: target can't exceed max.
        # Without this check the soft penalty would push the planner away
        # from any feasible edge, producing confusing "no plan" errors.
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

    Why this is subtle:
        heapq compares whole objects to maintain heap order. If two nodes
        have the same f_score, Python falls through to compare the next
        field, then the next. Without `compare=False` on the Hut field,
        Python would try to compare Hut objects using `<`, which Pydantic
        doesn't define — and the heap operation would crash.

    Naming convention:
        Leading underscore (_SearchNode) signals this is an internal type.
        External code should never construct or import it.
    """

    # f_score is what the heap orders on. Lowest f_score gets popped first.
    # Listed first in the dataclass so it dominates comparison.
    f_score: float

    # g_score (cost so far) and the other fields are excluded from
    # comparison. Without compare=False, the dataclass would generate
    # __lt__ that compares them too, which is both wrong (we only want
    # to order by f_score) and would fail at runtime (Hut isn't ordered).
    g_score: float = field(compare=False)
    hut: Hut = field(compare=False)
    day: int = field(compare=False)

    # path and edges are tuples so they're immutable. Lists would risk
    # accidental mutation between heap operations.
    path: tuple[Hut, ...] = field(compare=False)
    edges: tuple[Edge, ...] = field(compare=False)


def great_circle_km(a: Hut, b: Hut) -> float:
    """Great-circle distance between two huts on the Earth's surface, in km.

    Uses the haversine formula. Admissible as an A* heuristic for walking
    distance: no walking route can be shorter than the great-circle distance.

    Why haversine and not Euclidean on lat/lon?
        Degrees of longitude get shorter as you approach the poles —
        a 1° difference at the equator is ~111km, but at 60° latitude
        it's only ~55km. Euclidean distance on (lat, lon) ignores this
        and would over- or under-estimate depending on latitude.
        Haversine handles the spherical geometry correctly.

    Why does admissibility matter?
        An admissible heuristic never overestimates the true cost to the
        goal. With one, A* is guaranteed to find the optimal path.
        Great-circle is admissible because the shortest path on Earth's
        surface IS the great circle — any walking route can only be
        equal to or longer than it.
    """
    # Convert degrees to radians for trig. Both inputs are WGS84
    # (validated by the Hut model), so this is safe.
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)

    # Haversine formula. The intermediate `h` represents half the
    # squared chord length between the two points on the unit sphere.
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2

    # Convert chord to great-circle arc length, then scale by Earth's radius.
    # asin(sqrt(h)) gives us half the central angle; doubling and multiplying
    # by R gives the arc length in kilometres.
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


class AStarPlanner:
    """Plans multi-day hut traverses using A* search.

    The planner is stateless beyond the repository it was constructed with;
    individual `plan` calls don't share state, making it safe to reuse a
    single planner instance across requests.

    Why stateless?
        FastAPI services need to be safe under concurrent requests. A
        stateless planner means the same instance can serve multiple
        plans in parallel without locks or contention. State that DOES
        need to live somewhere (the repository's graph) is read-only
        after construction, so even that is safe to share.
    """

    def __init__(self, repository: HutRepository) -> None:
        """Construct with a repository.

        Depends on the HutRepository Protocol, not on JsonHutRepository
        specifically. Any object that satisfies the protocol's three
        methods works — tests pass simple fakes; production passes the
        JSON-backed repository; a hypothetical future implementation
        could pass an OSM-backed or database-backed repository.
        """
        self._repo = repository

    def plan(self, request: PlanRequest) -> Trip:
        """Find the lowest-cost trip satisfying the request.

        Raises:
            KeyError: if start_hut_id or goal_hut_id is not in the repository.
            PlanningError: if no valid path satisfies the constraints.
        """
        # Resolve string IDs to Hut objects up front. Two reasons:
        # 1. Any unknown-hut error surfaces here, not deep in the search.
        # 2. We can use the Hut objects directly in heuristic calculations
        #    rather than re-looking them up at every node expansion.
        start_hut = self._repo.get_hut(request.start_hut_id)
        goal_hut = (
            self._repo.get_hut(request.goal_hut_id) if request.goal_hut_id is not None else None
        )

        # Initial node: at the start hut, on day 0 (i.e. zero days walked).
        # f_score = g + h = 0 + heuristic(start, goal). For free-goal
        # searches, the heuristic is 0, so f_score is just 0.
        start_node = _SearchNode(
            f_score=self._heuristic(start_hut, goal_hut, request),
            g_score=0.0,
            hut=start_hut,
            day=0,
            # Path starts with just the start hut (no edges traversed yet).
            path=(start_hut,),
            edges=(),
        )

        # Priority queue ordered by f_score = g_score + heuristic.
        # heapq.heappush/heappop maintain heap invariant in O(log n).
        frontier: list[_SearchNode] = [start_node]

        # For free-goal search (no specified goal), we don't know when to
        # stop — any terminal node at the right depth could be the answer.
        # We track the best one we've seen and return it when the heap empties.
        # For fixed-goal search, this is unused (we return as soon as we
        # pop a goal node at the right depth).
        best_terminal: _SearchNode | None = None

        # Main search loop. Continues until the heap is empty or we
        # return a result.
        while frontier:
            # Pop the node with lowest f_score — the most promising candidate.
            current = heapq.heappop(frontier)

            # Have we reached the goal? Two cases:
            # Walked the requested number of days. Now decide what to do
            # based on whether we have a fixed goal or are free-ranging.
            if current.day == request.days:
                # Walked the right number of days. Check goal constraint.
                if goal_hut is None:
                    # Free-goal search: any node at the right depth is a
                    # candidate. Track the cheapest and keep searching.
                    if best_terminal is None or current.g_score < best_terminal.g_score:
                        best_terminal = current
                    # Don't expand further; this path is complete.
                    continue
                elif current.hut.id == goal_hut.id:
                    # Fixed-goal search: we're at the goal at the right
                    # depth. Because A* with an admissible heuristic
                    # guarantees the first goal-pop is optimal, we can
                    # return immediately.
                    return self._build_trip(current, request)
                else:
                    # Reached max depth but at the wrong hut — this path
                    # is a dead end. Don't expand; continue with the next
                    # node from the frontier.
                    continue

            # Expand neighbours of the current node.
            for neighbour_hut, edge in self._repo.neighbours(current.hut.id):
                # Constraint: don't revisit huts within the same trip.
                # Real-world hikers want a traverse, not a loop. Checking
                # `in current.path` is O(n) in path length; for our small
                # trip lengths this is fine.
                if neighbour_hut in current.path:
                    continue

                # Constraint: respect max daily distance. Edges longer
                # than the user's limit are skipped entirely. This is a
                # hard constraint — no penalty, just exclusion.
                if edge.distance_km > request.max_km_per_day:
                    continue

                # Compute g_score for the candidate. Three components:
                # 1. Previous g_score (cost so far)
                # 2. Cost of this edge (distance + elevation penalty)
                # 3. Soft penalty for deviating from target daily distance
                leg_cost = edge_cost(edge, request.elevation_weight)

                # Quadratic penalty: small deviations are cheap, large
                # ones are expensive. /100 is a scaling factor so a 5km
                # deviation costs 0.25 km-equivalent, while a 15km
                # deviation costs 2.25 km-equivalent.
                distance_penalty = (edge.distance_km - request.target_km_per_day) ** 2 / 100.0
                new_g = current.g_score + leg_cost + distance_penalty

                # Heuristic from the neighbour to the goal (if any).
                # This is the "h" in A*'s f = g + h.
                h = self._heuristic(neighbour_hut, goal_hut, request)

                # For free-goal searches with days remaining, we have no
                # goal to aim at, so the heuristic is 0. A* degrades to
                # Dijkstra in this branch (uninformed search).
                remaining_days = request.days - (current.day + 1)
                if goal_hut is None and remaining_days > 0:
                    h = 0.0

                # Construct and enqueue the neighbour node.
                # Note: path is built with tuple unpacking (Python 3.5+
                # syntax) for clarity and a tiny performance edge over
                # tuple concatenation.
                neighbour_node = _SearchNode(
                    f_score=new_g + h,
                    g_score=new_g,
                    hut=neighbour_hut,
                    day=current.day + 1,
                    path=(*current.path, neighbour_hut),
                    edges=(*current.edges, edge),
                )
                heapq.heappush(frontier, neighbour_node)

        # Heap exhausted. Two possibilities:
        # 1. Free-goal search found at least one terminal: return the best.
        # 2. No terminal ever reached: raise PlanningError.
        if goal_hut is None and best_terminal is not None:
            return self._build_trip(best_terminal, request)

        # No valid plan found — either the constraints are too restrictive,
        # the graph is disconnected, or the goal is unreachable in the
        # requested number of days.
        raise PlanningError(
            f"No valid {request.days}-day plan from {request.start_hut_id}"
            + (f" to {goal_hut.id}" if goal_hut else "")
        )

    @staticmethod
    def _heuristic(current: Hut, goal: Hut | None, request: PlanRequest) -> float:
        """Admissible heuristic: great-circle distance to goal.

        Returns 0 when there's no goal (free-goal search degrades to Dijkstra).

        Why static?
            The method doesn't use `self`. Marking it @staticmethod
            signals that fact, makes it cheaper to call (no method-binding
            overhead), and allows it to be called without a planner
            instance — useful in tests.
        """
        # No goal → no heuristic. Search becomes uniform-cost (Dijkstra).
        if goal is None:
            return 0.0

        # Great-circle distance is in km, same units as our cost function,
        # so no scaling needed. Admissibility argument: walking distance
        # is always ≥ great-circle distance, so this never overestimates
        # the true cost. We ignore elevation in the heuristic — including
        # it would only make the heuristic less tight (it'd be smaller,
        # still admissible, but less informative). Great-circle alone
        # gives the best informedness while preserving admissibility.
        return great_circle_km(current, goal)

    @staticmethod
    def _build_trip(terminal: _SearchNode, request: PlanRequest) -> Trip:
        """Construct a Trip value object from a successful search node.

        Translates the internal _SearchNode (which is convenient for
        searching) into the external Trip type (which is convenient for
        rendering). The Trip's validators enforce continuity and day
        numbering, so any structural bugs here would be caught at
        construction.
        """
        # Build the per-day plans by walking edges in order. terminal.edges
        # holds the edges in traversal order; terminal.path holds the huts.
        day_plans: list[DayPlan] = []
        for i, edge in enumerate(terminal.edges, start=1):
            # Day i goes from path[i-1] to path[i] via edges[i-1].
            # (Path includes the start hut at index 0, so it's one longer
            # than edges.)
            start_hut = terminal.path[i - 1]
            end_hut = terminal.path[i]
            day_plans.append(
                DayPlan(
                    day_number=i,
                    start_hut=start_hut,
                    end_hut=end_hut,
                    distance_km=edge.distance_km,
                    elevation_gain_m=edge.elevation_gain_m,
                    # Naismith's rule converts (distance, ascent) → hours.
                    estimated_hours=naismith_hours(edge.distance_km, edge.elevation_gain_m),
                )
            )

        # Compute totals once for the Trip's summary fields. Summing here
        # rather than as computed properties means the values are stored
        # explicitly and don't recompute on every access.
        return Trip(
            days=day_plans,
            total_distance_km=sum(d.distance_km for d in day_plans),
            total_elevation_gain_m=sum(d.elevation_gain_m for d in day_plans),
            total_estimated_hours=sum(d.estimated_hours for d in day_plans),
        )
