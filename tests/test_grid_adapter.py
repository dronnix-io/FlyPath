"""QGIS adapter check against the stored plugin reference."""

import importlib
import json
import os
from pathlib import Path
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt" and os.environ.get("OSGEO4W_ROOT"):
    qgis_root = Path(os.environ["OSGEO4W_ROOT"])
    _dll_directories = [
        os.add_dll_directory(str(qgis_root / path))
        for path in ("bin", "apps/Qt5/bin", "apps/qgis-ltr/bin")
    ]

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsPointXY,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "flypath_adapter_test"
module = types.ModuleType(PACKAGE)
module.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = module
grid = importlib.import_module(f"{PACKAGE}.grid_planner")


def test_qgis_adapter_matches_reference():
    fixture = json.loads((ROOT / "tests/shared_engine/fixtures/plugin-direction.json").read_text())
    profile = json.loads((ROOT / "tests/shared_engine/profiles/mini3pro-v1.json").read_text())
    polygon = fixture["polygon"]
    geometry = QgsGeometry.fromPolygonXY([[
        QgsPointXY(longitude, latitude)
        for latitude, longitude in polygon + polygon[:1]
    ]])
    crs = QgsCoordinateReferenceSystem("EPSG:4326")
    for case in fixture["cases"]:
        actual, spacing = grid.generate_flight_grid(
            geometry, crs, 80, 16, .7, case["direction"], 0, profile["camera"]
        )
        expected = [(longitude, latitude) for latitude, longitude in case["waypoints"]]
        assert spacing == 16
        assert len(actual) == len(expected)
        assert all(abs(a - e) <= 1e-9 for point, reference in zip(actual, expected)
                   for a, e in zip(point, reference))


def test_qgis_adapter_uses_ellipsoidal_area():
    request = json.loads(
        (ROOT / "tests/shared_engine/fixtures/reported-mission-126-input.json").read_text()
    )
    ring = request["survey_area"]["exterior"]
    geometry = QgsGeometry.fromPolygonXY([[
        QgsPointXY(point["longitude_deg"], point["latitude_deg"])
        for point in ring + ring[:1]
    ]])
    area_ha = grid.measure_survey_area(
        geometry, QgsCoordinateReferenceSystem("EPSG:4326")
    ) / 10_000
    assert 11.1 < area_ha < 11.3, area_ha


def test_qgis_adapter_uses_shared_route_distance():
    request = json.loads(
        (ROOT / "tests/shared_engine/fixtures/reported-mission-126-input.json").read_text()
    )
    profile = json.loads(
        (ROOT / "tests/shared_engine/profiles/mini3pro-v1.json").read_text()
    )
    ring = request["survey_area"]["exterior"]
    geometry = QgsGeometry.fromPolygonXY([[
        QgsPointXY(point["longitude_deg"], point["latitude_deg"])
        for point in ring + ring[:1]
    ]])
    waypoints, _spacing = grid.generate_flight_grid(
        geometry, QgsCoordinateReferenceSystem("EPSG:4326"), 80, 16, .7, 70, 0,
        profile["camera"],
    )
    assert 3_824 < grid.measure_route(waypoints) < 3_826


if __name__ == "__main__":
    app = QgsApplication([], False)
    app.initQgis()
    try:
        test_qgis_adapter_matches_reference()
        test_qgis_adapter_uses_ellipsoidal_area()
        test_qgis_adapter_uses_shared_route_distance()
        print("PASS  test_qgis_adapter_matches_reference")
    finally:
        app.exitQgis()
