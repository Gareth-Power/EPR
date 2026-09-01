#!/bin/sh
# Ensure the data volume exists and, if PUID/PGID are set, hand it (and the
# process) to that host user so bind-mounted files aren't root-owned.
set -e

DATA="${EPR_DATA_DIR:-/data}"
mkdir -p "$DATA"

if [ "$(id -u)" = "0" ] && [ -n "$PUID" ]; then
    GID="${PGID:-$PUID}"
    chown -R "${PUID}:${GID}" "$DATA" 2>/dev/null || true
    exec gosu "${PUID}:${GID}" "$@"
fi

exec "$@"
