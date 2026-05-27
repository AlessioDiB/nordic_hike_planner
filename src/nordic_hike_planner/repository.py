import json
from pathlib import Path

from nordic_hike_planner.models import Hut, Edge


class JsonHutRepository:
    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path

    def _load_data(self) -> dict:
        """Opens and reads the JSON file."""
        with open(self.data_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_huts(self) -> list[Hut]:
        """Converts raw JSON hut data into Pydantic Hut models."""
        data = self._load_data()
        return [Hut(**hut) for hut in data["huts"]]

    def get_edges(self) -> list[Edge]:
        """Converts raw JSON edge data into Pydantic Edge models."""
        data = self._load_data()
        return [Edge(**edge) for edge in data["edges"]]