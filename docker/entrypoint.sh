#!/bin/sh
set -eu
mkdir -p /data/uploads /workspace
chown -R harness:harness /data
if ! runuser -u harness -- test -w /workspace; then
  chown harness:harness /workspace
fi
exec runuser -u harness -- "$@"
