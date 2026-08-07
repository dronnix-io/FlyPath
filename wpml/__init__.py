"""DJI WPML mission writers. `write_mission` picks the right format for a drone;
`MissionSpec` carries the mission inputs."""

from .base import MissionSpec
from .factory import write_mission

__all__ = ['MissionSpec', 'write_mission']
