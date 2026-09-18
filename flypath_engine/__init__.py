"""Pure Python mission-planning core shared by FlyPath products."""

__version__ = "0.4.0"

from .planning import PlanningError, plan_2d
from .route import boustrophedon_route, split_by_waypoint_count, split_waypoints

__all__ = (
    "__version__",
    "PlanningError",
    "boustrophedon_route",
    "plan_2d",
    "split_by_waypoint_count",
    "split_waypoints",
)
