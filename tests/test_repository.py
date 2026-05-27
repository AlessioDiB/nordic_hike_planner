from pathlib import Path

from nordic_hike_planner.repository import JsonHutRepository


def test_repository_loads_real_data() -> None:
    # Point the test at our actual JSON file
    data_path = Path("data/hardangervidda.json")
    repo = JsonHutRepository(data_path)

    huts = repo.get_huts()
    edges = repo.get_edges()

    # Prove that it successfully loaded data
    assert len(huts) == 3
    assert len(edges) == 2

    # Prove that it successfully created the Pydantic models
    assert huts[0].name == "Finsehytta"
    assert edges[0].start_hut_name == "Finsehytta"