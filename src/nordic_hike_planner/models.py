"""Domain models for the Nordic Hike Planner.

These represent the core vocabulary of the system: huts, the legs between them,
and the multi-day trips composed of those legs.

All models are immutable (frozen=True) and validated at construction time.
Business logic lives in the planner module, not here.

Why a separate models module?
    The domain types are the "ubiquitous language" of the codebase — every
    other module speaks in terms of Hut, Edge, DayPlan, Trip. Keeping them
    in their own module with no business logic means they can be imported
    everywhere without circular-import worries, and a reviewer can read this
    one file to learn the entire vocabulary of the system.
"""

from __future__ import annotations

# Self is used in model_validator return types so type checkers know the
# validator returns the same concrete subclass it was called on.
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Hut(BaseModel):
    """A mountain hut that can serve as a start, end, or overnight stop.

    Coordinates are WGS84. Elevation is metres above sea level.
    The season field is an inclusive month range when the hut is operational.

    Design notes:
        - frozen=True makes instances immutable and hashable. Immutability
          eliminates a whole class of "who mutated this?" bugs. Hashability
          lets us use Hut as a dict key and a set member, which the A*
          search relies on for O(1) "have we visited this hut?" checks.
        - All numeric fields have explicit ranges. Latitude/longitude are
          bounded by WGS84; elevation is bounded by Mount Everest plus a
          safety margin; capacity is non-negative.
    """

    # frozen=True: instance attributes can't be reassigned after construction.
    # This is what makes Hut hashable (Pydantic auto-generates __hash__ from
    # field values when the model is frozen).
    model_config = ConfigDict(frozen=True)

    # Stable identifier used everywhere else (edges reference it, the API
    # accepts it as input). Kept as a plain string rather than a separate
    # HutId type for simplicity — a NewType would be more rigorous but
    # add ceremony without much value at this size.
    id: str = Field(..., min_length=1, description="Stable identifier, e.g. 'finse'")

    # Human-readable name shown in CLI/API output.
    name: str = Field(..., min_length=1)

    # WGS84 latitude/longitude. Bounds are enforced by Pydantic so invalid
    # coordinates are rejected at construction, not at use.
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)

    # Elevation in metres. Upper bound 9000m is "Everest plus slack" —
    # impossibly high in practice but a safety net against unit-confusion
    # bugs (e.g. someone passes feet by accident).
    elevation_m: int = Field(..., ge=0, le=9000)

    # Number of beds. Zero is allowed for unstaffed shelters with no
    # formal beds (rare but real in the DNT network).
    capacity: int = Field(..., ge=0)

    # Operator string. Could be a Literal["DNT", "STF", "private"] for
    # stricter typing, but keeping it open lets the data file include
    # operators we haven't enumerated yet.
    operator: str = Field(..., description="e.g. 'DNT', 'STF', 'private'")

    # Inclusive month range. A summer-only hut is (6, 9); a winter hut
    # spanning the year boundary is (11, 3). The is_open_in_month()
    # method handles both cases.
    season_start_month: int = Field(..., ge=1, le=12)
    season_end_month: int = Field(..., ge=1, le=12)

    @model_validator(mode="after")
    def _validate_season(self) -> Self:
        """Cross-field season validation.

        We deliberately do NOT require season_start_month <= season_end_month
        because that would reject winter-spanning huts (e.g. open Nov-March).
        The is_open_in_month() method below handles both orientations.
        This validator exists as an explicit "yes, we considered this" marker
        for future maintainers who might be tempted to add such a check.
        """
        # No constraints to enforce — kept as a documentation hook for
        # future season-related invariants (e.g. "season can't be a single
        # month if capacity is zero" or similar).
        return self

    def is_open_in_month(self, month: int) -> bool:
        """Whether the hut is operational in the given month (1-12).

        Handles both normal ranges (e.g. 6-9 for summer) and wrap-around
        ranges (e.g. 11-3 for winter spanning the new year).
        """
        # Normal case: start month comes before or equals end month in the
        # calendar year. Simple range check.
        if self.season_start_month <= self.season_end_month:
            return self.season_start_month <= month <= self.season_end_month

        # Wrap-around case: season crosses the new year (e.g. 11 → 3 means
        # Nov, Dec, Jan, Feb, Mar). A month is "in range" if it's at or
        # after the start, OR at or before the end.
        return month >= self.season_start_month or month <= self.season_end_month


