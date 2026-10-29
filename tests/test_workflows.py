"""The workflows pin every action and build the image only after the checks."""

from pathlib import Path
import re

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+(\.\d+){0,2}$")


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
    """The image workflow calls the check workflow and its build job needs it."""
    text = (WORKFLOWS / "CD.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/ci.yml" in text
    assert re.search(
        r"^  build-and-push-image:\n(?:    .*\n)*?    needs: check$",
        text,
        re.MULTILINE,
    )
