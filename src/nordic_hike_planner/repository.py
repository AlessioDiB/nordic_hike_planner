"""Repository layer: loads hut data and exposes a graph-friendly interface.

The HutRepository protocol defines the contract the planner depends on.
JsonHutRepository is the concrete implementation backed by a JSON file.
Other implementations (OSM, DNT scrape, SQL) could be added without
touching the planner.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from nordic_hike_planner.models import Edge, Hut


class RepositoryError(Exception):
    """Raised when repository data is malformed or inconsistent."""


class HutRepository(Protocol):
    """Contract for any source of hut and edge data.

    The planner depends on this protocol, not on any concrete implementation.
    """

    def get_hut(self, hut_id: str) -> Hut:
        """Return the hut with the given id, or raise KeyError."""
        ...

    def all_huts(self) -> tuple[Hut, ...]:
        """Return all huts in the dataset."""
        ...

    def neighbours(self, hut_id: str) -> tuple[tuple[Hut, Edge], ...]:
        """Return all (neighbour_hut, edge) pairs reachable from hut_id.

        Each pair represents a direct walkable leg. Returns an empty tuple
        if the hut has no neighbours (an isolated node).
        """
        ...


class JsonHutRepository:
    """Loads huts and edges from a JSON file and exposes them as a graph.

    Graph construction is eager: the adjacency map is built once at
    construction time. This trades a small upfront cost for fast neighbour
    lookups during planning.
    """

    def __init__(self, data_path: Path) -> None:
        self._data_path = data_path
        self._huts: dict[str, Hut] = {}
        self._adjacency: dict[str, list[tuple[Hut, Edge]]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        """Parse the JSON file and construct the in-memory graph.

        Fails loudly on malformed data, duplicate hut ids, or edges
        referencing unknown huts.
        """
        try:
            raw = json.loads(self._data_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RepositoryError(f"Data file not found: {self._data_path}") from exc
        except json.JSONDecodeError as exc:
            raise RepositoryError(f"Invalid JSON in {self._data_path}: {exc}") from exc

        if "huts" not in raw or "edges" not in raw:
            raise RepositoryError(
                f"Data file must contain 'huts' and 'edges' keys; got {list(raw)}"
            )

        # Use TypeAdapter for batch validation — faster and clearer than a loop
        huts = TypeAdapter(list[Hut]).validate_python(raw["huts"])
        edges = TypeAdapter(list[Edge]).validate_python(raw["edges"])

        # Index huts by id, rejecting duplicates
        for hut in huts:
            if hut.id in self._huts:
                raise RepositoryError(f"Duplicate hut id: {hut.id}")
            self._huts[hut.id] = hut

        # Build the adjacency map, treating each edge as bidirectional
        for edge in edges:
            if edge.from_hut_id not in self._huts:
                raise RepositoryError(f"Edge references unknown hut: {edge.from_hut_id}")
            if edge.to_hut_id not in self._huts:
                raise RepositoryError(f"Edge references unknown hut: {edge.to_hut_id}")

            from_hut = self._huts[edge.from_hut_id]
            to_hut = self._huts[edge.to_hut_id]

            self._adjacency[edge.from_hut_id].append((to_hut, edge))
            # Bidirectional: add the reverse direction with a synthetic edge.
            # We invert from/to so the edge's direction matches the traversal.
            reverse_edge = Edge(
                from_hut_id=edge.to_hut_id,
                to_hut_id=edge.from_hut_id,
                distance_km=edge.distance_km,
                elevation_gain_m=edge.elevation_gain_m,
            )
            self._adjacency[edge.to_hut_id].append((from_hut, reverse_edge))

    def get_hut(self, hut_id: str) -> Hut:
        try:
            return self._huts[hut_id]
        except KeyError as exc:
            raise KeyError(f"Unknown hut id: {hut_id}") from exc

    def all_huts(self) -> tuple[Hut, ...]:
        return tuple(self._huts.values())

    def neighbours(self, hut_id: str) -> tuple[tuple[Hut, Edge], ...]:
        if hut_id not in self._huts:
            raise KeyError(f"Unknown hut id: {hut_id}")
        return tuple(self._adjacency[hut_id])
