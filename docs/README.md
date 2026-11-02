# Documentation

Each guide is for one reader. The project's front page is the [README](../README.md).

| Guide | For | It covers |
| --- | --- | --- |
| [User guide](USERGUIDE.md) | Members and staff | Every command by domain, and who can run each one |
| [Operator guide](OPERATIONS.md) | Whoever hosts an instance | Settings, adding and removing servers, Developer Portal, roles, staff commands in the slash menu, redeploying and rolling back, the move to the database, troubleshooting |
| [Deployment and releases](DEPLOYMENT.md) | Whoever deploys or cuts a release | The hardened production image, image tags, making a release, repository settings, moving a host to the non-root image |
| [Contributing](../CONTRIBUTING.md) | Contributors | Local setup, checks, screen previews, stored data changes, pull requests, building the code documentation |

`docs/index.html` and the `docs/fablabot*` folder are the code documentation. `uv run doc_builder` generates them and git ignores them: see [Build the technical documentation](../CONTRIBUTING.md#build-the-technical-documentation).