class Edge(BaseModel):
    """A walkable leg between two huts.

    Edges are directional in the model but the dataset stores symmetric pairs.
    The repository expands each JSON edge into both directions in the
    adjacency map, so the planner sees a bidirectional graph.

    Design notes:
        - We store hut IDs (from_hut_id, to_hut_id) rather than Hut objects
          because edges are loaded from JSON before huts are necessarily
          resolved to objects. The repository stitches IDs to huts during
          adjacency construction.
        - distance_km has an upper bound of 200km as a sanity check — any
          single hut-to-hut leg longer than that is almost certainly a
          data error.
    """

    # Same immutability + hashability rationale as Hut.
    model_config = ConfigDict(frozen=True)

    # String references to Hut.id. Kept as strings (rather than Hut objects)
    # to keep JSON loading straightforward: edges can be parsed before all
    # huts are resolved.
    from_hut_id: str
    to_hut_id: str

    # Distance is bounded: gt=0 (no zero-length edges; that would be a
    # self-loop in disguise) and le=200 (no single leg longer than 200km;
    # that's a data error, not a real day's walk).
    distance_km: float = Field(..., gt=0, le=200)

    # Elevation gain bounded similarly. 5000m is "Kilimanjaro in one leg" —
    # impossibly high in practice but catches unit-confusion bugs.
    elevation_gain_m: int = Field(..., ge=0, le=5000)

    @model_validator(mode="after")
    def _validate_endpoints_differ(self) -> Self:
        """An edge from a hut to itself is meaningless.

        Pydantic's field-level validation can't express this cross-field
        constraint, so we do it here. Failing fast at construction time
        means a bad data file is caught at load, not during a search.
        """
        if self.from_hut_id == self.to_hut_id:
            raise ValueError("Edge endpoints must differ")
        return self


class DayPlan(BaseModel):
    """A single day's walk: from one hut to the next.

    Composed by the planner from an Edge plus the actual hut objects at each
    end. Holds enough information to be rendered standalone, so the CLI/API
    don't need to look anything else up.
    """

    # Immutable for the same reasons as Hut and Edge. Once a DayPlan is
    # built by the planner, no consumer should ever modify it.
    model_config = ConfigDict(frozen=True)

    # 1-indexed day number. The validator on Trip enforces that day_number
    # values are 1, 2, 3, ... in order.
    day_number: int = Field(..., ge=1)

    # Full Hut objects (not just IDs) so consumers can render names,
    # coordinates, capacity, etc. without re-querying the repository.
    start_hut: Hut
    end_hut: Hut

    # Per-leg statistics. distance_km is gt=0 because a zero-distance day
    # would be a bug (no walking). elevation_gain_m can be zero (flat day).
    distance_km: float = Field(..., gt=0)
    elevation_gain_m: int = Field(..., ge=0)

    # Estimated walking time from Naismith's rule. gt=0 because any real
    # leg takes some time.
    estimated_hours: float = Field(..., gt=0)


class Trip(BaseModel):
    """A complete multi-day trip plan.

    Holds the ordered list of DayPlans plus pre-computed totals. The totals
    are pre-computed rather than calculated on demand because they're shown
    in every output and we'd rather pay the cost once than on every render.

    Invariants enforced by validators:
        - Days are continuous: each day starts where the previous ended.
        - Days are numbered sequentially: 1, 2, 3, ... with no gaps.

    These invariants mean any consumer of a Trip can trust the structure
    without defensive checking.
    """

    # Frozen for consistency with the other domain models. Once a Trip is
    # constructed, every consumer can rely on its shape.
    model_config = ConfigDict(frozen=True)

    # min_length=1 because a zero-day "trip" is meaningless.
    days: list[DayPlan] = Field(..., min_length=1)

    # Pre-computed totals. The planner fills these in when constructing the
    # Trip. Alternative design: computed properties on Trip. Trade-off: a
    # computed property re-runs on every access; a stored field is computed
    # once and then immutable. We chose stored for predictability.
    total_distance_km: float = Field(..., gt=0)
    total_elevation_gain_m: int = Field(..., ge=0)
    total_estimated_hours: float = Field(..., gt=0)

    @model_validator(mode="after")
    def _validate_continuity(self) -> Self:
        """Each day must start where the previous day ended.

        This is a structural invariant of a trip plan — if day 2 starts at
        a different hut from where day 1 ended, the trip is teleporting.
        Catching this at construction means downstream code can iterate
        through days assuming continuity, without defensive checks.
        """
        # zip(..., strict=False) intentionally: if days has length 1, the
        # zip produces no pairs and the loop simply doesn't execute, which
        # is correct (a single-day trip is trivially continuous).
        for previous, current in zip(self.days, self.days[1:], strict=False):
            if previous.end_hut.id != current.start_hut.id:
                raise ValueError(
                    f"Day {current.day_number} starts at {current.start_hut.id} "
                    f"but day {previous.day_number} ended at {previous.end_hut.id}"
                )
        return self

    @model_validator(mode="after")
    def _validate_day_numbering(self) -> Self:
        """Days must be numbered 1, 2, 3, ... with no gaps.

        Why enforce this? Because a Trip with days [1, 3] suggests something
        went wrong upstream — either a day was dropped or numbered wrong.
        Either way, garbage in, garbage out: we'd rather fail loudly here
        than render a misleading plan to the user.
        """
        expected = list(range(1, len(self.days) + 1))
        actual = [day.day_number for day in self.days]
        if actual != expected:
            raise ValueError(f"Day numbering must be sequential; got {actual}")
        return self
