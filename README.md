# FablaBot

> General purpose Discord bot for the DeVinci Fablab discord server.

![Banner](./assets/banner.png)

## Project setup

This library is managed with [`uv`](https://docs.astral.sh/uv/), a fast Python package and project manager written in Rust.

Start by [installing `uv`](https://docs.astral.sh/uv/getting-started/installation/).

While using `uv` is not mandatory, it is the simplest way to get started with this project. If you know what you're doing feel free to use any other tool.

### Installing dependencies

To create a Virtual Environment for the project and install the dependencies, you can simply use:

```bash
uv sync
```

### Environment variables

You will need to create a `secrets/` folder into the project root directory and add one file:

- `secrets/discord_token.secret`

Here are some commands to help you create it:

```bash
mkdir secrets
```

```bash
# Token for authenticating with the Discord API (https://discord.com/developers/applications)
echo -n 'my_discord_token' > secrets/discord_token.secret
```

#### Allowed servers

`ALLOWED_GUILD_IDS` holds the IDs of the servers the bot may serve, separated by commas:

```bash
ALLOWED_GUILD_IDS=111111111111111111,222222222222222222
```

- When it is empty, the bot keeps serving the servers it is in at start and logs a warning. After that, it only stays in a server it joins if the application owner, or an admin or developer of its team, owns it.
- A malformed value stops the bot at start.

To keep other people from adding the bot to their servers, set these in the [Developer Portal](https://discord.com/developers/applications):

1. Installation: set Install Link to None and uncheck User Install.
2. Bot: turn Public Bot off.

To copy a server ID, enable Developer Mode in Discord (User Settings, Advanced), then right-click the server and choose Copy Server ID.

#### Log channels

Each server picks its log channel with `/log set` and receives its own errors only.

`OPERATOR_LOG_CHANNEL_ID` holds one channel ID and receives the errors that belong to no server. Pick a channel only the bot's administrators can read: those errors come from every server's tasks.

```bash
OPERATOR_LOG_CHANNEL_ID=888888888888888888
```

- When it is empty, those errors stay in the log files.
- A malformed value is ignored, with a warning at start.

Log files live in `logs/`:

- `logs/<date>.log` holds every line, stays on the host and is never sent.
- `logs/guilds/<server ID>/<date>.log` holds one server's lines. `/log export` sends that file to that server only.
- The bot deletes no log file.

### Running the project

To run the code, you can then use:

```bash
DISCORD_TOKEN_FILE=secrets/discord_token.secret ALLOWED_GUILD_IDS=<your server ID> uv run main
```

## Documentation

We **highly recommend** you to read the user guide before using the bot. It will help you understand the bot's features
and how to use them. You can find the user guide [here](./docs/user-guide.md).

### Building the technical documentation

To build the documentation of a version of this project, you can use the provided `doc_builder` utility.

```bash
uv run doc_builder
```

You can then open the `docs/index.html` file in your browser to view the codebase's documentation.

Add the `--help` flag to this command to see available options.

## Releases

Each release has a version `vX.Y.Z`, a GitHub release and an entry in `CHANGELOG.md`. The version is set in `pyproject.toml`; each release copies it to `uv.lock` and `.release-please-manifest.json`, and a test checks that the three agree. Images are published to `ghcr.io/devinci-fablab/fablabot`:

| Tag | Moves when | Use it for |
| --- | --- | --- |
| `:latest`, `:X.Y.Z`, `:X.Y` | a release is published | production |
| `:edge`, `:sha-<commit>` | every push to `main`, once the check passes | a test server, or trying one commit |

Images published before the first release keep their `:<commit sha>` tags.

### Making a release

1. Every push to `main` updates one pull request titled `chore(main): release X.Y.Z`. It holds the next version and its changelog, worked out from the commit titles on `main`: `fix:` bumps Z, `feat:` bumps Y, and below 1.0.0 a breaking change (`feat!:`) bumps Y as well.
2. The check runs on that pull request too. Review it and merge it like any other.
3. The merge tags `vX.Y.Z`, publishes the GitHub release, and builds the image under `:latest`, `:X.Y.Z`, `:X.Y`, `:edge` and `:sha-<commit>`.
4. Publishing does not restart a running bot. Redeploy the stack (pull the image, recreate the container) to run the release.

If the image build of a release fails, the tag exists but `:latest` has not moved. Open that run in the Actions tab and choose "Re-run failed jobs": re-running all jobs would publish `:edge` only.

### Rolling back

Set the stack's image to the previous release, `ghcr.io/devinci-fablab/fablabot:X.Y.Z`, or to one commit with `:sha-<commit>`, and redeploy. Read the changelog entries in between first: a release that changed the files in `data/` says so there. Go back to `:latest` once a fixed release is out.

### Repository settings

Releases need two settings, once, by a repository admin:

- Settings, Actions, General, Workflow permissions: tick "Allow GitHub Actions to create and approve pull requests", so the release pull request can be opened. No workflow approves pull requests. Each job asks for its own permissions, so the default should be "Read repository contents and packages permissions".
- Settings, General, Pull Requests, Allow squash merging: set the default commit message to "Pull request title", since the release reads the squash commit titles.

A pull request opened by a workflow does not start other workflows, so the release workflow starts the check on the release pull request itself. A required `check` status is met without a personal token.

## Roadmap

The library is still in active development. The next feature and bug resolutions are listed in
the [Project](https://github.com/orgs/DeVinci-FabLab/projects/5/views/2) section of the GitHub repository.

## Security Policy

Consider reading our [SECURITY](https://github.com/MorganKryze/ConsoleAppVisuals/blob/main/.github/SECURITY.md) policy
to know more about how we handle security issues and how to report them. You will also find the stable versions of the
project.

## Acknowledgments

Consider reading
the [ACKNOWLEDGMENTS](https://github.com/MorganKryze/ConsoleAppVisuals/blob/main/.github/ACKNOWLEDGMENTS.md) file. It's
a testament to the collaborative effort that has gone into improving and refining our library. We're deeply grateful to
all our contributors for their invaluable input and the significant difference they've made to the project.

It also lists the open source projects that have been used to build this library until now.

## Contributing

Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any
contributions you make are **greatly appreciated**. To do so, follow the steps described in
the [CONTRIBUTING](https://github.com/MorganKryze/ConsoleAppVisuals/blob/main/.github/CONTRIBUTING.md) file.

We are always open for feedback and discussions. If you are using our library and want to share your use case, or if you
have any suggestions for improvement, please feel free
to [open an issue](https://github.com/MorganKryze/ConsoleAppVisuals/issues)
or [open a discussion](https://github.com/MorganKryze/ConsoleAppVisuals/discussions) on our GitHub repository. Your
input helps us understand possible use cases and make necessary improvements.

Do not hesitate to **star** and **share** the project if you like it!

## License

Distributed under the MIT License. See [LICENSE](./LICENSE) for more information.
