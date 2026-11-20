#!/usr/bin/env bash
# Installation im LXC-Container (Debian 12 / Ubuntu 22.04+), als root ausführen:
#   git clone https://github.com/rush0815/claude-meme-trade /opt/memebot
#   bash /opt/memebot/deploy/install.sh
set -euo pipefail
APP=/opt/memebot
cd "$APP"

echo "==> Pakete"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python >= 3.11 nötig"'

echo "==> Benutzer memebot"
id memebot >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin memebot

echo "==> Python-Umgebung"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r requirements.txt

echo "==> Konfiguration"
[ -f config.toml ] || cp config.example.toml config.toml
if [ ! -f /etc/memebot.env ]; then
  install -m 600 -o root -g root deploy/memebot.env.example /etc/memebot.env
  echo "   /etc/memebot.env angelegt – bitte Schlüssel und Passwort eintragen!"
fi
mkdir -p data
chown -R memebot:memebot data
chmod 644 config.toml

echo "==> systemd"
install -m 644 deploy/memebot.service /etc/systemd/system/memebot.service
systemctl daemon-reload
systemctl enable memebot >/dev/null
systemctl restart memebot
sleep 2
systemctl --no-pager --lines=5 status memebot || true
IP=$(hostname -I | awk '{print $1}')
echo
echo "Fertig. Web-Oberfläche: http://$IP:8080"
echo "Logs:      journalctl -u memebot -f"
echo "Update:    bash $APP/deploy/update.sh"
