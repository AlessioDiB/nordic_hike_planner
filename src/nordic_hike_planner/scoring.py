"""Scoring functions for trip planning.

Naismith's rule estimates walking time from distance and ascent.
The cost function combines distance and elevation into a single scalar
that the planner minimises.

Both are pure functions with no dependency on the rest of the system,
which makes them trivially testable and easy to swap.
"""

from __future__ import annotations

from nordic_hike_planner.models import Edge

# Naismith's rule constants (Naismith, 1892; widely used in Nordic guidebooks).
# Expressed in hours per unit, kept as explicit constants so they're easy
# to find and reason about.
_HOURS_PER_KM_FLAT: float = 1.0 / 5.0  # 1 hour per 5 km flat distance
_HOURS_PER_METRE_ASCENT: float = 1.0 / 600.0  # 1 hour per 600m ascent

# Default elevation weight in the cost function.
# 1000m of ascent is treated as equivalent to 6km of flat walking,
# roughly matching Naismith's own ratio.
DEFAULT_ELEVATION_WEIGHT_KM_PER_1000M: float = 6.0


def naismith_hours(distance_km: float, ascent_m: int) -> float:
    """Estimate walking time using Naismith's rule.

    The rule (Naismith, 1892): allow 1 hour per 5 km of flat distance,
    plus 1 hour per 600 m of ascent.

    Args:
        distance_km: Horizontal distance of the leg, in kilometres.
        ascent_m: Cumulative ascent on the leg, in metres. Descent is
            treated as flat — a known simplification of the basic rule.

    Returns:
        Estimated walking time in hours.

    Raises:
        ValueError: If distance_km or ascent_m is negative.
    """
    if distance_km < 0:
        raise ValueError(f"distance_km must be non-negative, got {distance_km}")
    if ascent_m < 0:
        raise ValueError(f"ascent_m must be non-negative, got {ascent_m}")
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
    """
    if elevation_weight < 0:
        raise ValueError(f"elevation_weight must be non-negative, got {elevation_weight}")
    elevation_cost_km = elevation_weight * edge.elevation_gain_m / 1000.0
    return edge.distance_km + elevation_cost_km
