# Operator guide

This guide is for whoever hosts and runs a FablaBot instance. It covers deploying the image,
adding and removing servers, the Discord Developer Portal settings, the roles the bot expects, and
the problems seen in production. Commands for members and staff are in the [user guide](USERGUIDE.md).

## Deployment model

Every push to `main` publishes `:edge` and `:sha-<commit>`. `:latest`, `:X.Y.Z` and `:X.Y` move only
when the release pull request is merged (see the [README](../README.md#releases), "Releases").
Production follows `:latest`; a test server can follow `:edge`. A merge is not a deploy: the host
must redeploy the stack (pull the image, recreate the container) to run the new version. In Komodo,
the production container is named `fablabot`.

Example `compose.yml` (the repository's `compose.yml` is the reference):

```yaml
services:
  python-prod:
    image: ghcr.io/devinci-fablab/fablabot:latest
    container_name: fablabot-prod
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN_FILE=/run/secrets/discord_token
      - ALLOWED_GUILD_IDS=${ALLOWED_GUILD_IDS:-}
      - OPERATOR_LOG_CHANNEL_ID=${OPERATOR_LOG_CHANNEL_ID:-}
    secrets:
      - discord_token
    volumes:
      - ${VOLUMES_PATH}/fablabot/data:/app/data
      - ${VOLUMES_PATH}/fablabot/logs:/app/logs

secrets:
  discord_token:
    file: ./secrets/discord_token.secret
```

| Setting | Role |
| --- | --- |
| `DISCORD_TOKEN_FILE` | Path of the file holding the bot token (`src/fablabot/main.py:32`). |
| `ALLOWED_GUILD_IDS` | Server IDs the bot may serve, comma-separated, no quotes, no spaces. |
| `OPERATOR_LOG_CHANNEL_ID` | Optional. Channel receiving errors that belong to no server. Pick a channel only the bot's administrators can read: those errors come from every server's tasks. |
| `DEV_GUILD_ID` | Optional, development bot only. Syncs the commands to this one server and leaves global commands alone. Never set on the production bot. |
| `data/` volume | Per-server settings, the temporary-admin expiries (`data/temp_admin_state.json`) and the fingerprint of the last command sync (`data/command_sync_state.json`; delete it to force a sync). |
| `logs/` volume | `logs/<date>.log` (every line, never sent) and `logs/guilds/<server ID>/<date>.log` (one server's lines). |

### Before every redeploy

Archive `logs/` and `data/` first.

1. Create a private archive folder outside the volumes and restrict it:

   ```bash
   mkdir -p ~/fablabot-archive && chmod 700 ~/fablabot-archive
   ```

2. Archive both folders and restrict the file:

   ```bash
   STAMP=$(date +%Y%m%d-%H%M%S)
   tar -czf ~/fablabot-archive/fablabot-$STAMP.tar.gz -C "$VOLUMES_PATH/fablabot" data logs
   chmod 600 ~/fablabot-archive/fablabot-$STAMP.tar.gz
   ```

3. Check the file counts match:

   ```bash
   tar -tzf ~/fablabot-archive/fablabot-$STAMP.tar.gz | grep -vc '/$'
   find "$VOLUMES_PATH/fablabot/data" "$VOLUMES_PATH/fablabot/logs" -type f | wc -l
   ```

4. Redeploy the stack.

Never purge logs. Since the per-server log change, the bot deletes no log file. How long to keep them
is a decision for the association (GDPR), not for the bot.

### Releasing and redeploying

1. Merge the release pull request `chore(main): release X.Y.Z`.
2. Wait for the "Build and Push Docker Image" run to finish green: `:latest` is then `:X.Y.Z`.
3. Archive `logs/` and `data/`, as in "Before every redeploy".
4. Redeploy the stack in Komodo.
5. Check the start in the log, as usual.

The production stack stays on `:latest`; a test server can follow `:edge`.

### Rolling back

Set the stack's image to the `:X.Y.Z` of the previous release, or to `:sha-<commit>`. Images from before
the first release use `:<commit sha>`. Read the changelog entries in between for changes to `data/`,
then redeploy. Go back to `:latest` once a fixed release is out.

After a rollback, delete `data/command_sync_state.json` before upgrading again (see "This command is
outdated"). The [README](../README.md#rolling-back) has the same steps under "Rolling back".

## Find a server ID

1. In Discord, open User Settings, Advanced, and enable Developer Mode.
2. Right-click the server icon and choose Copy Server ID.

## Add a server

Order matters: the allow-list comes before the invite, otherwise the bot joins and leaves at once.

1. Add the server ID to `ALLOWED_GUILD_IDS` and redeploy.

   ```bash
   ALLOWED_GUILD_IDS=111111111111111111,222222222222222222
   ```

2. Only then invite the bot with this link, after replacing the two placeholders:

   ```text
   https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot+applications.commands&permissions=268561616&guild_id=<SERVER_ID>&disable_guild_select=true
   ```

   The permission integer `268561616` grants: Manage Roles, Manage Channels, View Audit Log, View
   Channels, Send Messages, Manage Messages, Embed Links, Attach Files, Read Message History and Add
   Reactions. It does not include Administrator.

3. In Server Settings, Roles, drag the bot's role above every role it manages, `Admin -temp-`
   included. Discord refuses to add or remove a role that sits above the bot's own.
4. In the commands channel (see below), run `/log set` and pick the channel that receives this
   server's errors.
5. In Server Settings, Integrations, show each staff group to the roles that use it, as in the
   [README](../README.md#staff-commands-in-the-slash-menu), "Staff commands in the slash menu". Until
   then, only members with the Administrator permission see the staff commands.

At the next start, the log shows one summary line:

```text
Guild allow-list: 2 IDs listed, in 3 guilds, leaving 1.
```

and one line per server the bot leaves:

```text
Leaving guild: not_allowed (guild_id=333333333333333333)
```

Two other cases are logged on purpose. When `ALLOWED_GUILD_IDS` is empty, the bot keeps the servers it is
already in at start and logs a warning, but leaves any server that invites it later unless the
application owner owns it (`Leaving guild: owner_not_proven`); when it is set but matches none of the current servers, the bot leaves none
and logs an error ("check the IDs"). A malformed value stops the bot at start.

## Remove a server

1. Remove its ID from `ALLOWED_GUILD_IDS`.
2. Redeploy. The bot leaves at start.

Its `logs/guilds/<server ID>/` folder stays on the host.

## Discord Developer Portal

Open <https://discord.com/developers/applications>, pick the application, and set:

| Page | Setting | Why |
| --- | --- | --- |
| Installation | User Install unchecked | The bot is not meant to run as a user app, outside any allow-listed server. |
| Installation | Install Link: None | Without it, Discord offers no public link to add the bot. |
| Bot | Public Bot off | Only the application owner can invite the bot, so nobody else can add it to their server. |

The allow-list still decides which servers are served; these settings keep strangers from getting that far.

## Roles

The bot looks roles up by name, and the names are fixed today in `src/fablabot/helpers/constants.py`
(`RoleNames`, the full list). A server needs roles with these exact names:

| Role | Used for |
| --- | --- |
| `CoDir` | Required: `/user op` refuses with "Rôles administratifs manquants sur le serveur." without it; also used by the welcome, channel and training flows |
| `Admin -temp-` | Required by `/user op` (see Temporary admin) |
| `Administrateur`, `Président`, `Vice-Président`, `Secrétaire Général` | Admin roles (`ADMIN_ROLES`, same file) |
| `Bureau`, `Sbire Bureau` | Bureau permissions (messages, `/user` with roles); `Bureau` members can hand out `Sbire Bureau` and `agent MyFab` |
| `Pôle Numérique` | Allowed to use `/log` with the admin roles and `Respo Numérique` |
| `Respo Communication`, `Respo Event`, `Respo Formation`, `Respo Numérique`, `Respo Partenariat`, `Respo Projet` | Area managers, who receive the matching suggestions and trainings |
| `agent MyFab`, `Membre ✓` | MyFab agents; verified members (welcome flow) |

Commands that need an admin accept `Administrateur`, `Président`, `Vice-Président` and
`Secrétaire Général`; `Admin -temp-` is left out on purpose. Per-server role names are planned and not
available yet.

The slash menu shows each staff group only to server administrators and to the roles allowed in
Integrations (README, "Staff commands in the slash menu"). The menu is a display setting; the bot
checks the roles above when a command runs.

## Commands channel

Most commands only run in one text channel per server. `/suggest` and `/msg purge` work outside it, `/msg clear`
refuses it, and `/welcome approve` runs in a `welcome-…` channel.

1. The bot reads the channel from the server's stored settings.
2. If none is stored, it uses the text channel named `commandes_bot` (`helpers/utils.py:32`) and stores it.
3. Elsewhere, the bot answers "Vous ne pouvez pas utiliser de commandes en dehors du salon ...", and
   with no such channel, "Le salon de commandes du bot n'est pas configuré sur ce serveur."

So on a new server, create a text channel named `commandes_bot`.

`/help` is open to every member and runs in this channel, so members need View Channel and
Use Application Commands on it. Without them, they get a refusal that points at a channel they cannot open.

`/log set` picks the channel that receives the server's errors. `/log export` sends that server's log
file to that server only.

## Server language

The help answers in the language the server declares to Discord: `fr`, `de`, else English. Discord lets a
server set its language only in Community mode: Server Settings > Enable Community, then Server Settings >
Community > Overview > Server Primary Language. A server outside Community mode reports `en-US`, so its help
is in English.

After the deploy, check it on the test server, where `/help` should answer in French.

A per-server language setting in the bot is planned and not available yet. The texts live in
`src/fablabot/locales/<language>.toml`; adding a language means adding one file with the keys of `en.toml`.

## Temporary admin

`/user op` gives the `Admin -temp-` role for a limited time (5 minutes by default); `/user deop` removes it
early. The expiry is recorded in `data/temp_admin_state.json`.

At every start, the bot checks each holder of `Admin -temp-` (`cogs/user_management.py`, `_reconcile_guild`):

- A holder with a future expiry gets a timer again.
- A holder with no recorded expiry, or one that has passed, loses the role, and the log says
  `Removed temporary admin role at start (no recorded expiry) guild_id=... member_id=...`.
- If Discord refuses the removal with a 403 (Missing Permissions), the bot's role sits below
  `Admin -temp-`. The log shows `Failed to remove temporary admin role at start`; move the bot's role above
  it (step 3 of "Add a server") and restart.

## "This command is outdated"

Commands are synced to Discord only when they changed since the last sync
(`data/command_sync_state.json`); a redeploy without command changes sends nothing. After a change,
clients still holding the old list show this message: reload with Ctrl+R (Windows, Linux) or Cmd+R
(macOS). The same reload makes the menu show or hide a staff group after its Integrations settings
change.

To force a sync: stop the stack, delete `data/command_sync_state.json`, start it. Do it after rolling
back to an older image, before upgrading again. The file only records the last sync; it holds no
member data.

The first start after the `/help` release syncs once, because the command list changed. Members reload
Discord to see `/help` (or `/aide`, `/hilfe`).

The log tells which case applies: `Commands synced (9, global).` after a sync, and
`Commands unchanged, sync skipped (global).` when nothing changed.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| The bot joins a new server and leaves at once | Its ID is not in `ALLOWED_GUILD_IDS`, or the host was not redeployed | Add the ID, redeploy, then invite again. |
| The bot does not leave a removed server | The change was not deployed, or the list now matches none of the current servers | Redeploy; read the "Guild allow-list" line, and check the IDs. |
| Bot stops at start with "entry N is not a numeric guild ID" | `ALLOWED_GUILD_IDS` has quotes, spaces or a non-numeric entry | Use `id1,id2`, digits only. |
| A merged fix is not live | A merge does not deploy | Archive, then redeploy the stack. |
| "Vous ne pouvez pas utiliser de commandes en dehors du salon ..." | The command ran outside the commands channel | Use the channel named in the message. |
| "Le salon de commandes du bot n'est pas configuré sur ce serveur." | No stored channel and no `commandes_bot` channel | Create a text channel named `commandes_bot`. |
| A `/user op` role is not added or removed (403) | The bot's role is below `Admin -temp-` | Move the bot's role above it. |
| `Missing roles ... for guild ...` in the log | The server lacks a role name the bot expects | Create the role with the exact name (see Roles). |
| "This command is outdated" | The client holds an old command list | Reload Discord (Ctrl/Cmd+R). |
| No errors reach the operator channel | `OPERATOR_LOG_CHANNEL_ID` is empty or malformed (warning at start) | Set a valid channel ID; they stay in `logs/<date>.log` meanwhile. |
| A staff member no longer sees their commands | Their role is not allowed on that command in Integrations, or their client holds the old list | Add the role as in the README table, then reload Discord. |
| A member sees a staff group | `@everyone` is allowed on that command in Integrations, or a role they hold was added at the top of the page | Remove that entry; leave the Roles & Members section at the top alone. |
| A changed command does not show after a redeploy | The record matches while Discord holds other commands (another instance with the same token, or a rollback) | Delete `data/command_sync_state.json` and restart. |
| Bot stops at start with `DEV_GUILD_ID: ...` | Malformed ID, or a server outside `ALLOWED_GUILD_IDS` | Digits only, and list the server; or unset it on the production bot. |
| A release's image build failed | The tag exists but `:latest` did not move | Re-run the failed jobs of that run (re-running all jobs publishes `:edge` only). |
