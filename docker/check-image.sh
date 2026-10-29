#!/bin/sh
# Build the images from the files .dockerignore admits, plus planted local files, then check what
# the production image holds and how it starts under the production compose restrictions.
# Needs Docker. Run from anywhere: sh docker/check-image.sh
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
cleanup() {
    docker rm -f fablabot-check-diff >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# The same restrictions as compose.yml.
hardening="--read-only --tmpfs /tmp:size=1m,mode=1777,noexec,nosuid,nodev --cap-drop ALL --security-opt no-new-privileges:true"
volumes="--tmpfs /app/data:uid=10001,gid=10001,mode=0700 --tmpfs /app/logs:uid=10001,gid=10001,mode=0700"

# Build context: what the allow-list admits, plus files that must never reach an image.
context="$work/context"
mkdir -p "$context/docker"
cp "$root/.dockerignore" "$context/"
cp "$root/docker/Dockerfile" "$context/docker/"
for entry in $(sed -n 's/^!//p' "$root/.dockerignore"); do
    cp -R "$root/${entry%/}" "$context/"
done
mkdir -p "$context/secrets" "$context/data" "$context/logs"
echo planted > "$context/secrets/discord_token.secret"
echo planted > "$context/.env"
echo planted > "$context/data/planted.json"
echo planted > "$context/logs/planted.log"
echo planted > "$context/notes.md"
echo planted > "$context/src/planted.secret"

docker build -q -f "$context/docker/Dockerfile" --target build -t fablabot:check-build "$context" >/dev/null
planted=$(docker run --rm --network none --entrypoint sh fablabot:check-build -c \
    'find / -xdev \( -name discord_token.secret -o -name planted.json -o -name planted.log -o -name notes.md -o -name planted.secret -o -path /app/.env \) -print')
[ -z "$planted" ] || fail "planted files reached the build stage: $planted"
echo "ok: no planted file in the build stage"

docker build -q -f "$context/docker/Dockerfile" --target production -t fablabot:check "$context" >/dev/null
[ "$(docker image inspect --format '{{.Config.User}}' fablabot:check)" = "10001:10001" ] || fail "the image user is not 10001:10001"
[ "$(docker image inspect --format '{{.Config.StopSignal}}' fablabot:check)" = "SIGINT" ] || fail "the stop signal is not SIGINT"
[ "$(docker image inspect --format '{{json .Config.Healthcheck.Test}}' fablabot:check)" = '["CMD","python","-m","fablabot.healthcheck"]' ] \
    || fail "the health check does not run fablabot.healthcheck"
content=$(docker run --rm --network none --entrypoint sh fablabot:check -c \
    'echo "$(id -u):$(id -g)"; ls -A /app; for tool in uv gcc; do command -v "$tool" || true; done')
[ "$content" = "$(printf '10001:10001\n.venv')" ] || fail "unexpected user or content: $content"
echo "ok: runs as 10001:10001, /app holds only .venv, no uv or compiler"

# Without volumes or token, the bot stops before connecting and names each problem.
set +e
stopped=$(docker run --rm --network none $hardening fablabot:check 2>&1)
code=$?
set -e
[ "$code" -eq 1 ] || fail "expected exit 1 without volumes, got $code: $stopped"
expected="Cannot start: data/ is not writable by uid 10001.
Cannot start: logs/ is not writable by uid 10001.
Cannot start: DISCORD_TOKEN_FILE is not set."
[ "$stopped" = "$expected" ] || fail "unexpected stop message: $stopped"
set +e
stopped=$(docker run --rm --network none $hardening $volumes -e DISCORD_TOKEN_FILE=/etc/shadow fablabot:check 2>&1)
code=$?
set -e
[ "$code" -eq 1 ] || fail "expected exit 1 with an unreadable token, got $code"
[ "$stopped" = "Cannot start: the token file /etc/shadow is not readable by uid 10001: Permission denied." ] \
    || fail "unexpected stop message: $stopped"
echo "ok: stops before connecting when a folder or the token cannot be used"

# A full start without network: cogs, log files, command record and one heartbeat, then the health check.
start='
import asyncio
from unittest.mock import AsyncMock

from fablabot.main import Fablabot


async def start() -> None:
    bot = Fablabot()
    bot.tree.sync = AsyncMock(return_value=[])
    await bot.setup_hook()
    await bot.on_ready()
    await asyncio.sleep(0.5)
    await bot.close()


asyncio.run(start())
'
docker run --rm --network none $hardening $volumes --entrypoint sh fablabot:check -c \
    "python -c '$start' >/dev/null 2>&1 && python -m fablabot.healthcheck" | grep -q '^healthy' \
    || fail "the offline start or the health check failed under the production restrictions"
echo "ok: starts offline with a read-only root, no capability and only /tmp in memory; health check healthy"

# The same start on a writable root: nothing may change outside the mounted folders.
docker run --name fablabot-check-diff --network none --cap-drop ALL --security-opt no-new-privileges:true \
    --tmpfs /tmp:size=1m,mode=1777,noexec,nosuid,nodev $volumes --entrypoint python fablabot:check -c "$start" >/dev/null 2>&1 \
    || fail "the offline start failed on a writable root"
changed=$(docker diff fablabot-check-diff | sort)
[ "$changed" = "$(printf 'A /app/data\nA /app/logs\nC /app')" ] || fail "files changed outside data/, logs/ and /tmp: $changed"
echo "ok: writes nothing outside data/, logs/ and /tmp"
