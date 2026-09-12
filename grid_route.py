"""Compatibility adapter for the shared route implementation."""

try:
    from .flypath_engine.route import (
        boustrophedon_route,
        cell_turns,
        decompose_cells,
        order_cells,
        split_by_waypoint_count,
        split_waypoints,
    )
except ImportError:  # Direct execution by the plugin's pure-Python tests.
    from flypath_engine.route import (
        boustrophedon_route,
        cell_turns,
        decompose_cells,
        order_cells,
        split_by_waypoint_count,
        split_waypoints,
    )

__all__ = (
    "boustrophedon_route",
    "cell_turns",
    "decompose_cells",
    "order_cells",
    "split_by_waypoint_count",
    "split_waypoints",
)
