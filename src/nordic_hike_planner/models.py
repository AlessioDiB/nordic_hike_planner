from pydantic import BaseModel

class Hut(BaseModel):
    name: str
    elevation_meters: int
    beds: int

class Edge(BaseModel):
    start_hut_name: str
    end_hut_name: str
    distance_km: float
    estimated_hours: float