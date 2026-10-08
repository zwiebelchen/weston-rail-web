#!/usr/bin/env python3
"""
weston-rail-web: Browser-Zugang zu den RemoteApps von weston-mirror RAIL.

  Browser ──WebSocket──► weston-rail-web ──TCP 4822──► guacd ──RDP──► weston-rail-broker

Anmeldung und Sitzungsstart laufen über die Verwaltungsschnittstelle des
Brokers (/run/weston-rail-broker.sock, nur root). Der Dienst läuft deshalb
als root und braucht ausser Python nichts.

MIT-Lizenz (wie weston-mirror). Die Browser-Seite benutzt
guacamole-common-js (Apache-2.0), siehe static/vendor/.
"""

import argparse
import base64
import traceback
import hashlib
import http.cookies
import json
import os
import secrets
import socket
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BROKER_SOCKET = "/run/weston-rail-broker.sock"
APPS_CONF = "/etc/weston-rail/apps.conf"
GUACD = ("127.0.0.1", 4822)
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SESSION_IDLE = 8 * 3600

# ---------------------------------------------------------------- Broker


def broker(request: str, extra: str = "") -> str:
    """Ein Kommando an die Verwaltungsschnittstelle des Brokers schicken."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(60)
    try:
        s.connect(BROKER_SOCKET)
        s.sendall((request + "\n" + extra).encode())
        s.shutdown(socket.SHUT_WR)
        out = b""
        while True:
            d = s.recv(4096)
            if not d:
                break
            out += d
        return out.decode("utf-8", "replace")
    finally:
        s.close()


def broker_auth(user: str, password: str) -> bool:
    try:
        return broker("AUTH %s" % user, password + "\n").startswith("OK")
    except OSError:
        return False


def broker_connect(user: str, app: str) -> int:
    """Einmaligen Port fuer eine Browser-Sitzung des Users anfordern."""
    answer = broker("CONNECT %s app=%s shell=%s" % (user, app, CONFIG.shell))
    for line in answer.splitlines():
        if line.startswith("PORT "):
            return int(line.split()[1])
    raise RuntimeError(answer.strip() or "keine Antwort vom Broker")


# ------------------------------------------------------------ apps.conf


def load_apps():
    """Veroeffentlichte Programme lesen (dieselbe Datei wie der Broker)."""
    apps, workspace, cur, section = [], {"name": "Work Resources"}, None, ""
    try:
        with open(CONFIG.apps_conf, encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line[0] in "#;":
                    continue
                if line.startswith("["):
                    section = line
                    if line == "[app]":
                        cur = {}
                        apps.append(cur)
                    else:
                        cur = None
                    continue
                if "=" not in line:
                    continue
                key, value = (p.strip() for p in line.split("=", 1))
                if section == "[workspace]":
                    workspace[key] = value
                elif cur is not None:
                    cur[key] = value
    except OSError:
        pass
    apps = [a for a in apps if a.get("name") and a.get("command")]
    for a in apps:
        a.setdefault("title", a["name"].capitalize())
    return workspace, apps


def find_app(name):
    for a in load_apps()[1]:
        if a["name"].lower() == name.lower():
            return a
    return None


ICON_DIRS = ("/usr/share/icons", "/usr/local/share/icons")
ICON_SIZES = ("64", "48", "128", "96", "256", "32")


def desktop_icon_name(command):
    exe = os.path.basename(command.split()[0].strip("\"'"))
    for d in ("/usr/share/applications", "/usr/local/share/applications"):
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".desktop"):
                continue
            icon = run_exec = None
            try:
                with open(os.path.join(d, entry), encoding="utf-8",
                          errors="replace") as f:
                    for line in f:
                        if line.startswith("Icon="):
                            icon = line[5:].strip()
                        elif line.startswith("Exec="):
                            run_exec = os.path.basename(
                                line[5:].strip().split()[0])
            except OSError:
                continue
            if icon and run_exec == exe:
                return icon
    return None


def icon_file(app):
    """PNG oder SVG zu einem Programm finden (wie weston-rail-feed)."""
    name = app.get("icon") or desktop_icon_name(app.get("command", ""))
    if not name:
        return None
    if name.startswith("/"):
        return name if os.path.exists(name) else None
    themes = []
    for base in ICON_DIRS:
        themes += [os.path.join(base, "hicolor")]
        try:
            themes += [os.path.join(base, t) for t in sorted(os.listdir(base))
                       if t != "hicolor"]
        except OSError:
            pass
    for ext in ("png", "svg"):
        for theme in themes:
            for size in ICON_SIZES:
                for path in ("%s/%sx%s/apps/%s.%s" % (theme, size, size, name, ext),
                             "%s/apps/%s/%s.%s" % (theme, size, name, ext)):
                    if os.path.exists(path):
                        return path
            path = "%s/scalable/apps/%s.svg" % (theme, name)
            if ext == "svg" and os.path.exists(path):
                return path
        path = "/usr/share/pixmaps/%s.%s" % (name, ext)
        if os.path.exists(path):
            return path
    return None


# -------------------------------------------------------------- Sessions

SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def session_new(user):
    token = secrets.token_urlsafe(32)
    with SESSIONS_LOCK:
        SESSIONS[token] = {"user": user, "seen": time.time()}
    return token


def session_user(token):
    with SESSIONS_LOCK:
        s = SESSIONS.get(token or "")
        if not s:
            return None
        if time.time() - s["seen"] > SESSION_IDLE:
            del SESSIONS[token]
            return None
        s["seen"] = time.time()
        return s["user"]


# ------------------------------------------------------------- WebSocket

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocket:
    """Minimale RFC-6455-Umsetzung fuer Textrahmen.

    Gelesen wird über den gepufferten Leser des HTTP-Servers (rfile):
    Teile der ersten Rahmen liegen nach dem Handschlag bereits in dessen
    Puffer und wären beim direkten Lesen vom Socket verloren.
    """

    def __init__(self, sock, rfile=None):
        self.sock = sock
        self.rfile = rfile
        self.buf = b""
        self.last_error = None
        self.lock = threading.Lock()

    def send(self, text: str):
        data = text.encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack(">H", n)
        else:
            header.append(127)
            header += struct.pack(">Q", n)
        with self.lock:
            self.sock.sendall(bytes(header) + data)

    def close(self):
        try:
            with self.lock:
                self.sock.sendall(b"\x88\x00")
        except OSError:
            pass

    def _read(self, n):
        if self.rfile is not None:
            data = self.rfile.read(n)
            if not data or len(data) < n:
                raise ConnectionError
            return data
        while len(self.buf) < n:
            d = self.sock.recv(65536)
            if not d:
                raise ConnectionError
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """Naechste Nachricht; None bei Verbindungsende."""
        try:
            b1, b2 = self._read(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else b"\0\0\0\0"
            payload = bytearray(self._read(length))
            if masked:
                for i in range(length):
                    payload[i] ^= mask[i % 4]
            if opcode == 0x8:
                return None
            if opcode == 0x9:          # ping
                with self.lock:
                    self.sock.sendall(b"\x8a" + bytes([len(payload)]) + bytes(payload))
                return ""
            return bytes(payload).decode("utf-8", "replace")
        except (OSError, ConnectionError, struct.error) as exc:
            self.last_error = exc
            return None


# ----------------------------------------------------------------- guacd


def guac_encode(*parts):
    return (",".join("%d.%s" % (len(p.encode()), p) for p in parts) + ";").encode()


class GuacdConnection:
    """Verbindung zu guacd: Handshake fuehren, danach Rahmen durchreichen."""

    def __init__(self, params):
        self.sock = socket.create_connection(GUACD, 10)
        self.buf = ""
        self.params = params

    def _instruction(self):
        while ";" not in self.buf:
            d = self.sock.recv(65536)
            if not d:
                raise ConnectionError("guacd hat die Verbindung geschlossen")
            self.buf += d.decode("utf-8", "replace")
        raw, self.buf = self.buf.split(";", 1)
        parts, i = [], 0
        raw = raw.strip()
        while i < len(raw):
            j = raw.find(".", i)
            if j < 0:
                break
            n = int(raw[i:j])
            parts.append(raw[j + 1:j + 1 + n])
            i = j + 1 + n + 1
        return parts

    def handshake(self, width, height, dpi):
        self.sock.sendall(guac_encode("select", "rdp"))
        args = self._instruction()
        names = args[1:]
        self.sock.sendall(guac_encode("size", str(width), str(height), str(dpi)))
        self.sock.sendall(guac_encode("audio"))
        self.sock.sendall(guac_encode("video"))
        self.sock.sendall(guac_encode("image", "image/png", "image/jpeg"))
        values = [self.params.get(n, "") for n in names]
        if names and names[0].startswith("VERSION_"):
            values[0] = names[0]
        self.sock.sendall(guac_encode("connect", *values))

    def pump_to(self, ws, log=None):
        """Alles von guacd an den Browser weiterreichen."""
        rest = ""
        while True:
            d = self.sock.recv(65536)
            if not d:
                break
            text = d.decode("utf-8", "replace")
            ws.send(text)
            if not log:
                continue
            # Fehlermeldungen von guacd mitlesen, damit sie im Protokoll stehen
            rest += text
            while ";" in rest:
                instruction, rest = rest.split(";", 1)
                if ".error," in instruction or instruction.startswith("5.error"):
                    log("guacd meldet: " + instruction.strip())
            if len(rest) > 65536:
                rest = ""

    def send(self, text):
        self.sock.sendall(text.encode())

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "weston-rail-web"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if CONFIG.verbose:
            print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def log(self, msg):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)

    # ---- Hilfsfunktionen

    def user(self):
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie["wrw"].value if "wrw" in cookie else None
        return session_user(token)

    def send_json(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- Routen

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(STATIC, "index.html"),
                                  "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self.send_file(os.path.join(STATIC, "app-default.svg"),
                                  "image/svg+xml")
        if path == "/api/me":
            user = self.user()
            return self.send_json({"user": user} if user else {"user": None})
        if path == "/api/apps":
            if not self.user():
                return self.send_json({"error": "nicht angemeldet"}, 401)
            workspace, apps = load_apps()
            return self.send_json({
                "workspace": workspace.get("name", "Work Resources"),
                "apps": [{"name": a["name"], "title": a["title"]} for a in apps],
            })
        if path.startswith("/api/icon/"):
            if not self.user():
                return self.send_error(401)
            app = find_app(path[len("/api/icon/"):])
            icon = icon_file(app) if app else None
            if not icon:
                return self.send_file(os.path.join(STATIC, "app-default.svg"),
                                      "image/svg+xml")
            return self.send_file(icon, "image/svg+xml" if icon.endswith(".svg")
                                  else "image/png")
        if path == "/ws":
            return self.websocket()
        if path.startswith("/static/"):
            rel = os.path.normpath(path[len("/static/"):]).lstrip("/")
            full = os.path.join(STATIC, rel)
            if not full.startswith(STATIC) or not os.path.isfile(full):
                return self.send_error(404)
            types = {".js": "application/javascript", ".css": "text/css",
                     ".svg": "image/svg+xml", ".png": "image/png",
                     ".ico": "image/x-icon"}
            return self.send_file(full, types.get(os.path.splitext(full)[1],
                                                  "application/octet-stream"))
        self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send_json({"error": "ungültige Anfrage"}, 400)

        if path == "/api/login":
            user = str(data.get("user", "")).strip()
            password = str(data.get("password", ""))
            # Domaenenschreibweisen wie bei mstsc zulassen
            if "\\" in user:
                user = user.split("\\")[-1]
            if not user or not password:
                return self.send_json({"error": "Benutzername und Kennwort angeben"}, 400)
            if not broker_auth(user, password):
                self.log("Anmeldung von '%s' abgelehnt (%s)" % (user, self.client_address[0]))
                return self.send_json({"error": "Benutzername oder Kennwort ist falsch"}, 401)
            token = session_new(user)
            self.log("Anmeldung von '%s' (%s)" % (user, self.client_address[0]))
            return self.send_json({"user": user}, 200, {
                "Set-Cookie": "wrw=%s; Path=/; HttpOnly; SameSite=Strict%s"
                              % (token, "" if CONFIG.insecure_cookie else "; Secure"),
            })
        if path == "/api/logout":
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            if "wrw" in cookie:
                with SESSIONS_LOCK:
                    SESSIONS.pop(cookie["wrw"].value, None)
            return self.send_json({"ok": True}, 200,
                                  {"Set-Cookie": "wrw=; Path=/; Max-Age=0"})
        self.send_error(404)

    # ---- WebSocket: Browser <-> guacd

    def websocket(self):
        user = self.user()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        app = (query.get("app") or [""])[0].rstrip("?")

        def number(name, default, lowest, highest):
            """Nur Ziffern übernehmen: guacamole-common-js hängt beim
            Verbinden ein '?' an die Adresse, sonst käme z. B. '96?' an
            und guacd würde die Verbindung verwerfen."""
            value_raw = (query.get(name) or [""])[0]
            raw = ""
            for c in value_raw:            # nur die führenden Ziffern
                if not c.isdigit():
                    break
                raw += c
            value = int(raw) if raw else default
            return str(max(lowest, min(highest, value)))

        width = number("width", 1280, 640, 8192)
        height = number("height", 800, 480, 8192)
        dpi = number("dpi", 96, 48, 480)
        key = self.headers.get("Sec-WebSocket-Key")
        if not user or not key:
            return self.send_error(401)
        if not find_app(app):
            return self.send_error(404)

        accept = base64.b64encode(
            hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()

        ws = WebSocket(self.connection, self.rfile)
        guacd = None
        try:
            port = broker_connect(user, app)
            self.log("Sitzung für '%s': %s über 127.0.0.1:%d" % (user, app, port))
            guacd = GuacdConnection({
                "hostname": "127.0.0.1",
                "port": str(port),
                "username": user,
                "password": "",
                "security": "tls",
                "ignore-cert": "true",
                "resize-method": "display-update",
                "enable-wallpaper": "true",
                "width": width, "height": height, "dpi": dpi,
            })
            guacd.handshake(width, height, dpi)
        except Exception as exc:                       # noqa: BLE001
            self.log("Sitzung für '%s' fehlgeschlagen: %s" % (user, exc))
            ws.send("5.error,%d.%s,3.519;" % (len(str(exc)), str(exc)))
            ws.close()
            if guacd:
                guacd.close()
            return

        def to_browser():
            try:
                guacd.pump_to(ws, self.log)
            except Exception as exc:                   # noqa: BLE001
                self.log("Richtung guacd->Browser beendet: %r" % (exc,))
            finally:
                ws.close()

        t = threading.Thread(target=to_browser, daemon=True)
        t.start()
        try:
            count = 0
            while True:
                msg = ws.recv()
                if msg is None:
                    self.log("Browser hat die Verbindung geschlossen "
                             "(%d Nachrichten empfangen%s)"
                             % (count, ", Grund: %r" % (ws.last_error,)
                                if ws.last_error else ""))
                    break
                if msg:
                    count += 1
                    guacd.send(msg)
        except Exception as exc:                       # noqa: BLE001
            self.log("Richtung Browser->guacd beendet: %r" % (exc,))
            if CONFIG.verbose:
                self.log(traceback.format_exc())
        finally:
            guacd.close()
            self.log("Sitzung für '%s' beendet (%s)" % (user, app))
        self.close_connection = True


def main():
    global CONFIG
    parser = argparse.ArgumentParser(description="Browser-Zugang zu weston-mirror RAIL")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--listen", default="127.0.0.1",
                        help="Adresse (Standard 127.0.0.1, also hinter einem Reverse Proxy)")
    parser.add_argument("--apps-conf", default=APPS_CONF)
    parser.add_argument("--shell", default="kiosk", choices=("kiosk", "desktop"),
                        help="kiosk: Anwendung bildschirmfüllend; desktop: mit Fensterverwaltung")
    parser.add_argument("--insecure-cookie", action="store_true",
                        help="Cookie ohne Secure-Flag (Test ohne HTTPS)")
    parser.add_argument("-v", "--verbose", action="store_true")
    CONFIG = parser.parse_args()

    if os.geteuid() != 0:
        print("weston-rail-web muss als root laufen (Zugriff auf den Broker).")
        return 1
    server = ThreadingHTTPServer((CONFIG.listen, CONFIG.port), Handler)
    print("[%s] weston-rail-web auf %s:%d, Shell: %s"
          % (time.strftime("%H:%M:%S"), CONFIG.listen, CONFIG.port, CONFIG.shell),
          flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
