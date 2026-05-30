"""FastAPI service exposing a single planning endpoint.

The API is intentionally minimal: one /plan endpoint plus /health.
Request and response models live here, not in the domain layer, to keep
the wire format decoupled from the internal model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from nordic_hike_planner.models import Trip
from nordic_hike_planner.planner import (
    AStarPlanner,
    PlanningError,
    PlanRequest,
)
from nordic_hike_planner.repository import (
    HutRepository,
    JsonHutRepository,
    RepositoryError,
)

DEFAULT_DATA_PATH = Path("data/hardangervidda.json")


class PlanRequestBody(BaseModel):
    """Wire-format request body for POST /plan.

    Mirrors PlanRequest today, but kept separate so the wire format can
    evolve independently of the internal model.
    """

    start_hut_id: str = Field(..., min_length=1, examples=["finse"])
    days: int = Field(..., ge=1, le=14, examples=[4])
    goal_hut_id: str | None = Field(default=None, examples=["haukeliseter"])
    max_km_per_day: float = Field(default=25.0, gt=0, le=60)
    target_km_per_day: float = Field(default=18.0, gt=0, le=60)
    elevation_weight: float = Field(default=6.0, ge=0)


class HealthResponse(BaseModel):
    status: str
    hut_count: int


# Module-level state populated at startup via lifespan
_planner: AStarPlanner | None = None
_repository: HutRepository | None = None


def _get_planner() -> AStarPlanner:
    """Return the singleton planner, raising if not initialised."""
    if _planner is None:
        raise RuntimeError("Planner not initialised — did lifespan run?")
    return _planner


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise the repository and planner once at startup.

    Loading the JSON file is slow relative to a request, so we do it
    once when the app boots. If the file is broken, we fail fast at
    startup rather than on the first request.
    """
    global _planner, _repository
    data_path = Path(app.state.data_path) if hasattr(app.state, "data_path") else DEFAULT_DATA_PATH
    try:
        _repository = JsonHutRepository(data_path)
    except RepositoryError as exc:
        raise RuntimeError(f"Failed to load hut data: {exc}") from exc
    _planner = AStarPlanner(_repository)
    yield
    # Shutdown: nothing to clean up
    _planner = None
    _repository = None


app = FastAPI(
    title="Nordic Hike Planner",
    description=(
        "Plans multi-day hut-to-hut hiking trips in the Norwegian and Swedish "
        "mountains. Built as a portfolio project."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Health check. Verifies the repository loaded successfully."""
    repo = _repository
    if repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return HealthResponse(status="ok", hut_count=len(repo.all_huts()))


@app.post("/plan", response_model=Trip)
def plan(body: PlanRequestBody) -> Trip:
    """Plan a multi-day hut traverse.

    Returns the optimal trip satisfying the constraints, or an error
    if no valid trip exists.
    """
    planner = _get_planner()
    try:
        request = PlanRequest(
            start_hut_id=body.start_hut_id,
            days=body.days,
            goal_hut_id=body.goal_hut_id,
            max_km_per_day=body.max_km_per_day,
            target_km_per_day=body.target_km_per_day,
            elevation_weight=body.elevation_weight,
        )
    except ValueError as exc:
        # Pydantic catches most invalid inputs at parse-time, but
        # cross-field validation (target ≤ max) raises ValueError here.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        return planner.plan(request)
    except KeyError as exc:
        # Unknown hut id
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanningError as exc:
        # Valid request, but no feasible plan
        raise HTTPException(status_code=422, detail=str(exc)) from exc