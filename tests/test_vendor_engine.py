"""Checks for the engine vendoring command."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.vendor_engine import _check_package, _replace_package
from tools.build_plugin import _include, main as build_plugin
from tools import vendor_engine


def test_replace_and_verify_package():
    config = {"tag": "v1.2.3", "commit": "abc123"}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source, destination = root / "src" / "flypath_engine", root / "destination"
        source.mkdir(parents=True)
        for name in ("LICENSE", "NOTICE"):
            (root / name).write_text(name, encoding="utf-8")
        (source / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        destination.mkdir()
        (destination / "stale.py").write_text("stale\n", encoding="utf-8")

        _replace_package(source, destination, config)
        _check_package(destination, config)
        for name in ("LICENSE", "NOTICE"):
            assert (destination / name).read_text() == name

        cache = destination / "__pycache__"
        cache.mkdir()
        (cache / "module.cpython-312.pyc").write_bytes(b"generated")
        _check_package(destination, config)

        assert not (destination / "stale.py").exists()
        assert json.loads((destination / "SOURCE.json").read_text())["tag"] == "v1.2.3"
    assert _include(Path("flypath_engine/grid.py"))
    assert not _include(Path("AGENTS.md"))
    assert not _include(Path("tests/test_grid.py"))


def test_build_includes_generated_engine_without_unrelated_untracked_files():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "metadata.txt").write_text("[general]\nversion=1.0\n", encoding="utf-8")
        (root / "README.md").write_text("Plugin", encoding="utf-8")
        (root / "local.py").write_text("PRIVATE = True\n", encoding="utf-8")
        engine = root / "flypath_engine"
        engine.mkdir()
        (engine / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
        (engine / "SOURCE.json").write_text("{}", encoding="utf-8")
        for name in ("LICENSE", "NOTICE"):
            (engine / name).write_text(name, encoding="utf-8")
        (engine / "profiles").mkdir()
        (engine / "profiles" / "drones.json").write_text("{}", encoding="utf-8")
        (engine / "__pycache__").mkdir()
        (engine / "__pycache__" / "module.pyc").write_bytes(b"generated")
        with patch("tools.build_plugin.ROOT", root), patch("tools.build_plugin.subprocess.run") as run:
            # Git reports no engine files: they must be included separately.
            run.return_value.stdout = b"README.md\0metadata.txt\0"
            build_plugin()
        assert run.call_count == 2
        assert run.call_args_list[0].args[0] == [
            sys.executable, str(root / "tools" / "vendor_engine.py"),
        ]
        run.assert_called_with(
            ["git", "-c", f"safe.directory={root}", "ls-files", "-z"],
            cwd=root, check=True, capture_output=True,
        )
        with zipfile.ZipFile(root / "dist" / "FlyPath-1.0.zip") as archive:
            # SOURCE.json is deliberately left out of the packaged ZIP so the
            # plugin-repository secret scanner does not flag its commit hash.
            assert set(archive.namelist()) == {
                "FlyPath/README.md", "FlyPath/metadata.txt",
                "FlyPath/flypath_engine/__init__.py",
                "FlyPath/flypath_engine/profiles/drones.json",
                "FlyPath/flypath_engine/LICENSE", "FlyPath/flypath_engine/NOTICE",
            }


def test_commit_fetch_without_release_tag_and_return_to_tag_fetch():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repo, destination = root / 'engine', root / 'vendored'
        source = repo / 'src' / 'flypath_engine'
        source.mkdir(parents=True)
        (source / '__init__.py').write_text('__version__ = "1.2.0"\n', encoding='utf-8')
        for name in ('LICENSE', 'NOTICE'):
            (repo / name).write_text(name, encoding='utf-8')
        subprocess.run(['git', 'init', '--quiet', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test',
                        '-c', 'user.email=test@example.com', 'commit', '--quiet', '-m', 'fixture'],
                       check=True)
        commit = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                                check=True, capture_output=True, text=True).stdout.strip()
        config = {'repository': str(repo), 'tag': 'v1.2.0', 'commit': commit,
                  'fetch_by_commit': True}
        with patch.object(vendor_engine, '_config', return_value=config), \
                patch.object(vendor_engine, 'DESTINATION', destination), \
                patch.object(sys, 'argv', ['vendor_engine.py']):
            vendor_engine.main()  # No tag exists: fetch exactly the pinned commit.
            _check_package(destination, config)
            subprocess.run(['git', '-C', str(repo), 'tag', 'v1.2.0'], check=True)
            del config['fetch_by_commit']  # Release mode remains the default.
            vendor_engine.main()
            _check_package(destination, config)
            config['commit'] = '0' * 40
            try:
                vendor_engine.main()
            except ValueError as exc:
                assert 'unexpected commit' in str(exc)
            else:
                raise AssertionError('tag resolving to a different commit accepted')


if __name__ == "__main__":
    test_replace_and_verify_package()
    print("PASS  test_replace_and_verify_package")
