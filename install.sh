#!/bin/sh
# weston-rail-web installieren (Debian 13)
set -e
[ "$(id -u)" = 0 ] || { echo "Bitte als root ausfuehren (sudo)."; exit 1; }
apt-get install -y guacd python3
install -d /usr/local/lib/weston-rail-web
cp -r weston-rail-web.py static /usr/local/lib/weston-rail-web/
chmod 755 /usr/local/lib/weston-rail-web/weston-rail-web.py
install -m 644 weston-rail-web.service /usr/local/lib/systemd/system/weston-rail-web.service 2>/dev/null ||
	install -D -m 644 weston-rail-web.service /usr/local/lib/systemd/system/weston-rail-web.service
systemctl daemon-reload
echo "Fertig. Starten mit: systemctl enable --now weston-rail-web"
