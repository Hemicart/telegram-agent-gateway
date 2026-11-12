#!/bin/sh
# PaaS volumes (Railway, Fly, Render) usually mount root-owned. Fix ownership, then drop root.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DATA_DIR:-/data}"
  chown -R app:app "${DATA_DIR:-/data}"
  chmod 700 "${DATA_DIR:-/data}"
  exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
fi
exec "$@"
