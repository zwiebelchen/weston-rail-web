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
	libcairo2-dev libjpeg62-turbo-dev libpng-dev libossp-uuid-dev ghostscript

# FreeRDP 2 ist Pflicht: mit dem experimentellen FreeRDP-3-Weg von
# guacamole-server 1.6 meldet sich der Geraete-Kanal (rdpdr) nicht an -
# dann gibt es in der Sitzung weder Laufwerk noch Drucker (nachgemessen).
# Debian 13 hat kein freerdp2-dev mehr; dann wird FreeRDP 2 nach
# /opt/freerdp2 gebaut, getrennt vom FreeRDP 3 des Systems (das Weston
# benutzt). Beides stoert sich nicht.
FREERDP2_PREFIX=${FREERDP2_PREFIX:-/opt/freerdp2}
FREERDP2_VERSION=${FREERDP2_VERSION:-2.11.7}

if apt-get install -y --no-install-recommends freerdp2-dev 2>/dev/null; then
	echo "FreeRDP 2 aus der Distribution"
elif pkg-config --exists freerdp2 2>/dev/null; then
	echo "FreeRDP 2 bereits vorhanden"
else
	echo "FreeRDP 2 fehlt in dieser Distribution - baue $FREERDP2_VERSION nach $FREERDP2_PREFIX"
	apt-get install -y --no-install-recommends \
		cmake git libssl-dev libx11-dev libxext-dev libxcursor-dev \
		libxi-dev libxrandr-dev libxinerama-dev libxv-dev libxkbcommon-dev \
		zlib1g-dev libusb-1.0-0-dev
	mkdir -p "$BUILD"
	cd "$BUILD"
	[ -d freerdp2-src ] || git clone --depth 1 --branch "$FREERDP2_VERSION" \
		https://github.com/FreeRDP/FreeRDP.git freerdp2-src
	cd freerdp2-src
	# FreeRDP 2.11 ist aelter als GCC 14: was dort inzwischen Fehler sind
	# (unvertraegliche Zeigertypen u. a.), waren damals Warnungen
	rm -rf build
	cmake -B build -S . \
		-DCMAKE_BUILD_TYPE=Release \
		-DCMAKE_C_FLAGS="-Wno-incompatible-pointer-types -Wno-int-conversion -Wno-implicit-function-declaration -Wno-error" \
		-DCMAKE_INSTALL_PREFIX="$FREERDP2_PREFIX" \
		-DWITH_SERVER=OFF -DWITH_SHADOW=OFF -DWITH_PROXY=OFF \
		-DWITH_CLIENT_SDL=OFF -DWITH_X11=OFF -DWITH_WAYLAND=OFF \
		-DWITH_CUPS=OFF -DWITH_PULSE=OFF -DWITH_ALSA=OFF \
		-DWITH_FFMPEG=OFF -DWITH_SWSCALE=OFF -DWITH_MANPAGES=OFF
	cmake --build build -j"$(nproc)"
	cmake --install build
	echo "$FREERDP2_PREFIX/lib" > /etc/ld.so.conf.d/freerdp2.conf
	ldconfig
	export PKG_CONFIG_PATH="$FREERDP2_PREFIX/lib/pkgconfig:$FREERDP2_PREFIX/lib64/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
	export LDFLAGS="-Wl,-rpath,$FREERDP2_PREFIX/lib${LDFLAGS:+ $LDFLAGS}"
	pkg-config --exists freerdp2 || { echo "FreeRDP 2 Bau fehlgeschlagen"; exit 1; }
	echo "FreeRDP 2 gebaut: $(pkg-config --modversion freerdp2)"
fi

VERSION=${VERSION:-1.5.5}

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
RDP_CPPFLAGS=$(pkg-config --cflags freerdp2 2>/dev/null ||
	pkg-config --cflags freerdp3 winpr3 2>/dev/null || true)
# der Bau von guacamole-server muss FreeRDP 2 finden, auch unter /opt
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
# root: guacd legt die umgeleiteten Laufwerke in den Home-Verzeichnissen
# der Benutzer an; lauscht nur auf 127.0.0.1
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now guacd
sleep 1
systemctl --no-pager --lines=5 status guacd || true
echo
echo "Fertig: $("$PREFIX"/sbin/guacd -v 2>&1 | head -1)"
