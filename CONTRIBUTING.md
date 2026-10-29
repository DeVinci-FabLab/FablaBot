# Contributing

Every pull request runs one check, called `check`. It covers formatting, lint, types, tests and the lockfile.
The image is built only when that check passes on `main`.
This page shows how to run the same commands locally.

## Set up

Install [uv](https://docs.astral.sh/uv/) (0.13.6 or later), then run:

```bash
uv sync --locked
```

## Run the checks

```bash
uv sync --locked
uv run --group dev ruff format --check src tests
uv run --group dev ruff check src tests
uv run --group dev basedpyright
git diff --exit-code -- .basedpyright/baseline.json
uv run --group dev pytest -q
uv lock --check
```

CI runs the same lines.

## Fix what they report

```bash
uv run --group dev ruff format src tests
uv run --group dev ruff check --fix src tests
```

## Type errors and the baseline

`.basedpyright/baseline.json` records the type errors that predate the check. A new error fails it. Fix it, or add `# pyright: ignore[<rule>]` followed by a short reason.

When a fix removes a recorded error, the check rewrites the baseline. Commit that change with the fix, or CI fails.

Do not run `--writebaseline` to hide a new error. It is only for a basedpyright upgrade that reports errors in code nobody touched, and the pull request says so.

## Pull requests

- Titles follow Conventional Commits, for example `fix(log): ...`, since the title becomes the squash commit.
- One maintainer approval is needed to merge.
- The title also decides the next version: `feat:` bumps the minor number; `fix:`, `perf:`, `deps:`, `revert:` and `docs:` bump the patch number and appear in the changelog. `build:`, `ci:`, `chore:`, `test:`, `refactor:` and `style:` do not start a release on their own.
- A push to `main` runs the check, then builds and publishes `ghcr.io/devinci-fablab/fablabot:edge` and `:sha-<commit>`. A red check publishes nothing.
- `:latest` moves only when the release pull request is merged; the README's Releases section explains the steps.
- Publishing does not restart a running bot. The deployment is updated by hand.

## Dependency updates

Dependabot opens one grouped pull request per week for Python packages, GitHub Actions and Docker images, for releases at least 7 days old. They are reviewed and merged like any other, never automatically.

Actions are pinned by full commit SHA with the version in a comment (`@<sha> # v7.0.1`). When updating one by hand, change both.

After editing `pyproject.toml`, run `uv lock` and commit `uv.lock`.
