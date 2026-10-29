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

#### Slash commands

At start, the bot sends its slash commands to Discord only when they changed since the last sync. It keeps a fingerprint of the last sync in `data/command_sync_state.json`.

- To force a sync, stop the bot, delete that file and start it again.
- After rolling back to an older image, delete that file before you upgrade again: the older image syncs at every start without updating it.

`DEV_GUILD_ID` holds one server ID, for a development bot only. When it is set, the bot syncs its commands to that server alone, where they show at once, and leaves the global commands as they are.

```bash
DEV_GUILD_ID=111111111111111111
```

- Use a separate Discord application for the development bot, one that never synced without `DEV_GUILD_ID`: otherwise each command shows twice on that server.
- Never set it on the production bot: its commands would stop updating on every other server.
- The server must be in `ALLOWED_GUILD_IDS` when that list is set. A malformed value, or a server outside the list, stops the bot at start.

### Running the project

To run the code, you can then use:

```bash
DISCORD_TOKEN_FILE=secrets/discord_token.secret ALLOWED_GUILD_IDS=<your server ID> uv run main
```

## Deployment

The production image runs the bot as the unprivileged user `10001:10001`, from start to stop. It writes only to `data/` and `logs/` (the two volumes) and to a heartbeat file in `/tmp`. `compose.yml` is the reference stack; on top of the image, it takes away what the bot does not need:

```yaml
    read_only: true
    tmpfs:
      - /tmp:size=1m,mode=1777,noexec,nosuid,nodev
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
```

- `read_only` mounts the image read-only; `/tmp` is a 1 MB in-memory folder, for the heartbeat only.
- `cap_drop` removes every Linux capability, and `no-new-privileges` stops any process from gaining rights.

At start, before connecting, the bot checks that it can read and write every folder and file under `data/` and `logs/`, and read the token file. Otherwise it stops with one line per problem, for example `Cannot start: data/ is not writable by uid 10001.`, and Docker restarts it until the problem is fixed. It writes and deletes nothing meanwhile.

The container reports `healthy` while the bot is connected to Discord, and `unhealthy` when its last heartbeat is more than 90 seconds old. It is a status only: nothing restarts the bot on its own.

To check an image locally (needs Docker): `sh docker/check-image.sh`. It builds the image with planted secrets, then checks its user, its content, the start messages, and that nothing is written outside `data/`, `logs/` and `/tmp`.

### Moving a host to the non-root image

Volumes and the token file written by an older image belong to root. Do this once, on the host, before the first redeploy on the non-root image:

1. Stop the stack.
2. Archive `data/` and `logs/` into a private folder and compare the file counts. Never delete either folder.

   ```bash
   VOLUMES_PATH=/path/to/volumes  # the value the stack uses
   STAMP=$(date +%Y%m%d-%H%M)
   sudo mkdir -p "$VOLUMES_PATH/fablabot/archive" && sudo chmod 700 "$VOLUMES_PATH/fablabot/archive"
   for dir in data logs; do
     sudo tar -czf "$VOLUMES_PATH/fablabot/archive/$dir-$STAMP.tar.gz" -C "$VOLUMES_PATH/fablabot" "$dir"
     sudo chmod 600 "$VOLUMES_PATH/fablabot/archive/$dir-$STAMP.tar.gz"
     echo "$dir: $(sudo tar -tzf "$VOLUMES_PATH/fablabot/archive/$dir-$STAMP.tar.gz" | grep -vc '/$') archived, $(sudo find "$VOLUMES_PATH/fablabot/$dir" -type f | wc -l) on disk"
   done
   ```

3. Give both folders to the bot's user. Leave `archive/` as it is.

   ```bash
   sudo chown -R 10001:10001 "$VOLUMES_PATH/fablabot/data" "$VOLUMES_PATH/fablabot/logs"
   ```

4. Let the bot's user read the token file, the one named under `secrets: discord_token: file:` in the stack.

   ```bash
   sudo chown 10001:10001 /path/to/discord_token.secret && sudo chmod 400 /path/to/discord_token.secret
   ```

5. Add the seven lines above to the bot's service in the stack.
6. Redeploy the stack.
7. Check the start. The first command prints `0`. The second prints `starting`, then `healthy` within two minutes.

   ```bash
   docker logs fablabot-prod 2>&1 | grep -c 'Cannot start'
   docker inspect --format '{{.State.Health.Status}}' fablabot-prod
   ```

   Use your stack's container name if it is not `fablabot-prod`. A `Cannot start:` line names the folder, file or setting to fix; once fixed, the bot starts at its next restart.

To roll back, set the image back to the previous `:<commit sha>` tag and restore the stack as it was before step 5. The older image runs as root and reads the volumes without a `chown` back, but root without capabilities cannot write files owned by 10001: the seven lines must go with it.

After restoring an archive, run step 3 again: `tar` run as root restores the files as root.

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
