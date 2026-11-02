# Operator guide

This guide is for whoever hosts and runs a FablaBot instance. It covers deploying the image,
adding and removing servers, the Discord Developer Portal settings, the roles the bot expects, and
the problems seen in production. Commands for members and staff are in the [user guide](USERGUIDE.md).

## Deployment model

Every push to `main` publishes `:edge` and `:sha-<commit>`. `:latest`, `:X.Y.Z` and `:X.Y` move only
when the release pull request is merged (see the [README](../README.md#releases), "Releases").
Production follows `:latest`; a test server can follow `:edge`. A merge to `main` is not a deploy, but a
release is: the production stack has `poll_for_updates` and `auto_update` on, and a Komodo procedure runs every
30 minutes, so `:latest` reaches production within 30 minutes. Treat merging the release pull request as
deploying. The stack can also be redeployed by hand (pull the image, recreate the container). In Komodo,
the production container is named `fablabot`.

The host also reboots every Sunday at 03:00, which restarts the bot on the same image.

Example `compose.yml` (the repository's `compose.yml` is the reference):

```yaml
services:
  python-prod:
    image: ghcr.io/devinci-fablab/fablabot:latest
    container_name: fablabot-prod
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /tmp:size=1m,mode=1777,noexec,nosuid,nodev
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
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

The image runs as `10001:10001`; the volumes and the token file must belong to that user (see "Moving a host to the non-root image" below, and the [README](../README.md#moving-a-host-to-the-non-root-image)).

| Setting | Role |
| --- | --- |
| `DISCORD_TOKEN_FILE` | Path of the file holding the bot token. Read at start, before connecting; must be readable by 10001. |
| `ALLOWED_GUILD_IDS` | Server IDs the bot may serve, comma-separated, no quotes. Each ID can carry a name after a colon, `111111111111111111:Main server`: the bot ignores it, and it cannot hold a comma. |
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

3. Archive both folders and restrict the file. `data/exports/` is private to the image's user (see "Stored data"), so
   `tar` and `find` run with `sudo`: without it they skip that folder, and the two counts can still match with an
   export missing. The redirection is done by your own shell, so the archive stays yours.

   ```bash
   STAMP=$(date +%Y%m%d-%H%M%S)
   sudo tar -czf - -C "$VOLUMES_PATH/fablabot" data logs > ~/fablabot-archive/fablabot-$STAMP.tar.gz
   chmod 600 ~/fablabot-archive/fablabot-$STAMP.tar.gz
   ```

4. Check the file counts. The two numbers must be the same and above 0: a 0 is an archive of nothing, as a wrong
   `VOLUMES_PATH` gives.

   ```bash
   tar -tzf ~/fablabot-archive/fablabot-$STAMP.tar.gz | grep -vc '/$'
   sudo find "$VOLUMES_PATH/fablabot/data" "$VOLUMES_PATH/fablabot/logs" -type f | wc -l
   ```

   Then count the export files alone, the ones a `tar` or `find` run without `sudo` drops without a word. Again the two
   numbers must be the same: 4 for each export folder, 0 only while there is none (`find` then says the folder does not
   exist).

   ```bash
   tar -tzf ~/fablabot-archive/fablabot-$STAMP.tar.gz | grep -c '^data/exports/.*\.json$'
   sudo find "$VOLUMES_PATH/fablabot/data/exports" -name '*.json' | wc -l
   ```

5. Redeploy the stack.

Never purge logs. Since the per-server log change, the bot deletes no log file. How long to keep them
is a decision for the association (GDPR), not for the bot.

### Releasing and redeploying

1. Archive `logs/` and `data/`, as in "Before every redeploy": do it before the merge, since the release can
   deploy itself.
2. Merge the release pull request `chore(main): release X.Y.Z`.
3. Wait for the "Build and Push Docker Image" run to finish green: `:latest` is then `:X.Y.Z`.
4. Read the running revision. If it is still the old one, redeploy the stack in Komodo, or wait for the update.
5. Check the start in the log, as usual: the `Server check` lines give each server's state.

The production stack stays on `:latest`; a test server can follow `:edge`.

The first release that keeps its state in the database does not follow these steps: see "Moving to the database".

### Rolling back

Set the stack's image to the `:X.Y.Z` of the previous release, or to `:sha-<commit>`. Images from before
the first release use `:<commit sha>`. Read the changelog entries in between for changes to `data/`,
then redeploy. Go back to `:latest` once a fixed release is out.

After a rollback, delete `data/command_sync_state.json` before upgrading again (see "This command is
outdated"). The [README](../README.md#rolling-back) has the same steps under "Rolling back".

#### Back to a release before the database

A release from before the database ignores `data/fablabot.db` and reads the JSON files, which stopped at the import. It
starts with no error, on old state: sign-ups since the import are gone for it, and reminders already sent go out again.
So write fresh JSON files first. If the database release never started, skip to step 4: the JSON files are still right.

1. Stop the stack, then archive, as in "Before every redeploy".
2. Export, with the image that was running and the `fablabot_state` helper of "Moving to the database". The command
   writes a new folder, and says how many replaced training messages have no place in those files: they stay in
   `data/fablabot.db`. On a volume that is not the bot's, a wrong `VOLUMES_PATH` or an empty folder, it stops with
   `Cannot export` and writes nothing: fix the path and run it again.

   ```bash
   IMAGE=ghcr.io/devinci-fablab/fablabot:X.Y.Z   # the release that was running
   fablabot_state "$VOLUMES_PATH/fablabot/data" fablabot.export_state data/exports/$STAMP
   ```

3. Look at the export, then move the old JSON files aside and copy the export in their place. Nothing is deleted. The
   export folder is private to the image's user, so every command that reads it runs with `sudo`, and the copy names
   its files: your own shell cannot enter the folder, so it would leave a `*` as it is, and `cp` would copy nothing
   after the old files were already aside.

   ```bash
   cd "$VOLUMES_PATH/fablabot/data"
   sudo ls exports/$STAMP
   ```

   It must list `guild_config.json`, `formations_state.json`, `messages_state.json` and `temp_admin_state.json`. If it
   does not, stop: nothing has moved. Then:

   ```bash
   sudo mkdir json-before-$STAMP
   sudo mv guild_config.json formations_state.json messages_state.json json-before-$STAMP/
   [ ! -e temp_admin_state.json ] || sudo mv temp_admin_state.json json-before-$STAMP/
   sudo cp -p exports/$STAMP/guild_config.json exports/$STAMP/formations_state.json \
     exports/$STAMP/messages_state.json exports/$STAMP/temp_admin_state.json .
   ls -l guild_config.json formations_state.json messages_state.json temp_admin_state.json
   ```

   The last command must list the four files. If one command above failed, do not deploy: the older release would start
   on empty state, with no error. `json-before-$STAMP/` holds the files that were moved; copy them back with
   `sudo cp -p`.

4. Set the stack's image to the older `:X.Y.Z` and deploy. Stay on that tag: `:latest` would bring the update back. If
   the older image runs as another user, give it `data/` first, with `sudo chown -R`.

To move forward again, the JSON files now hold what the database lacks, so the database release refuses to start. It
says `changed since it was imported` when the older release ran and wrote to the files. It says
`data/temp_admin_state.json not imported` when you went back by export and the older release never ran: the export
brings that fourth file, which the first import never saw. Either way, stop the stack, archive, set the database aside,
then follow "Moving to the database" from step 8. The file set aside keeps the replaced messages.

```bash
sudo mv "$VOLUMES_PATH/fablabot/data/fablabot.db" "$VOLUMES_PATH/fablabot/data/fablabot-before-$STAMP.db"
```

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

## Moving to the non-root image

The image runs as `10001:10001`. The production host keeps its state in `data/fablabot.db`, beside the JSON files of the
older releases, and all of it, `logs/` and the token file belong to root. The host moves once, with the release that
brings the non-root image. The commands are in the [README](../README.md#moving-a-host-to-the-non-root-image), "Moving a
host to the non-root image"; the order is:

1. Archive, with the bot running, into `~/fablabot-archive` (steps 2 to 4 of "Before every redeploy"). The `sudo`
   steps ask for the operator's password.
2. Hold the update: read the running version on the `fablabot` container, set the stack's `image:` to it and deploy.
   The stack updates itself every 30 minutes and merging a release deploys.
3. Merge the release pull request, wait for the image build to finish green, and pull the new image.
4. Stop the stack and archive again.
5. `sudo chown -R 10001:10001` on `data/` and `logs/`. Take `data/` whole: the database, its journal,
   `data/backups/`, `data/exports/` and the JSON files all go to 10001.
6. Make the token file readable by 10001: `sudo chown 10001:10001` and `sudo chmod 400` on it.
7. Add the hardening lines to the stack (the example under "Deployment model"), with the new version as `image:`.
8. Deploy.
9. Read the start: no `Cannot start` line, the `Server check` lines (see "Server check at start"), and the health
   status, `docker inspect --format '{{.State.Health.Status}}' fablabot`, which goes from `starting` to `healthy`
   within two minutes.
10. Set `image:` back to `:latest` and deploy.

A host not yet moved stops at start with `Cannot start: data/ is not writable by uid 10001.` and
Docker restarts it until the volume is fixed. No data is lost; the bot is down meanwhile.

Rolling back needs two things together: the previous image (the `:X.Y.Z` read in step 2) and the stack without the hardening lines.
The older image runs as root, and root without capabilities cannot write files owned by 10001. After a rollback, give the folders back to `10001` before redeploying the non-root
image. Archive `data/` and `logs/` first; never delete them.

## Health status

The container is `healthy` while the bot is connected to Discord, and `unhealthy` after 90 seconds
without a heartbeat (the bot is blocked, or Discord is unreachable). It is a status only: nothing
restarts the bot on its own. To read the last checks:

```bash
docker inspect --format '{{json .State.Health}}' fablabot
```

The status alone is `docker inspect --format '{{.State.Health.Status}}' fablabot`.

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

## When a database write fails

A full disk or a lost volume makes writes fail while the bot runs. Each failure reaches the log and the server's
log channel. Free the space: the next write works, with no restart.

| What failed | What people see | What comes back on its own |
| --- | --- | --- |
| A reaction on a training message | The reaction stays; no DM, not in the list; `Reaction not recorded` in the log | At the next 5-minute tick after the disk is back: the member is registered at that time, in the order of that moment, if the deadline has not passed |
| `/fm publish` or `/msg publish` | The post disappears and the staff member is told; the draft stays | Nothing: publish again |
| `/user op` | The role is not given, and the caller is told in private | Nothing: run it again |
| A reminder flag | The reminder goes out again at the next tick while its window lasts | The flag, once a tick can write it |

A restart during the failure forgets which servers to catch up: a member who reacted then has to react again.

An update of the training message that stops for another reason (Discord does not return the post, or the run
fails) is run again at the next 5-minute tick: the log shows `Error during scheduled update` or `Failed to fetch
message`, and a member whose reaction is logged gets their place then, with their DM unless it already failed once after the save. This includes a post the bot
may not read (403): an admin can repair the right, and the log gets one line per tick until then. A post that was
deleted or a channel that is gone (`is gone for guild` in the log) is not asked for again at the tick, unless a
failed write left the server marked: the next reaction runs the update. A restart forgets the retry, as above.

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

The reaction history is kept for 3 years, then deleted at the next write. The registrations of a replaced training
message stay with no time limit, so member IDs and names stay in the file and in `data/backups/` until the planned
step that keeps only anonymous statistics after 3 years.

- The file is one database at rest: no `-wal` or `-shm` file. A `data/fablabot.db-journal` file only exists while a write is
  in progress, or after a crash, and the next start finishes the recovery from it. Never delete it.
- When a release changes the schema, the bot first copies the database to `data/backups/`, then moves the schema at start.
  It never removes these copies, and they count in the archive of "Before every redeploy".
- A release never goes back: a database from a newer release stops an older one.
- JSON state files left by an older release (`guild_config.json`, `formations_state.json`, `messages_state.json`,
  `temp_admin_state.json`) stop the bot until they are imported (see "Moving to the database"). The bot never reads,
  changes or removes them. They stay in place after the import, and the bot compares their fingerprints at each start.
- The `__global__` section of `guild_config.json`, the instance-wide log channel of early releases, becomes one row that
  no command writes. When the bot is ready, it gives that channel to the server that owns it, if that server has no log
  channel, and keeps the row. A server that is no longer allowed keeps its settings; nothing reads them until it is
  listed again.
- `data/exports/<name>/` holds what `fablabot.export_state` wrote. These folders carry the same data, count in the
  archive, and are never removed by the bot. Each is private to the image's user: read it with `sudo`, and archive it
  with `sudo` too, or `tar` skips it without an error. Step 4 of "Before every redeploy" checks that the archive holds
  them.

## Moving to the database

Do this once, for the first release that keeps its state in `data/fablabot.db`. That release refuses to start over
the JSON files of older releases, and a release can deploy itself, so the update is held first. The bot is down from
step 6 to step 11: about 5 minutes when nothing goes wrong. The JSON files are never changed, so a failed import costs
that stop and nothing else.

Pick the moment first. A reaction made while the bot is off never counts: it stays on the message, and the member gets
no seat, no DM and no error. A reminder goes out only within 5 minutes of its time, so a stop across it loses that
reminder.

- Stay 24 hours away from the start of any training, on every server, before and after.
- Avoid the day after a `/fm publish`, when members react.
- Avoid Sunday around 03:00, when the host reboots.

The commands run on the host, through this helper. They need no token, no `logs/` volume and no network.

```bash
fablabot_state() {
  folder=$1; shift
  docker run --rm --network none --read-only \
    --tmpfs /tmp:size=1m,mode=1777,noexec,nosuid,nodev \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -v "$folder:/app/data" "$IMAGE" python -m "$@"
}
```

There is no `--user` on purpose: the image runs as the user the bot runs as, so what the import writes belongs to the
bot. `data/` must belong to that user, root included: the helper drops every capability, so even root cannot write a
folder that someone else owns, and the import stops with `cannot be used: unable to open database file`. With an image
that runs as root, a later move to `10001` must take `data/` whole with `chown -R`, the database, its journal and
`data/backups/` included: see "Moving to the non-root image". Step 8 checks the owner.

When you own `data/` and the image runs as root, as on the first move, you can skip the `chown`: add
`--user "$(id -u):$(id -g)"` to the helper. The database then belongs to you, and the bot, running as root with its
usual capabilities, still writes it. It needs no `sudo` and changes no owner.

1. **Tell each server's admins, the day before.** The first start removes every `Admin -temp-` given by hand, within a
   minute, on every server. Only roles from `/user op` stay. A holder the bot cannot touch, because the role sits above
   the bot's, writes two error lines a minute to that server's log channel until someone moves the role.

2. **Archive,** with the bot running, from step 2 of "Before every redeploy". The bot replaces each JSON file whole, so
   each one in the archive is complete. Keep `STAMP` for the next step.

3. **Rehearse on a copy.** The `:edge` image holds the import as soon as it is on `main`.

   ```bash
   IMAGE=ghcr.io/devinci-fablab/fablabot:edge
   docker pull "$IMAGE"
   COPY=~/fablabot-archive/rehearsal-$STAMP
   sudo mkdir -m 700 "$COPY"
   sudo tar -xzf ~/fablabot-archive/fablabot-$STAMP.tar.gz -C "$COPY" data
   fablabot_state "$COPY/data" fablabot.import_state --check
   fablabot_state "$COPY/data" fablabot.import_state
   fablabot_state "$COPY/data" fablabot.import_state --check
   ```

   The pull matters on a second try: `docker run` keeps a tag it already has, so without it the rehearsal would run
   the image from before the fix. The last command must print `data/fablabot.db matches the files.` Any
   `Cannot import` line is a fix to make while the bot still serves: stop here. Keep the copy with the archives.

4. **Hold the update.** Read the release running now: its version is a label on the container, while `.Config.Image`
   only prints `:latest`.

   ```bash
   docker inspect fablabot --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
   ```

   In Komodo, set the stack's `image:` to that version, `ghcr.io/devinci-fablab/fablabot:X.Y.Z` in place of `:latest`,
   and deploy. The container restarts once on the same image. A tag that does not move gives an automatic update
   nothing to pull. Read the stack's automatic update setting while you are there, and note it.

   ```bash
   docker inspect fablabot --format '{{.Config.Image}}'
   ```

   It must print the tag you set. If `image:` cannot be edited, turn the stack's automatic update off instead, note
   how you found it, and at step 5 read the version label again in place of the tag: it must still be the release
   running now.

5. **Merge the release pull request.** Wait for the "Build and Push Docker Image" run to finish green, then pull the
   new image. The bot keeps running on the held tag.

   ```bash
   IMAGE=ghcr.io/devinci-fablab/fablabot:X.Y.Z   # the new release
   docker pull "$IMAGE"
   docker inspect fablabot --format '{{.Config.Image}}'
   ```

   The last command must still print the held tag.

6. **Stop the stack.** The bot is down from here.

7. **Archive again,** steps 3 and 4 of "Before every redeploy". Nothing moves now.

8. **Check.** First the owner of `data/`: the two numbers must be the same. If they differ, the import stops on
   `unable to open database file`: give `data/` to the image's user, root included, with
   `sudo chown -R <the first number> "$VOLUMES_PATH/fablabot/data"`.

   ```bash
   docker run --rm --entrypoint id "$IMAGE" -u
   stat -c %u "$VOLUMES_PATH/fablabot/data"
   ```

   Then the files: one line per server, with counts and IDs only. Compare them with step 3: the same servers, and
   counts that may have grown, since the bot ran in between.

   ```bash
   fablabot_state "$VOLUMES_PATH/fablabot/data" fablabot.import_state --check
   ```

9. **Import.** One transaction: it writes everything or nothing, and a second run has nothing to do.

   ```bash
   fablabot_state "$VOLUMES_PATH/fablabot/data" fablabot.import_state
   ```

10. **Check again,** with the `--check` command of step 8. It must print `data/fablabot.db matches the files.`

11. **Start.** In Komodo, set `image:` to the new `:X.Y.Z` and deploy.

12. **Read the start.**
    - No `Cannot start` line.
    - One `Server check:` line per server, with the `blocking=` you had before (see "Server check at start").
    - `Removed temporary admin role (no recorded expiry)` for the roles of step 1, if any are left: the restart of
      step 4 may have removed them already.
    - `/fm export` on each server with a published training: same members, same order.
    - A member whose reaction is on the message but who is not on the list reacted during the stop: ask them to take
      it off and add it again.

13. **Release the hold.** Set `image:` back to `:latest`, set the automatic update back as you found it, and deploy.
    The container restarts once.

### If a step fails

| Step | What you see | What to do |
| --- | --- | --- |
| 3 | `Cannot import: <file>/<place>: ...` | Production has not moved. The line gives the place in the file, never its value: a key that is no ID and no field name shows as `key #3`, its position counted from 0. The update is not held yet, so do not cut a release: merging the release pull request would move `:latest`, and a release can deploy itself over JSON that was never imported, into a restart loop. Fix the cause in a pull request on `main`, leave the release pull request open, wait for the `:edge` build of the fix to finish green, then start again from step 2. |
| 8 or 9 | `Cannot import: ...`, or the import dies | Deploy the stack as it is: it still holds the old release, which reads the JSON files the import left alone. A failed import leaves no row. `data/fablabot.db` stays and the old release ignores it. The next import fills it, unless you went back by export before: the database then holds the data, so set it aside first with the `mv` at the end of "Back to a release before the database". |
| 9 | `data/fablabot.db-journal` next to the database | The import was killed. Run step 9 again: it finishes the recovery and starts over. Never delete the journal. |
| 10 | The line is not `data/fablabot.db matches the files.` | The import is committed: the database holds the state of the files. The old release ignores it and reads the JSON files. If it ran, it wrote to them and the database is behind: the next import would stop on `changed since the import`. Before starting again from step 7, set `data/fablabot.db` aside with the `mv` at the end of "Back to a release before the database". It keeps the data and counts in the archive. If the old release did not run, leave it in place. |
| 11, or the update was not held | `Cannot start: data/... not imported`, in a restart loop | Nothing was changed and the bot never logged in. Stop the stack, then go on from step 7. |
| 12 | The new release misbehaves | Go to "Back to a release before the database". |

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
| Bot stops at start with "entry N is not a numeric guild ID" | `ALLOWED_GUILD_IDS` has quotes, or an entry whose ID is not numeric | Use `id1:name,id2:name`, with digits before each colon. |
| A merged fix is not live | A merge does not deploy; only a release reaches `:latest` | Archive, then merge the release pull request. |
| "Vous ne pouvez pas utiliser de commandes en dehors du salon ..." | The command ran outside the commands channel | Use the channel named in the message. A member who cannot open it uses `/suggest` or a private `/help`, which work in any channel where they can use application commands. |
| "A public list only goes in ..." | `/help show:True` ran outside the commands channel | Run `/help` without `show`, or use the commands channel. |
| "Le salon de commandes du bot n'est pas configuré sur ce serveur." | No stored channel and no `commandes_bot` channel | Create a text channel named `commandes_bot`. |
| A `/user op` role is not added or removed (403) | The bot's role is below `Admin -temp-` | Move the bot's role above it. |
| Bot restarts in a loop with `Cannot start: data/ is not writable by uid 10001.` | The volume still belongs to root, or a file under it does | `sudo chown -R 10001:10001` the folder named, then wait for the restart. |
| `Cannot start: the token file ... is not readable by uid 10001` | The token file belongs to root | `sudo chown 10001:10001 <file> && sudo chmod 400 <file>`. |
| Status `unhealthy`, the bot answers | The stack has `read_only` without the `/tmp` tmpfs (an ERROR names the heartbeat file) | Add the `tmpfs` line. |
| After a rollback, registrations are lost | The older image runs as root without capabilities | Restore the stack without the hardening lines. |
| `Missing roles ... for guild ...` in the log | The server lacks a role name the bot expects | Create the role with the exact name (see Roles). |
| "This command is outdated" | The client holds an old command list | Reload Discord (Ctrl/Cmd+R). |
| No errors reach the operator channel | `OPERATOR_LOG_CHANNEL_ID` is empty or malformed (warning at start) | Set a valid channel ID; they stay in `logs/<date>.log` meanwhile. |
| A staff member no longer sees their commands | Their role is not allowed on that command in Integrations, or their client holds the old list | Add the role as in the README table, then reload Discord. |
| A member sees a staff group | `@everyone` is allowed on that command in Integrations, or a role they hold was added at the top of the page | Remove that entry; leave the Roles & Members section at the top alone. |
| A changed command does not show after a redeploy | The record matches while Discord holds other commands (another instance with the same token, or a rollback) | Delete `data/command_sync_state.json` and restart. |
| Bot stops at start with `DEV_GUILD_ID: ...` | Malformed ID, or a server outside `ALLOWED_GUILD_IDS` | Digits only, and list the server; or unset it on the production bot. |
| `Cannot start: data/... not imported into data/fablabot.db` | JSON state of an older release sits in `data/` and the database does not hold it | Nothing was changed. Keep the files and follow "Moving to the database"; never delete them to get past the message. After a rollback by export the database already holds the data: set it aside first, with the `mv` at the end of "Back to a release before the database". |
| `Cannot start: data/... changed since it was imported` | An older image ran after the import, so the file holds what the database lacks | Nothing was changed. Do not start this release before you know which side holds the latest data: see "Back to a release before the database". |
| `Cannot import: ...` | The JSON state holds something this release does not know, the database holds other data, or a file changed since the import | Nothing was written, and the line says where. See "If a step fails". |
| `Cannot start: data/fablabot.db is at schema version ...` | The database comes from a newer release | Run that release or a later one. |
| `Cannot start: data/fablabot.db cannot be used: ...` | The disk is full, `data/` is not writable, the file is damaged, or a migration left orphan rows | Check the disk and the folder's owner first. Restore the file from an archive or from `data/backups/` only if it is damaged. |
| A release's image build failed | The tag exists but `:latest` did not move | Re-run the failed jobs of that run (re-running all jobs publishes `:edge` only). |
