"""Tests for the CLI."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nordic_hike_planner.cli import app

HARDANGERVIDDA_ARGS = ["--data", "data/hardangervidda.json"]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestPlanCommand:
    def test_basic_plan_succeeds(self, runner: CliRunner) -> None:
        result = runner.invoke(
            app,
            ["--start", "finse", "--days", "3", *HARDANGERVIDDA_ARGS],
        )
        assert result.exit_code == 0
        assert "Trip plan" in result.stdout
        assert "Total distance" in result.stdout

    def test_plan_with_goal_succeeds(self, runner: CliRunner) -> None:
        result = runner.invoke(
            app,
            [
                "--start", "finse",
                "--days", "5",
                "--goal", "haukeliseter",
                *HARDANGERVIDDA_ARGS,
            ],
        )
        assert result.exit_code == 0
        assert "Finse" in result.stdout
        assert "Haukeli" in result.stdout

    def test_unknown_hut_exits_with_error(self, runner: CliRunner) -> None:
        result = runner.invoke(
            app,
            ["--start", "ghost", "--days", "3", *HARDANGERVIDDA_ARGS],
        )
        assert result.exit_code == 1

    def test_invalid_days_rejected_by_typer(self, runner: CliRunner) -> None:
        result = runner.invoke(
            app,
            ["--start", "finse", "--days", "0", *HARDANGERVIDDA_ARGS],
        )
        # Typer enforces min=1 before our code runs
        assert result.exit_code != 0

    def test_contradictory_constraints_exit_2(self, runner: CliRunner) -> None:
        result = runner.invoke(
            app,
            [
                "--start", "finse", "--days", "3",
                "--max-km", "10",
                "--target-km", "20",
                *HARDANGERVIDDA_ARGS,
            ],
        )
        # Our code (not Typer) catches this and exits with 2
        assert result.exit_code == 2
        assert "Invalid request" in result.stderr

    def test_missing_data_file_fails_cleanly(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        missing = tmp_path / "nope.json"
        result = runner.invoke(
            app,
            ["--start", "finse", "--days", "3", "--data", str(missing)],
        )
        assert result.exit_code == 1
        assert "Failed to load" in result.stderr

    def test_help_text_includes_command_purpose(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Plan" in result.stdout