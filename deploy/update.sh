#!/usr/bin/env bash
# Update the engine to the newest code, then restart it. Run as root: sudo bash deploy/update.sh
set -euo pipefail

CODE=/opt/pocket-assistant

git -C "$CODE" pull --ff-only
"$CODE/.venv/bin/pip" install -e "$CODE"
# A restart stops the engine the gentle way (the current job finishes), up to 150 s.
systemctl restart pocket-assistant
