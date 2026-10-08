#!/bin/sh
#
# guacd auf Debian 13 bauen und installieren.
#
# Debian hat guacamole-server im November 2024 aus dem Archiv entfernt, es
# gibt also kein Paket mehr. Dieses Skript baut es aus den Quellen, nur mit
# RDP-Unterstuetzung (SSH, VNC, Telnet und Kubernetes brauchen wir nicht).
#
# Bevorzugt wird FreeRDP 2 mit guacamole-server 1.5.5 (von Apache als
# stabil empfohlen); ist FreeRDP 2 nicht vorhanden, wird 1.6.0 mit
# FreeRDP 3 gebaut.
#
set -e

[ "$(id -u)" = 0 ] || { echo "Bitte als root ausfuehren (sudo)."; exit 1; }

PREFIX=${PREFIX:-/usr/local}
BUILD=${BUILD:-/usr/local/src}

apt-get update
apt-get install -y --no-install-recommends \
	build-essential autoconf automake libtool pkgconf curl ca-certificates \
	libcairo2-dev libjpeg62-turbo-dev libpng-dev libossp-uuid-dev

# Debian nennt das Entwicklerpaket von FreeRDP 2 "freerdp2-dev"
if apt-get install -y --no-install-recommends freerdp2-dev 2>/dev/null ||
   apt-get install -y --no-install-recommends libfreerdp-dev libwinpr-dev 2>/dev/null; then
	VERSION=${VERSION:-1.5.5}
	echo "FreeRDP 2 gefunden: baue guacamole-server $VERSION (empfohlen)"
else
	apt-get install -y --no-install-recommends freerdp3-dev libwinpr3-dev
	VERSION=${VERSION:-1.6.0}
	echo "FreeRDP 3: baue guacamole-server $VERSION (RemoteApp dort experimentell;"
	echo "fuer weston-rail-web ohne Belang, wir benutzen gewoehnliches RDP)"
fi

mkdir -p "$BUILD"
cd "$BUILD"
TARBALL="guacamole-server-$VERSION.tar.gz"
[ -f "$TARBALL" ] || curl -fL -o "$TARBALL" \
	"https://downloads.apache.org/guacamole/$VERSION/source/guacamole-server-$VERSION.tar.gz"
rm -rf "guacamole-server-$VERSION"
tar xf "$TARBALL"
cd "guacamole-server-$VERSION"

# CPPFLAGS: configure prueft mit einem eigenen Testprogramm, ob die
# FreeRDP-Strukturen einen "context" haben (FreeRDP 3), setzt dabei aber
# den Suchpfad nicht. Unter Debian liegen die Header in
# /usr/include/freerdp3, der Test schlaegt daher fehl und guacamole-server
# uebersetzt gegen die alten FreeRDP-2-Strukturen -> Fehler
# "'freerdp' has no member named 'input'".
# -Wno-error/-Wno-deprecated-declarations: configure haengt -Werror an;
# neuere FreeRDP-Versionen melden abgekuendigte Namen.
RDP_CPPFLAGS=$(pkg-config --cflags freerdp2 winpr 2>/dev/null ||
	pkg-config --cflags freerdp3 winpr3 2>/dev/null || true)
CPPFLAGS="$RDP_CPPFLAGS" ./configure --prefix="$PREFIX" \
	--with-rdp \
	--without-vnc --without-ssh --without-telnet --without-kubernetes \
	--disable-guacenc --disable-guaclog \
	CFLAGS="-g -O2 -Wno-error -Wno-deprecated-declarations"
make -j"$(nproc)"
grep -q "define FREERDP_HAS_CONTEXT" config.h 2>/dev/null ||
	echo "Hinweis: FreeRDP-3-Erkennung fehlgeschlagen (siehe config.log)"
make install
ldconfig

cat > /etc/systemd/system/guacd.service <<EOF
[Unit]
Description=Guacamole proxy daemon (guacd)
After=network.target

[Service]
ExecStart=$PREFIX/sbin/guacd -b 127.0.0.1 -f
Restart=on-failure
User=nobody
Group=nogroup

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now guacd
sleep 1
systemctl --no-pager --lines=5 status guacd || true
echo
echo "Fertig: $("$PREFIX"/sbin/guacd -v 2>&1 | head -1)"
