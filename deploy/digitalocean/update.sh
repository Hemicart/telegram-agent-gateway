#!/bin/sh
# Pull your fork's latest code and rebuild. The Telegram login stays on the tgdata volume.
set -eu
cd /opt/telegram-agent-gateway
git pull --ff-only
cd deploy/digitalocean
docker compose up -d --build
