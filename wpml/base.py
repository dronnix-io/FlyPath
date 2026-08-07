"""
wpml/base.py
------------
Shared pieces for the mission writers.

`MissionSpec` bundles a mission's inputs, independent of drone or output format,
so writers take one object instead of a long argument list. `esc` and
`package_kmz` are the XML/zip helpers every writer uses.
"""

import io
import zipfile
from dataclasses import dataclass


@dataclass
class MissionSpec:
    """Everything a writer needs to emit a mission, drone/format independent."""
    waypoints: list                  # [(lon, lat), ...] in WGS84
    altitude_m: float
    speed_ms: float
    finish_action: str               # human label, e.g. 'Return to Home'
    rc_lost_action: str              # human label
    gimbal_pitch: float = -90.0
    mission_name: str = 'FlyPath Mission'
    create_time_ms: int = None       # preserve an existing mission's date, or None


def esc(text):
    """Minimal XML text escaping."""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))


def package_kmz(filepath, entries):
    """Write a .kmz (zip) from an ordered list of (arcname, text) entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for arc, content in entries:
            zf.writestr(arc, content)
    with open(filepath, 'wb') as f:
        f.write(buf.getvalue())
