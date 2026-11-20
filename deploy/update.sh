#!/usr/bin/env bash
# Neueste Version holen und Bot neu starten (als root).
set -euo pipefail
cd /opt/memebot
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
systemctl restart memebot
systemctl --no-pager --lines=5 status memebot || true
