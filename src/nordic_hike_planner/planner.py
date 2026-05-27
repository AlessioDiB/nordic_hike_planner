import heapq

from nordic_hike_planner.calculator import NaismithCalculator
from nordic_hike_planner.models import Edge, Hut
from nordic_hike_planner.repository import JsonHutRepository


class PathNotFoundError(Exception):
    """Raised when it is impossible to reach the target hut."""
    pass


class AStarPlanner:
    def __init__(self, repository: JsonHutRepository) -> None:
        # 1. Grab the raw data
        self.huts: dict[str, Hut] = {hut.name: hut for hut in repository.get_huts()}
        self.edges: dict[str, list[Edge]] = {hut.name: [] for hut in self.huts.values()}

        # 2. Build the two-way trail network (the Graph)
        for edge in repository.get_edges():
            self.edges[edge.start_hut_name].append(edge)

            # Automatically create the return trip so hikers can walk both ways
            reverse_edge = Edge(
                start_hut_name=edge.end_hut_name,
                end_hut_name=edge.start_hut_name,
                distance_km=edge.distance_km,
                estimated_hours=edge.estimated_hours
            )
            self.edges[edge.end_hut_name].append(reverse_edge)

    def find_best_route(self, start: str, target: str) -> tuple[list[str], float, float]:
        """
        Calculates the fastest route.
        Returns: (List of hut names, total distance in km, total estimated hours).
        """
        if start not in self.huts or target not in self.huts:
            raise ValueError(f"Unknown hut. Start: {start}, Target: {target}")

        # Priority queue stores: (total_hours, current_hut, path_history, total_distance)
        queue = [(0.0, start, [start], 0.0)]
        visited = set()

        while queue:
            current_hours, current_hut, path, current_distance = heapq.heappop(queue)

            # If we reached the end, return the final calculations!
            if current_hut == target:
                return path, current_distance, current_hours

            if current_hut in visited:
                continue

            visited.add(current_hut)

            # Look at all connected trails and calculate the cost to walk them
            for edge in self.edges.get(current_hut, []):
                next_hut = edge.end_hut_name
                if next_hut not in visited:
                    # Use existing hours if present, otherwise calculate via Naismith
                    hours = edge.estimated_hours or NaismithCalculator.calculate_hours(edge)
                    new_hours = current_hours + hours
                    new_distance = current_distance + edge.distance_km
                    heapq.heappush(queue, (new_hours, next_hut, [*path, next_hut], new_distance))
                    
        raise PathNotFoundError(f"No valid trail found connecting {start} and {target}")