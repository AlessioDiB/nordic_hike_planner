"""FastAPI service exposing a single planning endpoint.

The API is intentionally minimal: one /plan endpoint plus /health.
Request and response models live here, not in the domain layer, to keep
the wire format decoupled from the internal model.

State (the planner and repository) lives on app.state, populated by
the lifespan context manager. This avoids module-level globals and
makes the app safe to instantiate multiple times in tests.

Why one endpoint plus health?
    Restraint. The temptation with FastAPI is to keep adding endpoints
    (/huts, /huts/{id}, /regions, /edges...). None of them are needed for
    the assignment. Each one would be more surface area to test, document,
    and maintain. The single /plan endpoint plus a health check is the
    minimum viable API surface — and the restraint is itself a signal.
"""

from __future__ import annotations

# AsyncIterator typing the lifespan generator. Required for FastAPI's
# lifespan parameter to recognise it as a valid context manager.
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

# Request is imported so endpoint handlers can access app.state via
# request.app.state — the modern FastAPI pattern for accessing
# application-scoped state without module globals.
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from nordic_hike_planner.models import Trip
from nordic_hike_planner.planner import (
    AStarPlanner,
    PlanningError,
    PlanRequest,
)
from nordic_hike_planner.repository import JsonHutRepository, RepositoryError

# Default location of the dataset. Overridable via app.state.data_path
# for tests that want to use the tiny test dataset.
DEFAULT_DATA_PATH = Path("data/hardangervidda.json")


class PlanRequestBody(BaseModel):
    """Wire-format request body for POST /plan.

    Mirrors PlanRequest today, but kept separate so the wire format can
    evolve independently of the internal model.

    Why a separate model from PlanRequest?
        Today they're identical, but they shouldn't be coupled. If we
        add internal-only fields to PlanRequest (debug flags, search
        stats), we don't want those exposed at the API. Conversely, if
        we add wire-only concerns (deprecated fields, version envelopes),
        we don't want those leaking into the planner. The separation is
        trivial today but pays off the first time you need it.
    """

    # `examples` parameter populates the Swagger UI's interactive form
    # with sensible defaults — a small touch that makes the API feel
    # polished when reviewers open /docs.
    start_hut_id: str = Field(..., min_length=1, examples=["finse"])

    # days bounded to 1..14 at the API level. The planner enforces
    # >= 1 already; the upper bound here is a sanity check — a 30-day
    # request is almost certainly a mistake and we'd rather reject it
    # at the boundary than spin up an expensive search.
    days: int = Field(..., ge=1, le=14, examples=[4])

    # Optional fields with sensible defaults. The defaults match
    # PlanRequest's defaults for consistency.
    goal_hut_id: str | None = Field(default=None, examples=["haukeliseter"])
    max_km_per_day: float = Field(default=25.0, gt=0, le=60)
    target_km_per_day: float = Field(default=18.0, gt=0, le=60)
    elevation_weight: float = Field(default=6.0, ge=0)


