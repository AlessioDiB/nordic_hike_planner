"""Command-line interface for the Nordic Hike Planner.

Calls the planner directly (in-process) for fast feedback. Produces
human-readable output via rich. Sensible defaults so the simplest
invocation is `nordic-hike --start finse --days 4`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nordic_hike_planner.models import Trip
from nordic_hike_planner.planner import (
    AStarPlanner,
    PlanningError,
    PlanRequest,
)
from nordic_hike_planner.repository import JsonHutRepository, RepositoryError

DEFAULT_DATA_PATH = Path("data/hardangervidda.json")

app = typer.Typer(
    add_completion=False,
    help="Plan multi-day hut-to-hut hiking trips in the Nordic mountains.",
)
console = Console()
error_console = Console(stderr=True, style="bold red")


@app.command()
def plan(
    start: Annotated[
        str,
        typer.Option(
            "--start", "-s",
            help="ID of the starting hut (e.g. 'finse').",
        ),
    ],
    days: Annotated[
        int,
        typer.Option(
            "--days", "-d",
            min=1, max=14,
            help="Number of days to walk (1–14).",
        ),
    ],
    goal: Annotated[
        str | None,
        typer.Option(
            "--goal", "-g",
            help="ID of the ending hut. If omitted, the planner picks one.",
        ),
    ] = None,
    max_km: Annotated[
        float,
        typer.Option(
            "--max-km", help="Maximum km per day.",
        ),
    ] = 25.0,
    target_km: Annotated[
        float,
        typer.Option(
            "--target-km", help="Preferred km per day.",
        ),
    ] = 18.0,
    elevation_weight: Annotated[
        float,
        typer.Option(
            "--elevation-weight",
            help="Cost (km equivalent) per 1000m of ascent. Higher = avoids climbing.",
        ),
    ] = 6.0,
    data: Annotated[
        Path,
        typer.Option(
            "--data",
            help="Path to the hut dataset JSON file.",
        ),
    ] = DEFAULT_DATA_PATH,
) -> None:
    """Plan a multi-day hut-to-hut trip and print it nicely.

    Example:
        nordic-hike --start finse --days 5 --goal haukeliseter
    """
    # Load the dataset
    try:
        repository = JsonHutRepository(data)
    except RepositoryError as exc:
        error_console.print(f"Failed to load data: {exc}")
        raise typer.Exit(code=1) from exc

    planner = AStarPlanner(repository)

    # Construct the request
    try:
        request = PlanRequest(
            start_hut_id=start,
            days=days,
            goal_hut_id=goal,
            max_km_per_day=max_km,
            target_km_per_day=target_km,
            elevation_weight=elevation_weight,
        )
    except ValueError as exc:
        error_console.print(f"Invalid request: {exc}")
        raise typer.Exit(code=2) from exc

    # Plan
    try:
        trip = planner.plan(request)
    except KeyError as exc:
        error_console.print(f"Unknown hut: {exc}")
        raise typer.Exit(code=1) from exc
    except PlanningError as exc:
        error_console.print(f"No valid plan: {exc}")
        raise typer.Exit(code=1) from exc

    _print_trip(trip)


def _print_trip(trip: Trip) -> None:
    """Format and print a Trip to the console using rich."""
    table = Table(
        title="Trip plan",
        title_style="bold cyan",
        header_style="bold",
    )
    table.add_column("Day", justify="right", style="cyan")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Distance", justify="right")
    table.add_column("Ascent", justify="right")
    table.add_column("Est. time", justify="right")

    for day in trip.days:
        table.add_row(
            str(day.day_number),
            day.start_hut.name,
            day.end_hut.name,
            f"{day.distance_km:.1f} km",
            f"{day.elevation_gain_m} m",
            f"{day.estimated_hours:.1f} h",
        )

    console.print(table)

    summary = (
        f"Total distance: [bold]{trip.total_distance_km:.1f} km[/bold]   "
        f"Total ascent: [bold]{trip.total_elevation_gain_m} m[/bold]   "
        f"Total walking time: [bold]{trip.total_estimated_hours:.1f} h[/bold]"
    )
    console.print(Panel(summary, title="Summary", title_align="left"))


# Module-level alias so `python -m nordic_hike_planner` works
def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    sys.exit(app())