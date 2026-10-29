"""One version number for the project, and image tags that move only with a release."""

import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    """Return the parsed pyproject.toml."""
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _release_config() -> dict:
    """Return the parsed release-please configuration."""
    return json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))


def test_one_version_in_pyproject_lock_and_manifest() -> None:
    """The project version in pyproject.toml is the one uv.lock and the release manifest record."""
    project = _pyproject()["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [package["version"] for package in lock["package"] if package["name"] == project["name"]]
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))
    assert locked == [project["version"]]
    assert manifest == {".": project["version"]}


def test_a_release_bumps_the_lock_with_pyproject() -> None:
    """release-please reads the version from pyproject.toml and writes it to uv.lock as well, so the lock stays valid."""
    config = _release_config()
    name = _pyproject()["project"]["name"]
    assert config["release-type"] == "python"
    assert config["include-component-in-tag"] is False
    assert config["bump-minor-pre-major"] is True
    assert "release-as" not in json.dumps(config)
    assert config["packages"] == {
        ".": {
            "package-name": name,
            "extra-files": [
                {"type": "toml", "path": "uv.lock", "jsonpath": f"$.package[?(@.name.value=='{name}')].version"},
            ],
        },
    }
