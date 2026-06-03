"""Command-line interface for the Nordic Hike Planner.

Calls the planner directly (in-process) for fast feedback. Produces
human-readable output via rich. Sensible defaults so the simplest
invocation is `nordic-hike --start finse --days 4`.

Why direct call instead of HTTP?
    For a portfolio CLI, the simplest demo path wins: no server to
    start, no port to bind, no network round-trip. In a real product,
    an HTTP-based CLI might be the right choice (you can update the
    service without redeploying every client). For this size of
    project, in-process is the right tradeoff.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Annotated lets us attach typer's Option metadata to type hints,
# producing a CLI signature that reads as Python while declaratively
# specifying CLI-level behaviour (flags, help text, validation).
from typing import Annotated

import typer

# rich gives us coloured tables and panels with no manual formatting.
# Console(stderr=True) lets us write errors to stderr separately from
# normal output, which matters for users piping the result.
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

# Default dataset location. Overridable via --data flag.
DEFAULT_DATA_PATH = Path("data/hardangervidda.json")

# Typer app. add_completion=False suppresses the shell-completion
# install commands, which add noise to --help without being useful
# for a portfolio tool.
app = typer.Typer(
    add_completion=False,
    help="Plan multi-day hut-to-hut hiking trips in the Nordic mountains.",
)

# Two consoles: one for normal output (stdout), one for errors (stderr).
# This separation lets users pipe `nordic-hike ... | head` without
# losing error messages, and lets shells distinguish success from
# failure output streams.
console = Console()
error_console = Console(stderr=True, style="bold red")


@app.command()
def plan(
    # Each parameter is an Annotated[type, typer.Option(...)] combination.
    # The Option metadata declares the CLI flag, short form, help text,
    # and validation. Typer parses argv into these parameters automatically.
    start: Annotated[
        str,
        typer.Option(
            "--start",
            "-s",
            help="ID of the starting hut (e.g. 'finse').",
        ),
    ],
    days: Annotated[
        int,
        typer.Option(
            "--days",
            "-d",
            # min/max here are enforced by Typer before our code runs.
            # An invalid value produces a usage error with exit code 2,
            # matching standard Unix conventions.
            min=1,
            max=14,
            help="Number of days to walk (1-14).",
        ),
    ],
    # Optional goal: if omitted, planner picks the best terminal hut.
    goal: Annotated[
        str | None,
        typer.Option(
            "--goal",
            "-g",
            help="ID of the ending hut. If omitted, the planner picks one.",
        ),
    ] = None,
    # Constraints with defaults matching the planner's defaults.
    max_km: Annotated[
        float,
        typer.Option(
            "--max-km",
            help="Maximum km per day.",
        ),
    ] = 25.0,
    target_km: Annotated[
        float,
        typer.Option(
            "--target-km",
            help="Preferred km per day.",
        ),
    ] = 18.0,
    elevation_weight: Annotated[
        float,
        typer.Option(
            "--elevation-weight",
            help="Cost (km equivalent) per 1000m of ascent. Higher = avoids climbing.",
        ),
    ] = 6.0,
    # Allow overriding the data file — useful for testing with the tiny
    # dataset, or for users who curate their own region.
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

    Exit codes:
        0  Plan produced successfully.
        1  Runtime failure (unknown hut, infeasible plan, missing data file).
        2  Bad arguments (caught either by Typer or our own validation).
    """
    # Load the dataset. Wrapping in try/except lets us produce a
    # user-friendly error rather than a Python traceback when the
    # JSON file is missing or malformed.
    try:
        repository = JsonHutRepository(data)
    except RepositoryError as exc:
        # Error to stderr so users piping stdout still see it.
        error_console.print(f"Failed to load data: {exc}")
        # typer.Exit raises a controlled exception that produces the
        # specified exit code. 1 = runtime failure.
        raise typer.Exit(code=1) from exc

    planner = AStarPlanner(repository)

    # Construct the request. PlanRequest's __post_init__ validates
    # cross-field invariants (target ≤ max) and raises ValueError on
    # contradictions. We translate that into a user-friendly error.
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
        # Exit code 2 for bad arguments — matches Typer's own convention
        # for invalid CLI inputs.
        raise typer.Exit(code=2) from exc

    # Run the planner. Three failure modes mapped to exit code 1:
    # KeyError (unknown hut), PlanningError (no valid path).
    try:
        trip = planner.plan(request)
    except KeyError as exc:
        error_console.print(f"Unknown hut: {exc}")
        raise typer.Exit(code=1) from exc
    except PlanningError as exc:
        error_console.print(f"No valid plan: {exc}")
        raise typer.Exit(code=1) from exc

    # Success: render the trip to stdout.
    _print_trip(trip)


def _print_trip(trip: Trip) -> None:
    """Format and print a Trip to the console using rich.

    Separates rendering from the command function so the command stays
    focused on orchestration. If we wanted to add a --json output
    flag later, this is the only function that'd need to change.
    """
    # Build a rich Table for the per-day breakdown. Justify=right for
    # numeric columns makes them easier to scan.
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

    # Populate one row per day. Formatting choices (1 decimal for km
    # and hours, integer for metres) are deliberate — more precision
    # would imply false accuracy given the underlying data quality.
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

    # Summary panel below the table. [bold]...[/bold] is rich's markup
    # syntax for inline formatting — same idea as Markdown's **bold**.
    summary = (
        f"Total distance: [bold]{trip.total_distance_km:.1f} km[/bold]   "
        f"Total ascent: [bold]{trip.total_elevation_gain_m} m[/bold]   "
        f"Total walking time: [bold]{trip.total_estimated_hours:.1f} h[/bold]"
    )
    console.print(Panel(summary, title="Summary", title_align="left"))


# Module-level alias so `python -m nordic_hike_planner` works.
# Not strictly needed (the project.scripts entry in pyproject.toml
# is the primary entry point), but useful for ad-hoc invocation.
def main() -> None:
    """Entry point for the CLI."""
    app()


# Allow running this file directly: `python cli.py --start finse --days 4`.
# sys.exit(app()) ensures the exit code from typer.Exit propagates correctly.
if __name__ == "__main__":
    sys.exit(app())
