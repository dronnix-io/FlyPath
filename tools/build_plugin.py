"""Build a QGIS plugin ZIP after refreshing the pinned planning engine."""

import argparse
import configparser
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".github", "build", "dist", "docs", "tests", "tools"}
EXCLUDED_ROOT_FILES = {
    ".gitignore", "CONTEXT.md", "CONTRIBUTING.md", "SECURITY.md",
    "flypath-engine.json",
}


def _include(relative):
    return (
        relative.parts[0] not in EXCLUDED
        and not relative.parts[0].startswith(".")
        and relative.as_posix() not in EXCLUDED_ROOT_FILES
        and "__pycache__" not in relative.parts
        and relative.suffix not in {".pyc", ".zip"}
    )


def _tracked_files():
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    ).stdout
    files = {Path(value.decode()) for value in output.split(b"\0") if value}
    files.update(path.relative_to(ROOT) for path in (ROOT / "flypath_engine").rglob("*") if path.is_file())
    return sorted(path for path in files if _include(path) and (ROOT / path).is_file())


def _version():
    metadata = configparser.ConfigParser()
    metadata.read(ROOT / "metadata.txt", encoding="utf-8")
    return metadata["general"]["version"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-repository", help="use a local engine checkout")
    args = parser.parse_args()
    vendor = [sys.executable, str(ROOT / "tools" / "vendor_engine.py")]
    if args.engine_repository:
        vendor.extend(("--repository", args.engine_repository))
    subprocess.run(vendor, cwd=ROOT, check=True)

    output = ROOT / "dist" / f"FlyPath-{_version()}.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in _tracked_files():
            archive.write(ROOT / relative, Path("FlyPath") / relative)
    print(output)


if __name__ == "__main__":
    main()