class HealthResponse(BaseModel):
    """Response shape for GET /health.

    Tiny but explicit — having a Pydantic model means /health appears in
    the OpenAPI schema with a documented response type, which is a small
    operational nicety.
    """

    status: str
    hut_count: int


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise the repository and planner once at startup.

    The data path can be overridden by setting app.state.data_path
    before the app is started (useful for tests).

    Loading the JSON file is slow relative to a request, so we do it
    once when the app boots. If the file is broken, we fail fast at
    startup rather than on the first request.

    Why lifespan instead of @app.on_event("startup")?
        on_event is deprecated in FastAPI 0.93+. The lifespan context
        manager is the modern equivalent and combines startup + shutdown
        logic into one place.
    """
    # Read the data path from app.state if set, otherwise use the default.
    # This allows tests to inject a different dataset path before starting
    # the app, without modifying module-level state.
    data_path = getattr(app.state, "data_path", DEFAULT_DATA_PATH)

    # Load the repository immediately. If it fails, raise — the app
    # won't start, which is the right behaviour for a broken data file.
    # A service that runs with no data is worse than no service at all.
    try:
        repository = JsonHutRepository(Path(data_path))
    except RepositoryError as exc:
        raise RuntimeError(f"Failed to load hut data: {exc}") from exc

    # Store the loaded objects on app.state so endpoints can access them.
    # This is the idiomatic FastAPI pattern — request.app.state.X is
    # accessible from any endpoint via the Request object.
    app.state.repository = repository
    app.state.planner = AStarPlanner(repository)

    # yield is the boundary between startup and shutdown. The app runs
    # while this generator is suspended at the yield.
    yield

    # Shutdown: nothing to clean up. JSON repository has no open files
    # or connections after loading.


# FastAPI app construction. title/description/version populate the
# /docs Swagger UI's header and OpenAPI metadata.
app = FastAPI(
    title="Nordic Hike Planner",
    description=(
        "Plans multi-day hut-to-hut hiking trips in the Norwegian and Swedish "
        "mountains. Built as a portfolio project."
    ),
    version="0.1.0",
    # Wire up the lifespan so the planner/repository are loaded at startup.
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Health check. Verifies the repository loaded successfully.

    Returns 200 with hut_count if everything's fine, 503 if the repository
    hasn't been initialised (which would indicate a startup problem).

    Why return hut_count?
        It's a lightweight verification that the data is actually loaded
        — not just "the process is running" but "the data is present
        and parseable". Docker's HEALTHCHECK and Kubernetes' liveness
        probe both call this endpoint.
    """
    # getattr with default None: if the lifespan hasn't run (which
    # shouldn't happen in production but can in tests), we return 503
    # rather than crash.
    repository = getattr(request.app.state, "repository", None)
    if repository is None:
        # 503 Service Unavailable: the service is up but not ready.
        # Different from 500 (internal error) — this is "wait and retry".
        raise HTTPException(status_code=503, detail="Service not ready")

    # Return the count as proof of life.
    return HealthResponse(status="ok", hut_count=len(repository.all_huts()))


@app.post("/plan", response_model=Trip)
def plan(body: PlanRequestBody, request: Request) -> Trip:
    """Plan a multi-day hut traverse.

    Returns the optimal trip satisfying the constraints, or an error
    if no valid trip exists.

    Error semantics:
        - 404: unknown start or goal hut (resource not found)
        - 422: request well-formed but unsatisfiable (no valid plan)
        - 422: contradictory constraints (caught at PlanRequest construction)
        - 503: planner not initialised (startup problem)
    """
    # Same defensive check as /health. In production the planner should
    # always be initialised by lifespan; this is belt-and-braces.
    planner: AStarPlanner | None = getattr(request.app.state, "planner", None)
    if planner is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Translate the wire model (PlanRequestBody) into the internal model
    # (PlanRequest). This is where cross-field validation runs — for
    # example, "target_km_per_day cannot exceed max_km_per_day".
    try:
        plan_request = PlanRequest(
            start_hut_id=body.start_hut_id,
            days=body.days,
            goal_hut_id=body.goal_hut_id,
            max_km_per_day=body.max_km_per_day,
            target_km_per_day=body.target_km_per_day,
            elevation_weight=body.elevation_weight,
        )
    except ValueError as exc:
        # Cross-field validation (target ≤ max) raises ValueError.
        # 422 Unprocessable Entity: the request was valid JSON but
        # semantically broken.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Run the planner. Two specific exceptions get mapped to HTTP errors;
    # any other exception bubbles up as a 500, which is the right
    # behaviour for unexpected failures (we want to see them in logs).
    try:
        return planner.plan(plan_request)
    except KeyError as exc:
        # Unknown hut ID. 404 Not Found: the requested resource doesn't exist.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanningError as exc:
        # No valid plan exists for these constraints. 422 again:
        # request is well-formed but unsatisfiable.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
