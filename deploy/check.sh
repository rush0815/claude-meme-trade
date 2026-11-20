#!/usr/bin/env bash
# Verbindungstest aller APIs (als root). Stoppt den Bot kurz, weil PumpPortal nur eine Verbindung pro IP erlaubt.
cd /opt/memebot
set -a; [ -f /etc/memebot.env ] && . /etc/memebot.env; set +a
systemctl stop memebot
echo "DNS pumpportal.fun: $(getent ahosts pumpportal.fun | awk '{print $1}' | sort -u | tr '\n' ' ')"
.venv/bin/python -m memebot --check
systemctl start memebot
echo "Bot wieder gestartet."
