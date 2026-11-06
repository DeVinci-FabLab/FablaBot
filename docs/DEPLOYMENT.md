# Deployment and releases

This guide is for whoever deploys FablaBot or cuts a release: how the production image is built to run, how a
release is made and published, and how to move a host to the non-root image. Day-to-day running, the settings and
the problems seen in production are in the [operator guide](OPERATIONS.md).

- [The production image](#the-production-image)
- [Releases](#releases)
  - [Making a release](#making-a-release)
  - [Rolling back](#rolling-back)
  - [Repository settings](#repository-settings)
- [Moving a host to the non-root image](#moving-a-host-to-the-non-root-image)

## The production image

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

At start, before connecting, the bot checks that it can read and write every folder and file under `data/` and `logs/`, and read the token file. Otherwise it stops with one line per problem, for example `Cannot start: data/ is not writable by uid 10001.`, and Docker restarts it until the problem is fixed. A refused start deletes nothing and writes nothing, except that it creates a missing `data/` or `logs/` folder. No data is lost; the bot is down meanwhile.

The container reports `healthy` while the bot is connected to Discord, and `unhealthy` when its last heartbeat is more than 90 seconds old. It is a status only: nothing restarts the bot on its own. See "Health status" in the [operator guide](OPERATIONS.md#health-status).

The image carries the commit it was built from in `FABLABOT_REVISION`, which `/info` shows in its footer. The release build sets it from the build argument `REVISION`; an image built by hand without `--build-arg REVISION=<commit>` has none, and `/info` then shows no revision.

To check an image locally (needs Docker): `sh docker/check-image.sh`. It builds the image with planted secrets, then checks its user, its content, the start messages, and that nothing is written outside `data/`, `logs/` and `/tmp`.

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
4. Publishing restarts production: the stack updates itself every 30 minutes. Archive before merging, as the [operator guide](OPERATIONS.md#releasing-and-redeploying) says, and redeploy the stack if the running revision stays old.

If the image build of a release fails, the tag exists but `:latest` has not moved. Open that run in the Actions tab and choose "Re-run failed jobs": re-running all jobs would publish `:edge` only.

If the build stops with "The release is not this commit", the release was cut for an earlier merge than the push being built. Nothing is published for that run, so `:latest` stays on the previous release. The tag exists without an image; the next release publishes normally.

### Rolling back

Set the stack's image to the previous release, `ghcr.io/devinci-fablab/fablabot:X.Y.Z`, or to one commit with `:sha-<commit>`, and redeploy. The steps, and what to read first, are under "Rolling back" in the [operator guide](OPERATIONS.md#rolling-back).

Going back past the first release that keeps its state in the database takes one more step, or the older release reads stale files with no error: see [the operator guide](OPERATIONS.md#back-to-a-release-before-the-database).

### Repository settings

Releases need two settings, once, by a repository admin:

- Settings, Actions, General, Workflow permissions: tick "Allow GitHub Actions to create and approve pull requests", so the release pull request can be opened. No workflow approves pull requests. Each job asks for its own permissions, so the default should be "Read repository contents and packages permissions".
- Settings, General, Pull Requests, Allow squash merging: set the default commit message to "Pull request title", since the release reads the squash commit titles.

A pull request opened by a workflow does not start other workflows, so the release workflow starts the check on the release pull request itself. A required `check` status is met without a personal token.

## Moving a host to the non-root image

The production host already runs a release that keeps its state in `data/fablabot.db`, beside the JSON files of the older releases. The release that brings the non-root image needs `data/`, `logs/` and the token file to belong to `10001`; today they belong to another user. A host not yet moved stops at start with `Cannot start: data/ is not writable by uid 10001.`, and Docker restarts it until the volume is fixed. Do this once, on the host. The `sudo` steps ask for the operator's password. Keep the previous image's version at hand: it is the way back.

Merging a release deploys it: the stack updates itself every 30 minutes. The pull request that brings the image, titled `feat(docker)!: run the bot as an unprivileged user in a hardened container`, is what cuts that release, so the update is held before it is merged, and the stack is stopped only once the new image is in.

Set the volumes path in the shell you use for the steps below, to the value the stack uses:

```bash
VOLUMES_PATH=/path/to/volumes
```

1. **Archive.** Follow steps 2 to 4 of ["Before every redeploy"](OPERATIONS.md#before-every-redeploy) in the operator guide, skipping its step 1: the bot keeps running, and this copy is a safety net taken before the merge. The archive goes to `~/fablabot-archive`, outside the volumes, and the two counts must be the same. Never delete `data/` or `logs/`.

2. **Hold the update.** Read the state now, with the container (it is called `fablabot`) still on its current image. The first command prints the release running, and the second keeps the `Server check:` lines, one per server, to compare with after the move.

   ```bash
   docker inspect fablabot --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
   docker logs fablabot 2>&1 | grep 'Server check:' > ~/fablabot-archive/server-check-before.txt
   ```

   Then set the stack's `image:` to that version in place of `:latest` and deploy. The container restarts once on the same image. The stack updates itself every 30 minutes, and a tag that does not move gives it nothing to pull.

   ```bash
   docker inspect fablabot --format '{{.Config.Image}}'
   ```

   This command must print the tag you set.

3. **Merge the pull requests.** First the pull request that brings the image, `feat(docker)!: run the bot as an unprivileged user in a hardened container`. It is a breaking `feat`, and with `bump-minor-pre-major` on it gives a minor release. Then the release pull request `chore(main): release X.Y.Z`, which that merge updates. Wait for the "Build and Push Docker Image" run to finish green, then pull the new image. The bot keeps running on the held tag.

   ```bash
   docker pull ghcr.io/devinci-fablab/fablabot:X.Y.Z   # the new release
   ```

4. **Stop the stack.** The bot is down from here. Archive again, with all four steps of "Before every redeploy": an archive of a running database can be inconsistent, and the volumes are about to change owner.

5. **Give the folders to the bot's user.** First check that nothing on the host already uses id 10001. The command must print nothing. If it prints a line, stop: giving the files to that id would give the token and the state to that host account.

   ```bash
   getent passwd 10001; getent group 10001
   ```

   Then give both folders to the bot's user, `data/` whole: the database, its journal, `data/backups/`, `data/exports/` and the JSON files all belong to it.

   ```bash
   sudo chown -R 10001:10001 "$VOLUMES_PATH/fablabot/data" "$VOLUMES_PATH/fablabot/logs"
   ```

6. **Give the token file to the bot's user.** It is the file named under `secrets: discord_token: file:` in the stack.

   ```bash
   sudo chown 10001:10001 /path/to/discord_token.secret && sudo chmod 400 /path/to/discord_token.secret
   ```

7. **Add the hardening lines.** Add the seven lines of [the production image](#the-production-image) to the bot's service in the stack, and set `image:` to the new version, `ghcr.io/devinci-fablab/fablabot:X.Y.Z`.

8. **Deploy.**

9. **Read the start.** The first command prints `0`. The second prints `starting`, then `healthy` within two minutes. The third prints the `Server check:` lines, one per server: each `blocking=` and `warnings=` count is the one in `~/fablabot-archive/server-check-before.txt`, saved in step 2 (the timestamps differ).

   ```bash
   docker logs fablabot 2>&1 | grep -c 'Cannot start'
   docker inspect --format '{{.State.Health.Status}}' fablabot
   docker logs fablabot 2>&1 | grep 'Server check:'
   ```

   A `Cannot start:` line names the folder, file or setting to fix; once fixed, the bot starts at its next restart. Use your stack's container name if it is not `fablabot`. See ["Server check at start"](OPERATIONS.md#server-check-at-start) for the lines.

10. **Release the hold.** Pull `:latest`, so that the next start is the release you merged.

    ```bash
    docker pull ghcr.io/devinci-fablab/fablabot:latest
    ```

    Set `image:` back to `:latest` and deploy. The container restarts once, with the hardening lines kept. Then read the version label, which must be the release you merged, and the health, `healthy` within two minutes.

    ```bash
    docker inspect fablabot --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
    docker inspect --format '{{.State.Health.Status}}' fablabot
    ```

To roll back, set the image back to the previous image, the `:X.Y.Z` read in step 2, and restore the stack as it was before step 7. The rollback stays on the tag: `:latest` brings the non-root image back. The older image runs as root and reads the volumes without a `chown` back, but root without capabilities cannot write files owned by 10001: the seven lines must go with it, so the rollback is the previous image and the stack without the hardening lines, together. Archive `data/` and `logs/` first; never delete them.

After a rollback, the root image writes root-owned files. Run step 5 again (and step 6 if the token file was replaced) before you redeploy the non-root image.

After restoring an archive, run step 5 again: `tar` run as root restores the files as root.
