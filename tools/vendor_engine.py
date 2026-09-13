"""Fetch the pinned FlyPath engine and place it in the QGIS plugin."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "flypath-engine.json"
DESTINATION = ROOT / "flypath_engine"
SOURCE_RECORD = "SOURCE.json"


def _config():
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not all(data.get(key) for key in ("repository", "tag", "commit")):
        raise ValueError("flypath-engine.json requires repository, tag, and commit.")
    return data


def _version(package):
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        (package / "__init__.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise ValueError("Engine package has no __version__.")
    return match.group(1)


def _digest(package):
    digest = hashlib.sha256()
    for path in sorted(p for p in package.rglob("*") if p.is_file() and p.name != SOURCE_RECORD):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _check_package(package, config):
    expected_version = config["tag"].removeprefix("v")
    if _version(package) != expected_version:
        raise ValueError(f"Vendored engine is not {config['tag']}.")
    record = json.loads((package / SOURCE_RECORD).read_text(encoding="utf-8"))
    if record.get("tag") != config["tag"] or record.get("commit") != config["commit"]:
        raise ValueError("Vendored engine source does not match flypath-engine.json.")
    if record.get("sha256") != _digest(package):
        raise ValueError("Vendored engine files were changed outside the update command.")


def _replace_package(source, destination, config):
    staging = destination.with_name(f".{destination.name}.new")
    backup = destination.with_name(f".{destination.name}.old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    record = {"tag": config["tag"], "commit": config["commit"], "sha256": _digest(staging)}
    (staging / SOURCE_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if destination.exists():
        destination.rename(backup)
    try:
        staging.rename(destination)
    except Exception:
        if backup.exists():
            backup.rename(destination)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify the committed vendored copy")
    parser.add_argument("--repository", help="override the repository URL with a local checkout")
    args = parser.parse_args()
    config = _config()
    if args.check:
        _check_package(DESTINATION, config)
        print(f"Vendored FlyPath engine {config['tag']} is current.")
        return

    repository = args.repository or config["repository"]
    with tempfile.TemporaryDirectory(prefix="flypath-engine-") as temp:
        checkout = Path(temp) / "engine"
        subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "--branch", config["tag"],
             "--single-branch", repository, str(checkout)],
            check=True,
        )
        commit = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if commit != config["commit"]:
            raise ValueError(f"Tag {config['tag']} resolved to unexpected commit {commit}.")
        source = checkout / "src" / "flypath_engine"
        if not source.is_dir():
            raise ValueError("Engine repository has no src/flypath_engine package.")
        if _version(source) != config["tag"].removeprefix("v"):
            raise ValueError("Engine package version does not match its tag.")
        _replace_package(source, DESTINATION, config)
    print(f"Vendored FlyPath engine {config['tag']} ({config['commit'][:12]}).")


if __name__ == "__main__":
    main()
