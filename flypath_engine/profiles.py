"""Versioned drone profiles shared by FlyPath clients."""

import json
from functools import lru_cache
from pathlib import Path


_PROFILE_FILE = Path(__file__).with_name("profiles") / "drones.json"


@lru_cache(maxsize=1)
def load_drone_profiles():
    profiles = json.loads(_PROFILE_FILE.read_text(encoding="utf-8"))
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Drone profiles must be a non-empty object.")
    codes = [profile.get("website_code") for profile in profiles.values() if profile.get("website_code")]
    if len(codes) != len(set(codes)):
        raise ValueError("Drone profile website codes must be unique.")
    return profiles


def drone_profile(code):
    for name, profile in load_drone_profiles().items():
        if profile.get("website_code") == code:
            return name, profile
    raise KeyError(code)
