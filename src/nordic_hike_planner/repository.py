"""Repository layer: loads hut data and exposes a graph-friendly interface.

The HutRepository protocol defines the contract the planner depends on.
JsonHutRepository is the concrete implementation backed by a JSON file.
Other implementations (OSM, DNT scrape, SQL) could be added without
touching the planner.

Why a repository layer?
    The planner shouldn't know or care where its data comes from. By
    depending on a Protocol rather than a concrete implementation, we
    can swap data sources (JSON → database → external API) by writing a
    new class — the planner is untouched. This is the Dependency
    Inversion Principle in its simplest form.
"""

from __future__ import annotations

import json

# defaultdict gives us "set if missing" semantics without explicit checks
# when building the adjacency map. A regular dict would require an
# `if key not in d: d[key] = []` line before each append.
from collections import defaultdict
from pathlib import Path

# Protocol enables structural typing — any class with the right methods
# satisfies the protocol, no inheritance required. This is cleaner than
# an ABC for a pure-interface contract.
from typing import Protocol

# TypeAdapter is Pydantic's batch validator. Faster than constructing
# models one at a time in a loop, and the error messages are clearer
# because they include the full list context.
from pydantic import TypeAdapter

from nordic_hike_planner.models import Edge, Hut


class RepositoryError(Exception):
    """Raised when repository data is malformed or inconsistent.

    Distinct from Python's built-in errors so callers can catch and
    handle data-loading problems specifically. E.g., the FastAPI lifespan
    catches RepositoryError to fail-fast at startup rather than crash on
    the first request.
    """


