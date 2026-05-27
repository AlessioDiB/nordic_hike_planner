from nordic_hike_planner.models import Edge


class NaismithCalculator:
    """
    Naismith's Rule:
    - 5 km/h (1 hour per 5 km)
    - +10 minutes per 100m of elevation gain
    """

    @staticmethod
    def calculate_hours(edge: Edge) -> float:
        time_for_distance = edge.distance_km / 5.0
        time_for_elevation = (edge.elevation_gain_meters / 100.0) * (1.0 / 6.0)
        return round(time_for_distance + time_for_elevation, 2)
