# FablaBot

> A Discord bot that runs a makerspace community: trainings with sign-ups, role management, anonymous suggestions and bot messages, for several servers from one instance.

[![check](https://github.com/DeVinci-FabLab/FablaBot/actions/workflows/ci.yml/badge.svg)](https://github.com/DeVinci-FabLab/FablaBot/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/DeVinci-FabLab/FablaBot)](https://github.com/DeVinci-FabLab/FablaBot/releases)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue)](./.python-version)
[![discord.py](https://img.shields.io/badge/discord.py-2.7-5865F2)](https://github.com/Rapptz/discord.py)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

FablaBot serves the [DeVinci FabLab](https://github.com/DeVinci-FabLab) association on Discord. Its code is open. Another community can host it today if its server uses the same [role names](./docs/OPERATIONS.md#roles); per-server settings are planned.

## What it does

- **Trainings:** draft, publish, sign up by reaction, waitlist, reminders and exports.
- **Help for each member:** `/help` lists what your roles open, in English, French or German.
- **Anonymous suggestions:** `/suggest` reaches the right team, and an anonymous one leaves no trace of its author.
- **Roles and temporary admin:** rules per role, with a rank check and rights that end on time.
- **Channels and bot messages:** create and manage channels, publish messages, and link reactions to actions.
- **Per-server logs:** each server has its own log channel and exports.
- **Built to run unattended:** one SQLite file, a non-root hardened container, and a report at each start on what each server lacks.

## Where to go next

| You are | Read |
| --- | --- |
| A member or staff | The [user guide](./docs/USERGUIDE.md), or run `/help` in Discord |
| Hosting an instance | The [operator guide](./docs/OPERATIONS.md): settings, servers, roles, staff commands in the slash menu, troubleshooting. Then [deployment and releases](./docs/DEPLOYMENT.md) |
| A contributor | [CONTRIBUTING](./CONTRIBUTING.md): setup, checks, pull requests, the code documentation |

All the guides are listed in [docs/](./docs/README.md).

## Quick start

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/), then, with a Discord token in `secrets/discord_token.secret`:

```bash
uv sync
DISCORD_TOKEN_FILE=secrets/discord_token.secret ALLOWED_GUILD_IDS=<your server ID> uv run main
```

[CONTRIBUTING](./CONTRIBUTING.md#run-the-bot-locally) has the setup in full.

## Roadmap

The next features and fixes are listed in the [Project](https://github.com/orgs/DeVinci-FabLab/projects/5/views/2) section of the GitHub repository.

## Security Policy

Please do not report security issues in a public issue. Use GitHub's private [vulnerability reporting](https://github.com/DeVinci-FabLab/FablaBot/security/advisories/new) instead, and we will answer there.

## Contributing

Follow the steps in [CONTRIBUTING](./CONTRIBUTING.md). Questions, bugs and ideas are welcome as [issues](https://github.com/DeVinci-FabLab/FablaBot/issues). Thanks to everyone on the [contributors](https://github.com/DeVinci-FabLab/FablaBot/graphs/contributors) page.

## License

Distributed under the MIT License. See [LICENSE](./LICENSE) for more information.
