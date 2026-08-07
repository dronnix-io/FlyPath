"""
wpml/factory.py
---------------
Chooses the mission writer for a drone by its category and delegates.

This is the single branch point between output formats: consumer drones use the
DJI Fly waypoint writer today, and enterprise (DJI Pilot 2) formats plug in here
later without touching the consumer path.
"""

from . import consumer

_WRITERS = {
    'consumer': consumer.write,
    # 'enterprise': enterprise.write,   # added in a later step
}


def write_mission(drone, spec, filepath):
    """Write `spec` as a mission for `drone` to `filepath`, picking the writer
    by drone.category. Raises ValueError for a category with no writer."""
    writer = _WRITERS.get(drone.category)
    if writer is None:
        raise ValueError(
            f'No mission writer for drone category {drone.category!r} '
            f'({drone.name}).')
    writer(drone, spec, filepath)
