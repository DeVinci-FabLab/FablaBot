"""One version number for the project, and image tags that move only with a release."""

import json
import os
from pathlib import Path
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "ghcr.io/devinci-fablab/fablabot"
SHA = "0123456789abcdef0123456789abcdef01234567"


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


def _run_tags(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the tag script with the given variables; return the run and what it wrote to GITHUB_OUTPUT."""
    output = tmp_path / "github_output"
    output.touch()
    run = subprocess.run(
        ["bash", str(ROOT / ".github" / "scripts" / "image-tags.sh")],
        env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    return run, output.read_text(encoding="utf-8")


def test_a_push_to_main_publishes_edge_and_its_commit(tmp_path: Path) -> None:
    """Without a release, the image gets :edge and :sha-<commit> only."""
    run, output = _run_tags(tmp_path, GITHUB_SHA=SHA, RELEASE_CREATED="", VERSION="")
    assert run.returncode == 0, run.stderr
    assert output == f"tags<<EOF\n{IMAGE}:edge\n{IMAGE}:sha-{SHA}\nEOF\nversion=edge\n"


def test_a_release_also_moves_latest_and_its_version_tags(tmp_path: Path) -> None:
    """A release adds :latest, :X.Y.Z and :X.Y, and labels the image with its version."""
    run, output = _run_tags(tmp_path, GITHUB_SHA=SHA, RELEASE_CREATED="true", VERSION="0.2.0")
    assert run.returncode == 0, run.stderr
    assert output == (
        f"tags<<EOF\n{IMAGE}:edge\n{IMAGE}:sha-{SHA}\n{IMAGE}:latest\n{IMAGE}:0.2.0\n{IMAGE}:0.2\nEOF\nversion=0.2.0\n"
    )


@pytest.mark.parametrize("version", ["", "0.2", "v0.2.0", "0.2.0-rc.1", "0.2.0\nx", "0.2.0; true"])
def test_a_malformed_release_version_publishes_nothing(tmp_path: Path, version: str) -> None:
    """A release whose version is not X.Y.Z stops the build before any tag is written."""
    run, output = _run_tags(tmp_path, GITHUB_SHA=SHA, RELEASE_CREATED="true", VERSION=version)
    assert run.returncode == 1
    assert "Not a release version" in run.stderr
    assert output == ""


@pytest.mark.parametrize("sha", ["", "0123456", SHA.upper(), f"{SHA}0"])
def test_a_malformed_commit_publishes_nothing(tmp_path: Path, sha: str) -> None:
    """A commit that is not a full lower-case SHA stops the build before any tag is written."""
    run, output = _run_tags(tmp_path, GITHUB_SHA=sha, RELEASE_CREATED="", VERSION="")
    assert run.returncode == 1
    assert "Not a commit SHA" in run.stderr
    assert output == ""
