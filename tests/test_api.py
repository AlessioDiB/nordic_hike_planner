"""Tests for the FastAPI service."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nordic_hike_planner.api import app

TINY_DATASET = Path("tests/data/tiny_test_dataset.json")
HARDANGERVIDDA = Path("data/hardangervidda.json")


@pytest.fixture
def client_real() -> TestClient:
    """Client backed by the real Hardangervidda dataset."""
    app.state.data_path = HARDANGERVIDDA
    return TestClient(app)


@pytest.fixture
def client_tiny() -> TestClient:
    """Client backed by the tiny test dataset."""
    app.state.data_path = TINY_DATASET
    return TestClient(app)


class TestHealth:
    def test_returns_ok_with_hut_count(self, client_real: TestClient) -> None:
        response = client_real.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["hut_count"] == 12


class TestPlanEndpoint:
    def test_plan_with_goal_succeeds(self, client_real: TestClient) -> None:
        response = client_real.post(
            "/plan",
            json={
                "start_hut_id": "finse",
                "days": 5,
                "goal_hut_id": "haukeliseter",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["days"]) == 5
        assert body["days"][0]["start_hut"]["id"] == "finse"
        assert body["days"][-1]["end_hut"]["id"] == "haukeliseter"

    def test_plan_without_goal_succeeds(self, client_real: TestClient) -> None:
        response = client_real.post(
            "/plan",
            json={"start_hut_id": "finse", "days": 3},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["days"]) == 3

    def test_unknown_start_hut_returns_404(self, client_real: TestClient) -> None:
        response = client_real.post(
            "/plan",
            json={"start_hut_id": "ghost", "days": 3},
        )
        assert response.status_code == 404
        assert "Unknown hut" in response.json()["detail"]

    def test_infeasible_plan_returns_422(self, client_real: TestClient) -> None:
        # Way too many days for the dataset — can't be done without revisits
        response = client_real.post(
            "/plan",
            json={"start_hut_id": "finse", "days": 13},
        )
        assert response.status_code == 422

    def test_contradictory_constraints_returns_422(
        self, client_real: TestClient
    ) -> None:
        # target_km_per_day > max_km_per_day
        response = client_real.post(
            "/plan",
            json={
                "start_hut_id": "finse",
                "days": 3,
                "max_km_per_day": 10.0,
                "target_km_per_day": 20.0,
            },
        )
        assert response.status_code == 422
        assert "cannot exceed" in response.json()["detail"]

    def test_invalid_body_returns_422(self, client_real: TestClient) -> None:
        # days=0 violates ge=1 in Pydantic
        response = client_real.post(
            "/plan",
            json={"start_hut_id": "finse", "days": 0},
        )
        assert response.status_code == 422

    def test_missing_required_field_returns_422(
        self, client_real: TestClient
    ) -> None:
        response = client_real.post("/plan", json={"start_hut_id": "finse"})
        assert response.status_code == 422