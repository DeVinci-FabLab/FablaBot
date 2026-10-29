"""Checks on the Dockerfile and the build-context allow-list."""

from pathlib import Path
import re

import pytest

from fablabot.healthcheck import HEARTBEAT_INTERVAL_SECONDS

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "docker" / "Dockerfile").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text()
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

UV_FROM = re.compile(r"^FROM ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64} AS uv$")


def stages(text: str) -> dict[str, list[str]]:
    """Split a Dockerfile into its stages.

    Args:
        text: The Dockerfile content.

    Returns:
        The lines of each stage, keyed by stage name, without comments or blanks.
    """
    result: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        match = re.match(r"^FROM .+ AS (\w+)$", line)
        if match:
            current = result.setdefault(match.group(1), [])
        if current is not None and line.strip() and not line.lstrip().startswith("#"):
            current.append(line)
    return result


def first_from() -> str:
    """Return the first FROM line of the Dockerfile."""
    return next(line for line in DOCKERFILE.splitlines() if line.startswith("FROM"))


def test_uv_is_pinned_by_version_and_digest() -> None:
    """Uv comes from its release image, pinned by version and digest."""
    assert UV_FROM.match(first_from())


def test_uv_matches_ci() -> None:
    """The uv version in the Dockerfile is the one CI uses."""
    if not CI_WORKFLOW.exists() or "setup-uv" not in CI_WORKFLOW.read_text():
        pytest.skip("no setup-uv step in the CI workflow")
    ci = re.search(r'version:\s*"(\d+\.\d+\.\d+)"', CI_WORKFLOW.read_text())
    pinned = UV_FROM.match(first_from())
    assert ci is not None
    assert pinned is not None
    assert pinned.group(1) == ci.group(1)


def test_production_stage_holds_only_the_environment() -> None:
    """The production stage copies the built environment and nothing else."""
    production = stages(DOCKERFILE)["production"]
    copies = [line for line in production if line.startswith("COPY")]
    assert copies == ["COPY --from=build /app/.venv /app/.venv"]
    assert not any(line.startswith("RUN") for line in production)
    body = "\n".join(production)
    assert "/app/data" not in body
    assert "/app/logs" not in body


def test_production_runs_as_10001_throughout() -> None:
    """The image switches to 10001:10001 once, before it starts anything."""
    lines = [line for line in DOCKERFILE.splitlines() if line.startswith("USER")]
    assert lines == ["USER 10001:10001"]
    production = stages(DOCKERFILE)["production"]
    assert "USER 10001:10001" in production
    user = production.index("USER 10001:10001")
    after = production[user:]
    assert any(line.startswith("HEALTHCHECK") for line in after)
    assert any(line.startswith("CMD") for line in after)
    assert "ENTRYPOINT" not in DOCKERFILE


def test_stop_signal_health_check_and_command() -> None:
    """The production stage stops on SIGINT, checks the heartbeat and runs the bot."""
    production = stages(DOCKERFILE)["production"]
    health = (
        'HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=1 CMD ["python", "-m", "fablabot.healthcheck"]'
    )
    assert "STOPSIGNAL SIGINT" in production
    assert health in production
    assert 'CMD ["python", "-m", "fablabot.main"]' in production
    assert f"--interval={HEARTBEAT_INTERVAL_SECONDS}s" in health


def test_images_install_from_the_lock() -> None:
    """Both images install from uv.lock and never with pip."""
    parsed = stages(DOCKERFILE)
    build = "\n".join(parsed["build"])
    assert "UV_COMPILE_BYTECODE=1" in build
    assert "RUN uv sync --locked --no-dev --no-editable --no-cache" in parsed["build"]
    assert "RUN uv sync --locked --no-install-project" in parsed["development"]
    assert "pip install" not in DOCKERFILE


def test_build_context_is_an_allow_list() -> None:
    """The build context admits only the listed files."""
    lines = [line for line in DOCKERIGNORE.splitlines() if line and not line.startswith("#")]
    assert lines[0] == "*"
    assert [line[1:] for line in lines if line.startswith("!")] == [
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "src/",
    ]
