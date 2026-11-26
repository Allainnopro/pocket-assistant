#!/usr/bin/env bash
# One-time set-up of the cloud VM (Ubuntu 24.04). Run as root: sudo bash deploy/setup.sh
# Safe to run again. It never starts the service and never touches secrets.
set -euo pipefail

REPO=https://github.com/Allainnopro/pocket-assistant.git
CODE=/opt/pocket-assistant
DATA=/var/lib/pocket-assistant

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root: sudo bash deploy/setup.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y git python3-venv sqlite3

# The service runs as its own user, with no login shell.
if ! id pocket >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin pocket
fi

# Data folder: only pocket can read it (secrets.json lives here).
install -d -m 0700 -o pocket -g pocket "$DATA"

# 1 GB swap: the e2-micro has 1 GB of RAM, and pip install can need more.
# A swap file left half-made by an earlier run that died is thrown away and made again.
if [ -e /swapfile ] && [ "$(blkid -s TYPE -o value /swapfile 2>/dev/null || true)" != swap ]; then
  swapoff /swapfile 2>/dev/null || true
  rm -f /swapfile
fi
if [ ! -e /swapfile ]; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
fi
swapon --show=NAME --noheadings | grep -qx /swapfile || swapon /swapfile
grep -qE '^/swapfile[[:space:]]' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Code: root-owned, so the service cannot change it.
if [ -d "$CODE/.git" ]; then
  git -C "$CODE" pull --ff-only
else
  git clone "$REPO" "$CODE"
fi
[ -d "$CODE/.venv" ] || python3 -m venv "$CODE/.venv"
"$CODE/.venv/bin/pip" install -e "$CODE"

# Bytecode now, because the service cannot write to this folder later.
"$CODE/.venv/bin/python" -m compileall -q "$CODE/src"

install -m 0644 "$CODE/deploy/pocket-assistant.service" /etc/systemd/system/pocket-assistant.service
systemctl daemon-reload
systemctl enable pocket-assistant

echo "Set-up done. The service is enabled but NOT started: finish the steps in deploy/README.md first."
