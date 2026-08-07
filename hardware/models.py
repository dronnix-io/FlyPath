"""
hardware/models.py
------------------
Typed model for a drone and its integrated camera.

Every drone FlyPath supports has one fixed camera built into the aircraft
(consumer DJI Mini bodies, and enterprise bodies such as the Matrice 4E that
ship with their own mapping camera). There are no swappable payloads, so a
drone is fully described by its aircraft data plus that one camera.

These are plain dataclasses with no QGIS dependency, so they can be imported
and unit-tested outside a QGIS runtime.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Camera:
    """The camera integrated into a drone."""
    sensor_width_mm: float
    sensor_height_mm: float
    focal_length_mm: float
    image_width_px: int
    image_height_px: int
    # Minimum time (s) the camera needs between shots. It bounds fly-through
    # capture speed and is added per photo in full-automatic flight-time
    # estimates. ~2 s for the 12 MP Minis; confirm per drone.
    min_shoot_interval_s: float = 2.0
    # DJI payload identifiers, used by the enterprise (Pilot 2) WPML writer.
    # Consumer missions do not emit a payload block, so these stay at 0 there.
    payload_enum: int = 0
    payload_sub_enum: int = 0
    payload_position_index: int = 0

    def footprint_across(self, altitude_m):
        """Ground footprint width (m) across the flight direction at altitude."""
        return altitude_m * self.sensor_width_mm / self.focal_length_mm

    def footprint_along(self, altitude_m):
        """Ground footprint length (m) along the flight direction at altitude."""
        return altitude_m * self.sensor_height_mm / self.focal_length_mm

    def gsd_cm_per_px(self, altitude_m):
        """Ground sample distance (cm/px) at the given altitude."""
        return (altitude_m * self.sensor_width_mm * 100.0) / (
            self.focal_length_mm * self.image_width_px)


@dataclass(frozen=True)
class Drone:
    """A drone body plus its integrated camera."""
    name: str
    category: str          # 'consumer' | 'enterprise'
    app: str               # 'DJI Fly' | 'DJI Pilot 2'
    drone_enum: int
    drone_sub_enum: int
    max_speed_ms: float
    battery_time_min: int
    camera: Camera
    info: str
    # Slowest allowed waypoint speed. Optional in the data (defaults to 1 m/s)
    # because not every drone documents a minimum.
    min_speed_ms: float = 1.0
    verified: str = ''
    # Whether the drone is offered in the UI. Kept in the registry (and tested)
    # even when False, so unfinished/unverified drones stay in the code but out
    # of the user's drop-down until they're ready.
    available: bool = True

    def speed_range(self):
        """(min, max) waypoint speed in m/s for this drone."""
        return self.min_speed_ms, self.max_speed_ms

    def grid_specs(self):
        """Minimal dict consumed by grid_planner.generate_flight_grid."""
        return {
            'focal_length_mm': self.camera.focal_length_mm,
            'sensor_width_mm': self.camera.sensor_width_mm,
        }
