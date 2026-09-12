"""Pure Python mission-planning core shared by FlyPath products."""

__version__ = "0.2.0"

from .route import boustrophedon_route, split_by_waypoint_count, split_waypoints

__all__ = (
    "__version__",
    "boustrophedon_route",
    "split_by_waypoint_count",
    "split_waypoints",
)
