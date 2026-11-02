# Contributing

Every pull request runs one check, called `check`. It covers formatting, lint, types, tests and the lockfile.
The image is built only when that check passes on `main`.
This page shows how to run the same commands locally.

- [Set up](#set-up)
- [Run the bot locally](#run-the-bot-locally)
- [Run the checks](#run-the-checks)
- [Fix what they report](#fix-what-they-report)
- [Preview a screen as an image](#preview-a-screen-as-an-image)
- [Type errors and the baseline](#type-errors-and-the-baseline)
- [Change the stored data](#change-the-stored-data)
- [Build the technical documentation](#build-the-technical-documentation)
- [Pull requests](#pull-requests)
- [Dependency updates](#dependency-updates)

## Set up

The project is managed with [uv](https://docs.astral.sh/uv/), a Python package and project manager. [Install it](https://docs.astral.sh/uv/getting-started/installation/) (0.13.6 or later), then create the virtual environment and install the dependencies:

```bash
uv sync --locked
```

Using uv is not mandatory. If you know what you are doing, any other tool works.

## Run the bot locally

Create a `secrets/` folder in the project root and put the Discord token in it. The token comes from the [Developer Portal](https://discord.com/developers/applications):

```bash
mkdir secrets
echo -n 'my_discord_token' > secrets/discord_token.secret
```

Then start the bot on your own server. `ALLOWED_GUILD_IDS` takes its ID (see "Allowed servers" in the [operator guide](docs/OPERATIONS.md#allowed-servers)):

```bash
DISCORD_TOKEN_FILE=secrets/discord_token.secret ALLOWED_GUILD_IDS=<your server ID> uv run main
```

Use a separate Discord application for a development bot, and set `DEV_GUILD_ID` so its commands show at once on your server only: see ["Development bot"](docs/OPERATIONS.md#development-bot). The other settings are in the [operator guide](docs/OPERATIONS.md#environment-variables).

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

`tests/test_journeys.py` drives the real bot through SimCord, a simulated Discord with no network and no token: commands, buttons, menus, forms, reactions and permission checks. Add a journey there when a change touches what a member does on Discord. SimCord always reports the `en-US` locale and does not draw the Discord client, so language and display still need a look on a test server.

SimCord reads private parts of discord.py and stops the whole test run if they move. If a discord.py security release breaks it, the fix pull request removes `simcord`, `tests/test_journeys.py` and `tests/test_server_check.py`; they come back once SimCord catches up.

## Fix what they report

```bash
uv run --group dev ruff format src tests
uv run --group dev ruff check --fix src tests
```

## Preview a screen as an image

A screen the bot sends can be rendered as a Discord-like image before it ships.
The screens run the bot's real command or view code with sample data, so a change to the bot shows in its image.
The browser comes from the `mockups` group, which the bot, CI and the Docker image never install.

Install the browser once, about 100 MB:

```bash
uv run --group mockups playwright install chromium --only-shell
```

Render every screen, or only the ones you name, to dark and light PNG at 390 px (phone) and 900 px (desktop), so 4 PNG per screen:

```bash
uv run --group mockups python -m tests.mockups --out <folder> [screen ...]
```

`--html-only` skips the browser and `--width 500` replaces both widths.

To add a screen, write a function in `tests/mockups/screens.py` that runs the real command or view, and register it in `SCREENS`.
Then write its snapshot and read the diff before you commit it:

```bash
MOCKUP_SNAPSHOTS=update uv run --group dev pytest -q tests/test_mockups.py
```

To sketch a screen no code produces yet, write a JSON file and pass it with `--payload`.
`names` maps a mention's id to its name, and each `sent` item is a Discord API `message` or `modal`:

```json
{"names": {"42": "Camille"}, "sent": [{"message": {"content": "Bonjour <@42>", "ephemeral": true}}]}
```

To capture one HTML page at one width, run `uv run --group mockups python -m tests.mockups --shot page.html out.png 390`.

Limits to keep in mind:

- The font is the system one, so line wrapping is approximate.
- Open text menus are drawn as a list, which Discord shows only on click.
- A modal is centred on a phone, where Discord fills the screen.
- CI checks the snapshots, not the images.

## Type errors and the baseline

`.basedpyright/baseline.json` records the type errors that predate the check. A new error fails it. Fix it, or add `# pyright: ignore[<rule>]` followed by a short reason.

When a fix removes a recorded error, the check rewrites the baseline. Commit that change with the fix, or CI fails.

Do not run `--writebaseline` to hide a new error. It is only for a basedpyright upgrade that reports errors in code nobody touched, and the pull request says so.

## Change the stored data

The schema lives in `src/fablabot/migrations/`, one numbered SQL file per step, applied in order at start. To change it, add
the next number. Never edit a file that has shipped: databases in production already ran it. A file may rebuild a table (create the new one, copy, drop the old one, rename, recreate its indexes): foreign keys are off while it runs, and the start refuses the file if it leaves a row without its parent. A local `data/` holding JSON
state from an older release stops the bot at start, as it does in production.

## Build the technical documentation

To build the documentation of the code, use the provided `doc_builder` utility:

```bash
uv run doc_builder
```

Open `docs/index.html` in your browser to read it. The generated files are not committed. Add the `--help` flag to see the options.

## Pull requests

- Titles follow Conventional Commits, for example `fix(log): ...`, since the title becomes the squash commit.
- One maintainer approval is needed to merge.
- The title also decides the next version: `feat:` bumps the minor number; `fix:`, `perf:`, `deps:`, `revert:` and `docs:` bump the patch number and appear in the changelog. `build:`, `ci:`, `chore:`, `test:`, `refactor:` and `style:` do not start a release on their own.
- A push to `main` runs the check, then builds and publishes `ghcr.io/devinci-fablab/fablabot:edge` and `:sha-<commit>`. A red check publishes nothing.
- `:latest` moves only when the release pull request is merged; the [deployment guide](docs/DEPLOYMENT.md#making-a-release) explains the steps.
- Publishing does not restart a running bot. The deployment is updated by hand.

## Dependency updates

Dependabot opens one grouped pull request per week for Python packages, GitHub Actions and Docker images, for releases at least 7 days old. They are reviewed and merged like any other, never automatically.

Actions are pinned by full commit SHA with the version in a comment (`@<sha> # v7.0.1`). When updating one by hand, change both.

After editing `pyproject.toml`, run `uv lock` and commit `uv.lock`.
