"""Checks for the engine vendoring command."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.vendor_engine import _check_package, _replace_package
from tools.build_plugin import _include


def test_replace_and_verify_package():
    config = {"tag": "v1.2.3", "commit": "abc123"}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source, destination = root / "source", root / "destination"
        source.mkdir()
        (source / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        destination.mkdir()
        (destination / "stale.py").write_text("stale\n", encoding="utf-8")

        _replace_package(source, destination, config)
        _check_package(destination, config)

        cache = destination / "__pycache__"
        cache.mkdir()
        (cache / "module.cpython-312.pyc").write_bytes(b"generated")
        _check_package(destination, config)

        assert not (destination / "stale.py").exists()
        assert json.loads((destination / "SOURCE.json").read_text())["tag"] == "v1.2.3"
    assert _include(Path("flypath_engine/grid.py"))
    assert not _include(Path("AGENTS.md"))
    assert not _include(Path("tests/test_grid.py"))


if __name__ == "__main__":
    test_replace_and_verify_package()
    print("PASS  test_replace_and_verify_package")
