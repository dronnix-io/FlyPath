"""Drone hardware registry: a single, validated source of truth for the drones
FlyPath supports (data in drones.json, typed model in models.py)."""

from . import registry
from .models import Camera, Drone

__all__ = ['registry', 'Camera', 'Drone']
