"""The workflows pin every action and build the image only after the checks."""

from pathlib import Path
import re

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+(\.\d+){0,2}$")
CONTRIBUTING = WORKFLOWS.parent.parent / "CONTRIBUTING.md"
STEP_START = re.compile(r"^      - ", re.MULTILINE)
RUN = re.compile(r"^\s*run:\s*(?P<command>.+)$", re.MULTILINE)
GUARD = "if: ${{ !cancelled() && steps.sync.outcome == 'success' }}"


def _uses_lines() -> list[tuple[str, str, str]]:
    """List every `uses:` line as (file name, ref, trailing text)."""
    found: list[tuple[str, str, str]] = []
    for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = USES.match(line)
            if match:
                found.append((path.name, match["ref"], match["rest"]))
    return found


def test_every_action_is_pinned_by_commit_sha() -> None:
    """Each third-party action names a full commit SHA followed by its version tag."""
    third_party = [use for use in _uses_lines() if not use[1].startswith("./")]
    assert third_party
    for name, ref, rest in third_party:
        assert PINNED.match(ref), f"{name}: {ref} is not pinned by a full commit SHA"
        assert VERSION_COMMENT.match(rest), f"{name}: {ref} has no '# vX.Y.Z' comment"


def test_image_build_waits_for_the_checks() -> None:
    """The image workflow calls the check workflow; the release and the build job both need it."""
    text = (WORKFLOWS / "CD.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/ci.yml" in text
    assert re.search(r"^    needs: check$", _job("release"), re.MULTILINE)
    assert re.search(r"^    needs: \[check, release\]$", _job("build-and-push-image"), re.MULTILINE)


def _read(name: str) -> str:
    """Return the text of one workflow file."""
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _job(job: str) -> str:
    """Return the body of one job of CD.yml, up to the next job."""
    found = re.search(rf"^  {re.escape(job)}:\n(?P<body>(?:    .*\n|\n)*)", _read("CD.yml"), re.MULTILINE)
    assert found, job
    return found["body"]


def _permissions(job: str) -> set[str]:
    """List the permissions one job of CD.yml grants, without their comments."""
    block = re.search(r"^    permissions:\n(?P<lines>(?:      .*\n)+)", _job(job), re.MULTILINE)
    assert block, job
    return {line.split("#")[0].strip() for line in block["lines"].splitlines()}


def _steps(name: str) -> list[str]:
    """Split a workflow into its step blocks, in order, each starting at its `- ` line."""
    text = _read(name)
    starts = [match.start() for match in STEP_START.finditer(text)]
    return [text[start:end] for start, end in zip(starts, [*starts[1:], len(text)], strict=True)]


def _documented_checks() -> list[str]:
    """List the commands of the 'Run the checks' block in CONTRIBUTING.md."""
    text = CONTRIBUTING.read_text(encoding="utf-8")
    block = re.search(r"^## Run the checks\n+```bash\n(?P<lines>.*?)```", text, re.MULTILINE | re.DOTALL)
    assert block
    return block["lines"].splitlines()


def test_ci_runs_the_documented_checks_in_order() -> None:
    """The run lines of ci.yml equal the commands CONTRIBUTING.md tells contributors to run."""
    assert [match["command"].strip() for match in RUN.finditer(_read("ci.yml"))] == _documented_checks()


def test_every_check_runs_after_a_good_sync_even_when_another_fails() -> None:
    """Each step after the sync carries the guard; the lockfile check only needs `!cancelled()`."""
    steps = _steps("ci.yml")
    sync = next(index for index, step in enumerate(steps) if "id: sync" in step)
    after = steps[sync + 1 :]
    assert after
    for step in after:
        if "uv lock --check" in step:
            assert "if: ${{ !cancelled() }}" in step, step
        else:
            assert GUARD in step, step


def test_no_step_can_hide_a_failure_or_force_a_run() -> None:
    """No workflow lets a step fail quietly or forces a job to run, and the image build has no condition."""
    for path in WORKFLOWS.glob("*.y*ml"):
        text = path.read_text(encoding="utf-8")
        assert "continue-on-error" not in text, path.name
        assert "always()" not in text, path.name
    build = re.search(r"^  build-and-push-image:\n(?P<body>(?:    .*\n|\n)*)", _read("CD.yml"), re.MULTILINE)
    assert build
    assert not re.search(r"^    if:", build["body"], re.MULTILINE)


def test_every_checkout_drops_its_credentials() -> None:
    """Each actions/checkout step sets `persist-credentials: false`."""
    checkouts = [step for name in ("ci.yml", "CD.yml") for step in _steps(name) if "uses: actions/checkout@" in step]
    assert checkouts
    for step in checkouts:
        assert re.search(r"^\s+persist-credentials: false$", step, re.MULTILINE), step


def test_permissions_stay_at_least_privilege() -> None:
    """CD.yml starts with no permissions and ci.yml grants `contents: read` and nothing else."""
    assert re.search(r"^permissions: \{\}$", _read("CD.yml"), re.MULTILINE)
    ci = _read("ci.yml")
    assert re.search(r"^permissions:\n  contents: read\n(?=\n|\S)", ci, re.MULTILINE)
    assert not re.search(r":\s*write\b", ci)


def test_each_job_gets_only_the_permissions_it_needs() -> None:
    """CD.yml has four jobs; only the release job writes to the repository, only the build writes packages."""
    jobs = re.findall(r"^  ([\w-]+):$", _read("CD.yml").split("\njobs:\n", 1)[1], re.MULTILINE)
    assert jobs == ["check", "release", "check-release-pr", "build-and-push-image"]
    assert _permissions("check") == {"contents: read"}
    assert _permissions("release") == {"contents: write", "pull-requests: write"}
    assert _permissions("check-release-pr") == {"actions: write"}
    assert _permissions("build-and-push-image") == {"contents: read", "packages: write"}


def test_the_release_pull_request_gets_the_check() -> None:
    """ci.yml can be started by hand, and CD.yml starts it on the release branch whenever release-please updates it."""
    assert re.search(r"^on:\n(?:  .*\n)*  workflow_dispatch:$", _read("ci.yml"), re.MULTILINE)
    job = _job("check-release-pr")
    assert "if: ${{ needs.release.outputs.pr != '' }}" in job
    assert "release-please--branches--*) ;;" in job
    assert 'gh workflow run ci.yml --repo "$GITHUB_REPOSITORY" --ref "$branch"' in job
