"""Tests for the repository layer."""

import json
from pathlib import Path

import pytest

from nordic_hike_planner.models import Edge, Hut
from nordic_hike_planner.repository import JsonHutRepository, RepositoryError

TINY_DATASET = Path("tests/data/tiny_test_dataset.json")


@pytest.fixture
def repo() -> JsonHutRepository:
    return JsonHutRepository(TINY_DATASET)


class TestLoading:
    def test_loads_all_huts(self, repo: JsonHutRepository) -> None:
        huts = repo.all_huts()
        assert len(huts) == 4
        assert {h.id for h in huts} == {"a", "b", "c", "d"}

    def test_get_hut_returns_correct_object(self, repo: JsonHutRepository) -> None:
        hut_a = repo.get_hut("a")
        assert hut_a.name == "Hut A"
        assert hut_a.elevation_m == 1000

    def test_get_unknown_hut_raises_keyerror(self, repo: JsonHutRepository) -> None:
        with pytest.raises(KeyError, match="Unknown hut id"):
            repo.get_hut("nonexistent")

    def test_neighbours_returns_bidirectional(self, repo: JsonHutRepository) -> None:
        # The JSON has an edge A→B; we expect both A's and B's neighbour lists
        # to contain the other.
        a_neighbours = {hut.id for hut, _ in repo.neighbours("a")}
        b_neighbours = {hut.id for hut, _ in repo.neighbours("b")}
        assert "b" in a_neighbours
        assert "a" in b_neighbours

    def test_neighbour_count_matches_edge_count(self, repo: JsonHutRepository) -> None:
        # Tiny dataset has 5 edges → 10 directed adjacency entries total
        total = sum(len(repo.neighbours(hut.id)) for hut in repo.all_huts())
        assert total == 10

    def test_isolated_hut_returns_empty(self, tmp_path: Path) -> None:
        # An edge-less dataset with a single hut
        dataset = {
            "huts": [
                {
                    "id": "lonely", "name": "Lonely Hut",
                    "lat": 60.0, "lon": 7.0, "elevation_m": 1000,
                    "capacity": 10, "operator": "test",
                    "season_start_month": 1, "season_end_month": 12,
                }
            ],
            "edges": [],
        }
        path = tmp_path / "lonely.json"
        path.write_text(json.dumps(dataset))
        repo = JsonHutRepository(path)
        assert repo.neighbours("lonely") == ()


class TestFailureModes:
    """The repository should fail loudly on malformed data."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(RepositoryError, match="not found"):
            JsonHutRepository(missing)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{ this is not json")
        with pytest.raises(RepositoryError, match="Invalid JSON"):
            JsonHutRepository(path)

    def test_missing_required_keys_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "incomplete.json"
        path.write_text(json.dumps({"huts": []}))
        with pytest.raises(RepositoryError, match="must contain"):
            JsonHutRepository(path)

    def test_duplicate_hut_id_raises(self, tmp_path: Path) -> None:
        hut = {
            "id": "dup", "name": "Dup",
            "lat": 60.0, "lon": 7.0, "elevation_m": 1000,
            "capacity": 10, "operator": "test",
            "season_start_month": 1, "season_end_month": 12,
        }
        dataset = {"huts": [hut, hut], "edges": []}
        path = tmp_path / "dup.json"
        path.write_text(json.dumps(dataset))
        with pytest.raises(RepositoryError, match="Duplicate hut id"):
            JsonHutRepository(path)

    def test_edge_with_unknown_hut_raises(self, tmp_path: Path) -> None:
        dataset = {
            "huts": [
                {
                    "id": "a", "name": "A",
                    "lat": 60.0, "lon": 7.0, "elevation_m": 1000,
                    "capacity": 10, "operator": "test",
                    "season_start_month": 1, "season_end_month": 12,
                }
            ],
            "edges": [
                {
                    "from_hut_id": "a", "to_hut_id": "ghost",
                    "distance_km": 5.0, "elevation_gain_m": 100,
                }
            ],
        }
        path = tmp_path / "dangling.json"
        path.write_text(json.dumps(dataset))
        with pytest.raises(RepositoryError, match="unknown hut"):
            JsonHutRepository(path)


class TestProtocolConformance:
    """Sanity check: JsonHutRepository structurally matches HutRepository."""

    def test_jsonhut_satisfies_protocol(self, repo: JsonHutRepository) -> None:
        # If this typechecks (mypy will catch it), and the methods exist
        # at runtime, the protocol is satisfied.
        assert hasattr(repo, "get_hut")
        assert hasattr(repo, "all_huts")
        assert hasattr(repo, "neighbours")
        assert callable(repo.get_hut)