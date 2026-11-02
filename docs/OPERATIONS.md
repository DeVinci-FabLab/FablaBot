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
| `data/` volume | The database `data/fablabot.db` (see "Stored data"), its copies in `data/backups/`, and the fingerprint of the last command sync (`data/command_sync_state.json`; delete it to force a sync). |
| `logs/` volume | `logs/<date>.log` (every line, never sent) and `logs/guilds/<server ID>/<date>.log` (one server's lines). |

### Before every redeploy

Stop the stack, then archive `logs/` and `data/`: an archive of a running database can be inconsistent.

1. Stop the stack.

2. Create a private archive folder outside the volumes and restrict it:

   ```bash
   mkdir -p ~/fablabot-archive && chmod 700 ~/fablabot-archive
   ```

3. Archive both folders and restrict the file:

   ```bash
   STAMP=$(date +%Y%m%d-%H%M%S)
   tar -czf ~/fablabot-archive/fablabot-$STAMP.tar.gz -C "$VOLUMES_PATH/fablabot" data logs
   chmod 600 ~/fablabot-archive/fablabot-$STAMP.tar.gz
   ```

4. Check the file counts match:

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
6. Restart the stack and read the server's `Server check` lines (see "Server check at start"). The check
   runs once per start, so a server invited since the last start is read at the next one.

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
| `Bureau`, `Sbire Bureau` | Bureau permissions (messages, `/user` with roles, `/vocal create` for temporary channels); `Bureau` members can hand out `Sbire Bureau` and `agent MyFab` |
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

Most commands only run in one text channel per server. `/suggest`, `/msg purge` and a private `/help` work
outside it, `/msg clear` refuses it, and `/welcome approve` runs in a `welcome-…` channel.

1. The bot reads the channel from the server's stored settings.
2. If none is stored, it uses the text channel named `commandes_bot` (`helpers/utils.py:32`) and stores it.
3. Elsewhere, the bot answers "Vous ne pouvez pas utiliser de commandes en dehors du salon ...", and
   with no such channel, "Le salon de commandes du bot n'est pas configuré sur ce serveur."
   `/help show:True` has its own refusal, in the server's language, that says to drop `show`.

So on a new server, create a text channel named `commandes_bot`.

`/help` is open to every member, in any channel where they can use application commands, so members do not
need access to this one. Only `/help show:True`, which posts the list for everyone, stays in this channel.

`/log set` picks the channel that receives the server's errors. `/log export` sends that server's log
file to that server only.

## Server language

The help answers in the language the server declares to Discord: `fr`, `de`, else English. Discord lets a
server set its language only in Community mode: Server Settings > Enable Community, then Server Settings >
Community > Overview > Server Primary Language. A server outside Community mode reports `en-US`, so its help
is in English.

After the deploy, check it on a server in Community mode with French as its primary language, where `/help`
should answer in French.

A per-server language setting in the bot is planned and not available yet. The texts live in
`src/fablabot/locales/<language>.toml`; adding a language means adding one file with the keys of `en.toml`.

## Temporary admin

`/user op` gives the `Admin -temp-` role for a limited time (5 minutes by default); `/user deop` removes it
early. The expiry is recorded in the database.

The expiry and the timer are set before the role is given. If the database refuses the expiry, the role is not
given and the caller is told. The timer holds its own time, so the role ends on time even with the database down.

At start and then every minute, the bot checks each holder of `Admin -temp-` (`cogs/user_management.py`,
`_settle_ops`). A role given by hand has no expiry, so it goes within a minute.

- A holder with a running timer is left to it. A holder with a future expiry gets a timer.
- A holder with no recorded expiry, or one that has passed, loses the role, and the log says
  `Removed temporary admin role (no recorded expiry) guild_id=... member_id=...`.
- A holder whose expiry cannot be read loses the role at once (`expiry unreadable`): run `/user op` again once
  the database answers.
- A removal that fails is tried again every minute, with no end. At the scheduled expiry the first failure also
  posts one notice in the op channel. The log shows `Failed to remove temporary admin role`. On a 403 (Missing
  Permissions) the bot's role sits below `Admin -temp-`: move it above (step 3 of "Add a server").
- A record the database could not read or drop stays, and the next round takes it up. The log shows
  `Op settling failed`.

A bot restarted on a full disk refuses to start (see `Cannot start`), so nothing removes a temporary role until
the disk is fixed: fix the disk first, then check the holders of `Admin -temp-` in the server settings.

## Server check at start

At the first start of the process, once the allow-list is applied, the bot reads each server it serves
and writes what that server lacks to the log. It changes nothing: no Discord write, no message in a
channel, nothing in `data/`. A reconnection does not run it again, and a server invited after the start
waits for the next restart.

Each server gets one summary line, then one line per problem:

```text
Server check: guild_id=111111111111111111 blocking=1 warnings=0 locale=fr texts=fr
Server check problem: guild_id=111111111111111111 severity=blocking code=log_channel_missing detail=[stored_id=None] fix: run /log set in the commands channel
```

`blocking=0 warnings=0` means the server is ready. `blocking` counts what makes a function fail on that
server for the people who need it, so a server with a blocking problem is not ready. `warning` counts what
degrades the bot without stopping it. A server that cannot be read counts as a warning, so an unreadable
server never shows a clean summary. `locale` is the language the server declares to Discord and `texts`
the language the bot answers in.

One more line lists the optional roles a server does not have (`Server check: optional roles absent`). It
counts for nothing: the roles are optional, as in "Roles".

The lines are WARNING at most, so the Discord log handler, which takes ERROR and above, never posts them.
They go to `logs/<date>.log` and to `logs/guilds/<server ID>/<date>.log`, so a server's staff reads its own
lines with `/log export`. They name no member. A role appears by name only when the bot itself names it
(the list in "Roles"); any other role is given as `role_id=<ID>`, since a name typed by the staff may hold a
person's name. Copy the ID into Discord's role search or Developer Mode to find the role.

| Code | Severity | What it means | Fix |
| --- | --- | --- | --- |
| `commands_channel_missing` | blocking | No stored commands channel is usable, and no text channel is named `commandes_bot`. | create a text channel named commandes_bot |
| `commands_channel_no_post` | warning | The bot cannot view or post in the commands channel (`channel_id=`). Answers to commands still work; its notifications are lost. | let the bot view and post in the commands channel |
| `log_channel_missing` | blocking | No `/log set` was run, or the stored channel (`stored_id=`) is gone or is not a text channel. | run /log set in the commands channel |
| `log_channel_no_post` | blocking | The bot cannot view, post or attach files in the log channel (`channel_id=`). | let the bot view, post and attach files in the log channel, or run /log set |
| `permissions_missing` | blocking | The bot's role lacks some permissions of the invitation; the detail lists their discord.py names (`read_messages` is View Channels). | give these permissions to the bot's role, or invite the bot again |
| `role_order_temp` | blocking | `Admin -temp-` sits above the bot's role, or is the bot's own highest role, so `/user op` fails with a 403. | drag the bot's role above Admin -temp- |
| `role_order` | warning | Roles the bot gives or removes sit above its role, or are its own highest role. The detail lists them. `Administrateur` is not counted, since no command gives it. | drag the bot's role above these roles |
| `roles_missing` | blocking | `CoDir` or `Admin -temp-` is missing, or none of the four admin roles exists, so nobody can finish the setup. | create the role with this exact name |
| `welcome_setup_missing` | blocking | The welcome flow is on, and the `Validation` category or the `Membre ✓` role is missing. | create what the welcome flow needs, or turn it off with /welcome verify |
| `suggest_recipient_unreachable` | warning | A recipient of `/suggest` (the detail gives its key) has no member holding its role and no channel. | give the role to a member or create the channel |
| `language_not_shipped` | warning | The language the server declares has no text file, so the help is in English. | pick a shipped language in Server Settings, Community, Overview |
| `integrations_no_role` | blocking | None of the roles that can use a staff group (the detail gives `/fm`, for instance) is allowed on it in Integrations, so only administrators see it in the slash menu. | allow the group's roles in Integrations |
| `integrations_role_missing` | warning | Some roles that can use a staff group are not allowed on it in Integrations; the detail lists them. A role with the Administrator permission needs no setting. | allow these roles on the group in Integrations |
| `integrations_everyone_allowed` | warning | `@everyone` is allowed on a staff group in Integrations, so every member sees it in the menu. | remove @everyone from the group in Integrations |
| `member_command_denied` | blocking | A command for every member, such as `/suggest`, is denied for `@everyone` or for All Channels in Integrations, on the command itself or on the whole bot. A setting on the command wins over the bot-wide one. | remove the deny on this command in Integrations |
| `leftover_commands` | warning | Commands registered on this server alone (`/user command_id=…`), left by an older release or a test. | the bot removes them at start; if this line stays, read the warning before it |
| `check_failed` | warning | A check did not finish (`step=local status=<error type>`), a read of Discord failed (`step=global commands`, `step=server commands` or `step=integrations`, with `status=HTTP <code>` or the error type), a command of the bot is not on Discord (`step=integrations command=/<name> not on Discord`), the report of the server could not be written (`step=report status=<error type>`), or Discord has not delivered the server (`step=unavailable`). The report of that server is incomplete. | read the next start; HTTP 403 on commands means the invite lacked applications.commands |

The check reads Discord once for the bot's global commands, then twice per server, for its commands and its
Integrations settings: 11 reads for 5 servers. With `DEV_GUILD_ID` set, the commands live on that one server, so
`leftover_commands`, `integrations_*` and `member_command_denied` are read there only, a command of the
bot's own list is no leftover there, and the other servers get the checks that need no Discord call.

Before the check, the first ready removes the commands registered on a served server alone: it reads them,
and only when there are any, clears and syncs that server, then logs `Removed leftover commands` with the
IDs and names. The global commands and their Integrations settings are untouched. With `DEV_GUILD_ID` set,
nothing is removed. A failure is logged as a warning with the error type, and the next server goes on.

## Stored data

Everything the bot keeps lives in one SQLite file, `data/fablabot.db`: per-server settings, training drafts,
published messages and their registrations, tracked messages, the reaction history and the temporary-admin
expiries. `data/command_sync_state.json` stays a separate file: it is a cache you may delete.

- The file is one database at rest: no `-wal` or `-shm` file. A `data/fablabot.db-journal` file only exists while a write is
  in progress, or after a crash, and the next start finishes the recovery from it. Never delete it.
- When a release changes the schema, the bot first copies the database to `data/backups/`, then moves the schema at start.
  It never removes these copies, and they count in the archive of "Before every redeploy".
- A release never goes back: a database from a newer release stops an older one.
- JSON state files left by an older release (`guild_config.json`, `formations_state.json`, `messages_state.json`,
  `temp_admin_state.json`) stop the bot until they are imported. The bot never reads, changes or removes them.

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
| "Vous ne pouvez pas utiliser de commandes en dehors du salon ..." | The command ran outside the commands channel | Use the channel named in the message. A member who cannot open it uses `/suggest` or a private `/help`, which work in any channel where they can use application commands. |
| "A public list only goes in ..." | `/help show:True` ran outside the commands channel | Run `/help` without `show`, or use the commands channel. |
| "Le salon de commandes du bot n'est pas configuré sur ce serveur." | No stored channel and no `commandes_bot` channel | Create a text channel named `commandes_bot`. |
| A `/user op` role is not added or removed (403) | The bot's role is below `Admin -temp-` | Move the bot's role above it. |
| `Missing roles ... for guild ...` in the log | The server lacks a role name the bot expects | Create the role with the exact name (see Roles). |
| "This command is outdated" | The client holds an old command list | Reload Discord (Ctrl/Cmd+R). |
| No errors reach the operator channel | `OPERATOR_LOG_CHANNEL_ID` is empty or malformed (warning at start) | Set a valid channel ID; they stay in `logs/<date>.log` meanwhile. |
| A staff member no longer sees their commands | Their role is not allowed on that command in Integrations, or their client holds the old list | Add the role as in the README table, then reload Discord. |
| A member sees a staff group | `@everyone` is allowed on that command in Integrations, or a role they hold was added at the top of the page | Remove that entry; leave the Roles & Members section at the top alone. |
| A changed command does not show after a redeploy | The record matches while Discord holds other commands (another instance with the same token, or a rollback) | Delete `data/command_sync_state.json` and restart. |
| Bot stops at start with `DEV_GUILD_ID: ...` | Malformed ID, or a server outside `ALLOWED_GUILD_IDS` | Digits only, and list the server; or unset it on the production bot. |
| `Cannot start: data/... not imported into data/fablabot.db` | JSON state of an older release sits in `data/` and the database does not hold it | Nothing was changed. Keep the files and import them; never delete them to get past the message. |
| `Cannot start: data/... changed since it was imported` | An older image ran after the import, so the file holds what the database lacks | Nothing was changed. Do not start this release before you know which side holds the latest data. |
| `Cannot start: data/fablabot.db is at schema version ...` | The database comes from a newer release | Run that release or a later one. |
| `Cannot start: data/fablabot.db cannot be used: ...` | The file is damaged, or `data/` is not writable | Restore the file from an archive or from `data/backups/`, or fix the folder's owner. |
| A release's image build failed | The tag exists but `:latest` did not move | Re-run the failed jobs of that run (re-running all jobs publishes `:edge` only). |