class HutRepository(Protocol):
    """Contract for any source of hut and edge data.

    The planner depends on this protocol, not on any concrete implementation.

    Why a Protocol and not an ABC?
        Protocols give structural typing (duck typing with static checks).
        Any class with these three methods satisfies the protocol — no
        inheritance, no registration. Tests can pass simple fakes; future
        implementations don't need to know this protocol exists. ABCs
        are the right choice when you want to share implementation via
        inheritance, which we don't here.
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

    Why eager rather than lazy?
        Loading is slow (file I/O, JSON parse, Pydantic validation, graph
        construction); planning is fast (heap operations and dict lookups).
        Doing the loading once at startup means every plan request gets
        consistent, fast neighbour lookups. The alternative — lazy loading
        on first access — would only matter if loading was expensive AND
        the repository might never be queried, which doesn't apply here.
    """

    def __init__(self, data_path: Path) -> None:
        """Initialise from a JSON file. Triggers immediate loading.

        Loading happens in __init__ (not lazily) so that any data errors
        surface at construction time. A reviewer or test that constructs
        a JsonHutRepository immediately knows whether the data file is valid.
        """
        self._data_path = data_path

        # Hut ID → Hut. dict (not defaultdict) because missing keys are
        # genuine errors we want to raise on, not silently fill with empties.
        self._huts: dict[str, Hut] = {}

        # Hut ID → list of (neighbour, edge) pairs. defaultdict(list) means
        # we can `.append` without first checking whether the key exists.
        self._adjacency: dict[str, list[tuple[Hut, Edge]]] = defaultdict(list)

        # Perform loading immediately so any data errors surface here, not
        # at the first request.
        self._load()

    def _load(self) -> None:
        """Parse the JSON file and construct the in-memory graph.

        Fails loudly on malformed data, duplicate hut ids, or edges
        referencing unknown huts.

        Why fail loudly rather than skip bad entries?
            Silent partial loads are the worst possible default. If the
            JSON file references a hut that doesn't exist, that's almost
            certainly a typo or an incomplete refactor — exactly the bug
            we want to catch at load time, not as "planner can't find a
            path" three days later in production.
        """
        # Catch file-not-found and invalid-JSON as distinct error categories
        # and re-raise as RepositoryError. This way callers only need to
        # handle one exception type, but the wrapped error keeps the
        # original cause via `from exc` for debugging.
        try:
            raw = json.loads(self._data_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RepositoryError(f"Data file not found: {self._data_path}") from exc
        except json.JSONDecodeError as exc:
            raise RepositoryError(f"Invalid JSON in {self._data_path}: {exc}") from exc

        # Schema check. We expect a JSON object with `huts` and `edges`
        # arrays at the top level. Other keys (like our `_metadata`) are
        # permitted but ignored.
        if "huts" not in raw or "edges" not in raw:
            raise RepositoryError(
                f"Data file must contain 'huts' and 'edges' keys; got {list(raw)}"
            )

        # Use TypeAdapter for batch validation — faster and clearer than a loop
        # constructing models one at a time. If any single hut/edge is
        # malformed, Pydantic raises with the index and field, pointing
        # us straight at the bad entry.
        huts = TypeAdapter(list[Hut]).validate_python(raw["huts"])
        edges = TypeAdapter(list[Edge]).validate_python(raw["edges"])

        # Index huts by id, rejecting duplicates.
        # Duplicate IDs would silently overwrite each other if we just used
        # `self._huts[hut.id] = hut`. Explicit check turns that into a
        # loud failure.
        for hut in huts:
            if hut.id in self._huts:
                raise RepositoryError(f"Duplicate hut id: {hut.id}")
            self._huts[hut.id] = hut

        # Build the adjacency map, treating each edge as bidirectional.
        # Why bidirectional? Real-world hiking trails are walkable both
        # ways. Writing each edge twice in the JSON would be redundant
        # and error-prone, so the JSON stores each edge once and we
        # expand it here at load time.
        for edge in edges:
            # Validate both endpoints exist before adding the edge.
            # Dangling references would otherwise produce mysterious
            # planner errors at query time.
            if edge.from_hut_id not in self._huts:
                raise RepositoryError(f"Edge references unknown hut: {edge.from_hut_id}")
            if edge.to_hut_id not in self._huts:
                raise RepositoryError(f"Edge references unknown hut: {edge.to_hut_id}")

            from_hut = self._huts[edge.from_hut_id]
            to_hut = self._huts[edge.to_hut_id]

            # Forward direction: from `from_hut`, we can reach `to_hut`
            # via the original edge.
            self._adjacency[edge.from_hut_id].append((to_hut, edge))

            # Reverse direction: synthesise a "mirror" edge with from/to
            # swapped. The edge's direction matches the traversal so the
            # planner doesn't need to reason about which way to read it.
            # Note: elevation gain is symmetric in this model — a known
            # simplification documented in the README's Limitations section.
            reverse_edge = Edge(
                from_hut_id=edge.to_hut_id,
                to_hut_id=edge.from_hut_id,
                distance_km=edge.distance_km,
                elevation_gain_m=edge.elevation_gain_m,
            )
            self._adjacency[edge.to_hut_id].append((from_hut, reverse_edge))

    def get_hut(self, hut_id: str) -> Hut:
        """Look up a hut by ID.

        Raises KeyError if the hut doesn't exist. We wrap the default
        KeyError message because the bare one ("'finsex'") isn't very
        informative; our version says "Unknown hut id: finsex" which
        is clearer in logs.
        """
        try:
            return self._huts[hut_id]
        except KeyError as exc:
            raise KeyError(f"Unknown hut id: {hut_id}") from exc

    def all_huts(self) -> tuple[Hut, ...]:
        """All huts in the dataset, as an immutable tuple.

        Returning a tuple (rather than a list) signals that callers
        shouldn't mutate the result — modifications wouldn't propagate
        anyway since we'd be returning a copy, but a tuple makes the
        intent explicit.
        """
        return tuple(self._huts.values())

    def neighbours(self, hut_id: str) -> tuple[tuple[Hut, Edge], ...]:
        """All (neighbour, edge) pairs reachable in one step from hut_id.

        Raises KeyError for unknown hut_id. We check explicitly rather
        than relying on defaultdict's silent "return empty list" behaviour,
        because a query for an unknown hut is almost certainly a bug,
        not a legitimate "this hut has no neighbours" case.
        """
        # Explicit check before access. defaultdict would silently return
        # an empty list for unknown IDs, masking bugs.
        if hut_id not in self._huts:
            raise KeyError(f"Unknown hut id: {hut_id}")

        # Tuple conversion: same immutability signal as all_huts().
        return tuple(self._adjacency[hut_id])
