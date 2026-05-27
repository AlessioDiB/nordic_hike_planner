"""Domain models for the Nordic Hike Planner.

These represent the core vocabulary of the system: huts, the legs between them,
and the multi-day trips composed of those legs.

All models are immutable (frozen=True) and validated at construction time.
Business logic lives in the planner module, not here.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Hut(BaseModel):
    """A mountain hut that can serve as a start, end, or overnight stop.

    Coordinates are WGS84. Elevation is metres above sea level.
    The season field is an inclusive month range when the hut is operational.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1, description="Stable identifier, e.g. 'finse'")
    name: str = Field(..., min_length=1)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    elevation_m: int = Field(..., ge=0, le=9000)
    capacity: int = Field(..., ge=0)
    operator: str = Field(..., description="e.g. 'DNT', 'STF', 'private'")
    season_start_month: int = Field(..., ge=1, le=12)
    season_end_month: int = Field(..., ge=1, le=12)

    @model_validator(mode="after")
    def _validate_season(self) -> Self:
        # We allow start > end to represent winter-spanning seasons (e.g. 11 → 3).
        # The planner handles "is the hut open in month X" using both fields.
        return self

    def is_open_in_month(self, month: int) -> bool:
        """Whether the hut is operational in the given month (1-12)."""
        if self.season_start_month <= self.season_end_month:
            return self.season_start_month <= month <= self.season_end_month
        # Wrap-around season (e.g. Nov → March)
        return month >= self.season_start_month or month <= self.season_end_month


class Edge(BaseModel):
    """A walkable leg between two huts.

    Edges are directional in the model but the dataset stores symmetric pairs.
    The planner treats them as bidirectional in graph terms.
    """

    model_config = ConfigDict(frozen=True)

    from_hut_id: str
    to_hut_id: str
    distance_km: float = Field(..., gt=0, le=200)
    elevation_gain_m: int = Field(..., ge=0, le=5000)

    @model_validator(mode="after")
    def _validate_endpoints_differ(self) -> Self:
        if self.from_hut_id == self.to_hut_id:
            raise ValueError("Edge endpoints must differ")
        return self


class DayPlan(BaseModel):
    """A single day's walk: from one hut to the next."""

    model_config = ConfigDict(frozen=True)

    day_number: int = Field(..., ge=1)
    start_hut: Hut
    end_hut: Hut
    distance_km: float = Field(..., gt=0)
    elevation_gain_m: int = Field(..., ge=0)
    estimated_hours: float = Field(..., gt=0)


class Trip(BaseModel):
    """A complete multi-day trip plan."""

    model_config = ConfigDict(frozen=True)

    days: list[DayPlan] = Field(..., min_length=1)
    total_distance_km: float = Field(..., gt=0)
    total_elevation_gain_m: int = Field(..., ge=0)
    total_estimated_hours: float = Field(..., gt=0)

    @model_validator(mode="after")
    def _validate_continuity(self) -> Self:
        """Each day must start where the previous day ended."""
        for previous, current in zip(self.days, self.days[1:], strict=False):
            if previous.end_hut.id != current.start_hut.id:
                raise ValueError(
                    f"Day {current.day_number} starts at {current.start_hut.id} "
                    f"but day {previous.day_number} ended at {previous.end_hut.id}"
                )
        return self

    @model_validator(mode="after")
    def _validate_day_numbering(self) -> Self:
        """Days must be numbered 1, 2, 3, ... with no gaps."""
        expected = list(range(1, len(self.days) + 1))
        actual = [day.day_number for day in self.days]
        if actual != expected:
            raise ValueError(f"Day numbering must be sequential; got {actual}")
        return self