"""Scoring functions for trip planning.

Naismith's rule estimates walking time from distance and ascent.
The cost function combines distance and elevation into a single scalar
that the planner minimises.

Both are pure functions with no dependency on the rest of the system,
which makes them trivially testable and easy to swap.

Why a separate scoring module?
    "What we optimise for" and "how we search" are independent concerns.
    If we wanted to switch from Naismith to Tobler's hiking function, or
    from a linear cost to something more sophisticated, those changes
    happen here without touching the A* implementation. The planner
    depends on these functions; they depend on nothing.
"""

from __future__ import annotations

from nordic_hike_planner.models import Edge

# Naismith's rule constants (Naismith, 1892; widely used in Nordic guidebooks).
# Expressed in hours per unit, kept as explicit constants so they're easy
# to find and reason about.
#
# The rule itself: "allow one hour for every five kilometres forward, plus
# an additional hour for every six hundred metres of ascent."
#
# We store these as the inverses (1/5, 1/600) so the formula can use simple
# multiplication rather than division. Clearer to read; identical numerically.
_HOURS_PER_KM_FLAT: float = 1.0 / 5.0  # 1 hour per 5 km flat distance
_HOURS_PER_METRE_ASCENT: float = 1.0 / 600.0  # 1 hour per 600m ascent

# Default elevation weight in the cost function.
#
# Why 6? Because 1000m of ascent under Naismith's rule takes ~1.67 hours,
# which is the time it'd take to walk ~8.3km on flat ground. We use 6 as
# a slightly under-Naismith default — the planner penalises climbing but
# not punitively. Users can override via the --elevation-weight flag.
DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M: float = 6.0


def naismith_hours(distance_km: float, ascent_m: int) -> float:
    """Estimate walking time using Naismith's rule.

    The rule (Naismith, 1892): allow 1 hour per 5 km of flat distance,
    plus 1 hour per 600 m of ascent.

    Args:
        distance_km: Horizontal distance of the leg, in kilometres.
        ascent_m: Cumulative ascent on the leg, in metres. Descent is
            treated as flat — a known simplification of the basic rule.
            (Langmuir adds a descent correction; we don't use it because
            it complicates the math more than the data quality justifies.)

    Returns:
        Estimated walking time in hours.

    Raises:
        ValueError: If distance_km or ascent_m is negative.

    Why fail loudly on negatives?
        A negative distance or ascent indicates a unit-conversion bug or
        bad data upstream. We want this to crash loudly at the boundary
        rather than silently produce nonsense walking times. Pydantic
        validation should catch this earlier (Edge has ge=0 constraints),
        but defence-in-depth doesn't hurt.
    """
    # Defensive validation. Pydantic's Edge model already enforces these
    # bounds, but this function can be called with raw floats from tests
    # or future callers — we don't want it to silently misbehave.
    if distance_km < 0:
        raise ValueError(f"distance_km must be non-negative, got {distance_km}")
    if ascent_m < 0:
        raise ValueError(f"ascent_m must be non-negative, got {ascent_m}")

    # The actual rule: time is linear in both distance and ascent.
    # Order of terms matches the original rule's wording for clarity:
    # "X hours of walking, plus Y hours of climbing".
    return distance_km * _HOURS_PER_KM_FLAT + ascent_m * _HOURS_PER_METRE_ASCENT


def edge_cost(
    edge: Edge,
    elevation_weight: float = DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M,
) -> float:
    """Compute the planning cost of traversing an edge.

    The cost is a weighted sum of distance and elevation gain, expressed
    in "equivalent flat kilometres":

        cost = distance_km + elevation_weight * elevation_gain_m / 1000

    A higher `elevation_weight` penalises climbing more heavily.

    Args:
        edge: The edge to score.
        elevation_weight: Cost (in km of flat equivalent) per 1000m of ascent.
            Defaults to 6, roughly matching Naismith's distance:ascent ratio.

    Returns:
        Non-negative cost in km-equivalent units.

    Raises:
        ValueError: If elevation_weight is negative.

    Design notes:
        - The cost is expressed in *km-equivalent* units so the planner's
          heuristic (great-circle distance, also in km) is dimensionally
          consistent. This is what makes the heuristic admissible.
        - We divide elevation_gain_m by 1000 so elevation_weight is "km
          equivalent per 1000m of climb" — a number humans can reason
          about (6 ≈ Naismith's ratio). Encoding the weight as "km per
          metre" would mean a default of 0.006, which is harder to think
          about and easier to typo.
    """
    # Defensive validation. A negative weight would invert the optimisation
    # (preferring high-elevation routes), which is almost certainly a bug.
    if elevation_weight < 0:
        raise ValueError(f"elevation_weight must be non-negative, got {elevation_weight}")

    # Convert elevation gain to km-equivalent: at elevation_weight=6,
    # 500m of climb adds 6 * 500 / 1000 = 3km to the effective distance.
    # The /1000 is unit conversion (metres → "thousands of metres").
    elevation_cost_km = elevation_weight * edge.elevation_gain_m / 1000.0

    # Total cost is flat distance plus the elevation penalty in km-equivalent.
    return edge.distance_km + elevation_cost_km
