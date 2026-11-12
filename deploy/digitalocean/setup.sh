#!/bin/sh
# Runs once on a fresh Ubuntu droplet (from cloud-init.yaml), or by hand: sudo sh setup.sh
set -eu
REPO_URL="${REPO_URL:-}"
DIR=/opt/telegram-agent-gateway
LOG=/var/log/telegram-agent-gateway-setup.log
exec >>"$LOG" 2>&1
echo "== setup $(date -u)"

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if [ ! -d "$DIR/.git" ] && [ ! -f "$DIR/Dockerfile" ]; then
  if [ -n "$REPO_URL" ] && git clone --depth 1 "$REPO_URL" "$DIR"; then
    echo "cloned $REPO_URL"
  else
    echo "Code not found at $DIR. Copy the repo there, then run: sudo sh $DIR/deploy/digitalocean/setup.sh"
    exit 0
  fi
fi

cd "$DIR/deploy/digitalocean"
if [ ! -f .env ]; then
  IP=$(curl -fsS http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)
  # A droplet gets its own certificate, so its address appears in public certificate logs within
  # minutes and bots could press Start first. A setup password closes that window.
  PW=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)
  umask 077
  printf 'DOMAIN=%s\nSETUP_PASSWORD=%s\n' "${DOMAIN:-$IP.sslip.io}" "$PW" > .env
fi
. ./.env

if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null
fi

docker compose up -d --build
MSG="READY: open https://$DOMAIN , press Start, and enter setup password: $SETUP_PASSWORD"
echo "$MSG"
echo "$MSG" > /etc/motd   # shown when you SSH in
echo "$MSG" > /dev/console 2>/dev/null || true   # shown in DigitalOcean's web console
