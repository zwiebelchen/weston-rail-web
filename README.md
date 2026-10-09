# weston-rail-web

Web-Zugang zu den RemoteApps von
[weston-mirror RAIL](https://github.com/zwiebelchen/weston-mirror), im Stil
von RD Web Access: Anmeldung im Browser, Liste der veröffentlichten
Programme, Start im Browser-Tab.

Die Anwendung selbst läuft weiter in der ganz normalen Session des Users auf
dem RAIL-Server – mit Laufwerken, Druckern und allem, was ein mstsc-Nutzer
auch bekommt. Der Browser ist nur ein weiterer Client.

## Stand

Funktioniert Ende zu Ende (im Container getestet): Anmeldung, Programmliste,
Symbole, WebSocket-Sitzung mit Bild vom Server. Die Oberfläche ist am
RD-Web-Client von Windows Server orientiert: Anmeldekarte, Kopfleiste mit
Startseite und Konto, Kacheln bzw. Liste unter dem Namen des Arbeitsbereichs,
Verbindungsdialog, Tabs für laufende Anwendungen.

## Installation

Auf dem RAIL-Server (dort laufen Broker und Weston):

```bash
git clone https://github.com/zwiebelchen/weston-rail-web.git
cd weston-rail-web
sudo ./install.sh
sudo systemctl enable --now weston-rail-web
```

### Von Hand starten (Test und Fehlersuche)

Der Dienst liegt nach der Installation unter
`/usr/local/lib/weston-rail-web/weston-rail-web.py` und läuft als root
(er spricht mit dem Broker und legt die Laufwerksordner an):

```bash
# zum Testen direkt erreichbar, ohne HTTPS, mit Protokoll:
sudo /usr/local/lib/weston-rail-web/weston-rail-web.py \
    --listen 0.0.0.0 --port 8085 --insecure-cookie -v

# im Betrieb hinter Caddy/nginx (Vorgabe):
sudo /usr/local/lib/weston-rail-web/weston-rail-web.py --listen 127.0.0.1 --port 8081

# mit Mitschnitt der ersten 20 Anweisungen je Richtung (Fehlersuche):
sudo /usr/local/lib/weston-rail-web/weston-rail-web.py \
    --listen 0.0.0.0 --port 8085 --insecure-cookie -v --trace 20
```

Vorher den Dienst stoppen, falls er schon läuft:
`sudo systemctl stop weston-rail-web`.

Alle Optionen:

| Option | Bedeutung |
|---|---|
| `--listen ADRESSE` | Adresse (Vorgabe `127.0.0.1`; `0.0.0.0` für Zugriff ohne Proxy) |
| `--port N` | Port (Vorgabe 8081) |
| `--insecure-cookie` | Anmelde-Cookie ohne `Secure`-Kennzeichen, nötig ohne HTTPS |
| `--shell desktop\|kiosk` | Fensterverwaltung (Vorgabe) oder eine Anwendung bildschirmfüllend |
| `--apps-conf DATEI` | andere Allowlist (Vorgabe `/etc/weston-rail/apps.conf`) |
| `--drive-dir NAME` | Ordner im Home, der als Laufwerk erscheint (Vorgabe `Browser-Dateien`) |
| `--drive-name NAME`, `--printer-name NAME` | Namen von Laufwerk und Drucker |
| `--gfx` | Grafikkanal in guacd einschalten (Vorgabe: aus) |
| `--trace N` | die ersten N Anweisungen je Richtung protokollieren |
| `-v` | jede Anfrage protokollieren |

Dauerhafte Optionen für den Dienst setzt man in der Unit:

```bash
sudo systemctl edit weston-rail-web
#   [Service]
#   ExecStart=
#   ExecStart=/usr/local/lib/weston-rail-web/weston-rail-web.py --listen 127.0.0.1 --port 8081 --shell kiosk
sudo systemctl restart weston-rail-web
```

Der Dienst lauscht auf `127.0.0.1:8081` und gehört hinter einen Reverse Proxy
mit TLS (Caddy); ohne HTTPS zum Testen mit `--insecure-cookie` starten.
Optionen: `--listen`, `--port`, `--shell kiosk|desktop`, `--apps-conf`, `-v`.

Caddy:

```
apps.example.org {
	reverse_proxy 127.0.0.1:8081
}
```

Voraussetzungen: Python 3, ein laufender `weston-rail-broker` mit `AUTH`
und `CONNECT` – und `guacd`.

### guacd gibt es in Debian 13 nicht mehr

Debian hat `guacamole-server` im November 2024 aus dem Archiv entfernt, es
gibt also kein Paket. `install.sh` baut es deshalb bei Bedarf selbst
(`tools/install-guacd.sh`), nur mit RDP-Unterstützung, und richtet einen
systemd-Dienst ein:

```bash
sudo ./tools/install-guacd.sh      # auch einzeln aufrufbar
```

**FreeRDP 2 ist Pflicht** (Debian 13: Paket `freerdp2-dev`), das Skript
baut damit guacamole-server 1.5.5. Mit dem experimentellen FreeRDP-3-Weg
von 1.6.0 meldet sich der Geräte-Kanal (rdpdr) nicht an – dann gibt es in
der Sitzung **weder Laufwerk noch Drucker**. Nachgemessen:

```
# 1.5.5 auf FreeRDP 2
guacd:  Support for static channel "rdpdr" loaded. / Connected to RDPDR 1.12
weston: RDP rdpdr: drive 'GUACFS' (device 1) from client 'Guacamole RDP'

# 1.6.0 auf FreeRDP 3: keine dieser Zeilen
```

Eine Stolperfalle, die das Skript umgeht: `configure` prüft mit einem
eigenen Testprogramm, ob die FreeRDP-Strukturen einen `context` haben
(FreeRDP 3), setzt dabei aber den Header-Suchpfad nicht. Unter Debian
liegen die Header in `/usr/include/freerdp3`, der Test schlägt deshalb
fehl, und der Bau scheitert später mit
`'freerdp' has no member named 'input'`. Das Skript setzt `CPPFLAGS`
entsprechend.

## Aufbau

```
Browser ──WebSocket──► weston-rail-web ──TCP 4822──► guacd ──RDP──► weston-rail-broker
                       (Anmeldung, App-Liste)                        (Session pro User)
```

Von Apache Guacamole (Apache-2.0) werden zwei Teile mitbenutzt:

- **`guacd`**: spricht RDP (über FreeRDP) und übersetzt den Bildschirm in das
  Guacamole-Protokoll. Kann RemoteApp (`remote-app`).
- **`guacamole-common-js`**: die fertige Browser-Seite dieses Protokolls
  (Canvas, Tastatur, Maus, Zwischenablage).

Nicht übernommen wird die Java-Weboberfläche von Guacamole mit Tomcat,
Datenbank und eigener Benutzerverwaltung: Benutzer (PAM/AD) und
veröffentlichte Programme (`/etc/weston-rail/apps.conf`) hat der RAIL-Server
bereits.

## Ergebnis des Durchstichs

Getestet mit `guacd` 1.3 gegen `weston-rail-broker`
(`tools/guac-smoketest.py`):

- Die RDP-Verbindung kommt zustande, `guacd` meldet
  „Support for RAIL (RemoteApp) registered“, der Broker meldet
  `user … authenticated`, `session … started`, `connection … handed to the
  session`.
- **Danach bricht `guacd` ab.** Grund: Der Broker leitet jeden Client nach
  der Anmeldung mit einem Einmal-Token auf die Session um (Server
  Redirection PDU). In `guacamole-server` (geprüft bis 1.5.5) gibt es dafür
  keine Unterstützung – es wertet nur `load-balance-info` aus. mstsc und
  xfreerdp folgen der Umleitung, `guacd` nicht.

## Zweiter Befund: guacd kann kein HiDef-RAIL

Mit der Übergabe ohne Umleitung (Broker-Kommando `CONNECT`, inzwischen
eingebaut) kommt `guacd` bis zur Aktivierung der Sitzung und scheitert dann:

```
weston: HiDef-RAIL is required for RAIL.
guacd:  Connection closed.
```

`rdprail-shell` (aus WSLg) beherrscht nur die moderne RAIL-Variante, bei der
jedes Fenster über den Grafikkanal (EGFX) als eigene Fläche übertragen wird.
`guacamole-server` setzt dagegen nur klassisches RAIL und **unterstützt EGFX
überhaupt nicht** (geprüft in 1.5.5: kein Grafikkanal-Code, keine
Einstellung dafür). Das ist in Guacamole kein kleiner Patch, sondern ein
eigenes Protokollmodul.

## Lösungsweg (angepasst)

Für den Browser also **kein RAIL**, sondern eine Kiosk-Sitzung: Weston mit
`kiosk-shell`, in der genau eine Anwendung bildschirmfüllend läuft. Das ist
gewöhnliches RDP, womit `guacd` bestens zurechtkommt, und sieht im
Browser-Tab aus wie eine einzelne Anwendung.

1. `weston-rail-web` meldet den Benutzer per PAM an (Dienst `weston-rail`,
   also auch AD-Konten).
2. Es fordert beim Broker über `CONNECT` eine Sitzung für diesen Benutzer an
   – künftig wahlweise als Kiosk-Sitzung mit einem Programm aus
   `/etc/weston-rail/apps.conf`.
3. `guacd` verbindet sich auf den einmaligen Port, der Browser bekommt das
   Bild über das Guacamole-Protokoll.

Dieselben Sessions wie bei mstsc lassen sich im Browser damit nicht
weiterbenutzen (RAIL dort, Kiosk hier). Drucken, Zwischenablage und
Dateiübertragung bleiben möglich.

## Alter Lösungsweg (umgesetzt, aber nicht ausreichend)

Statt `guacd` zu patchen, bekommt der Broker eine Übergabe **ohne**
Umleitung, die ohnehin nützlich ist:

1. `weston-rail-web` meldet den Benutzer selbst per PAM an (derselbe
   PAM-Dienst `weston-rail`, also auch AD-Konten).
2. Es fragt den Broker über dessen Verwaltungsschnittstelle nach einem
   einmaligen, nur lokal erreichbaren Endpunkt für diesen Benutzer
   (neues Kommando `CONNECT <user>`). Der Broker startet die Session, falls
   nötig, und reicht die erste Verbindung an dieser Stelle direkt an die
   Weston-Instanz weiter.
3. `guacd` verbindet sich dorthin und startet die gewünschte RemoteApp.

Damit bleibt die Anmeldung an einer Stelle, der Browser-Nutzer landet in
derselben Session pro User wie ein mstsc-Nutzer, und Guacamole bleibt
unverändert.

## Wie es zusammenspielt

- **Anmeldung**: Der Dienst fragt den Broker (`AUTH`), der prüft per PAM –
  also dieselben Konten wie bei mstsc, inklusive Active Directory.
- **Programme**: aus `/etc/weston-rail/apps.conf`, derselben Allowlist.
  Symbole kommen aus der `.desktop`-Datei bzw. dem Icon-Theme.
- **Sitzung**: Der Dienst fordert beim Broker per `CONNECT <user> app=…`
  einen einmaligen lokalen Port an; der Broker startet dafür eine eigene
  Weston-Instanz mit `kiosk-shell` (eine Anwendung bildschirmfüllend) oder
  `desktop-shell` (mit Fensterverwaltung). `guacd` verbindet sich dorthin,
  der Dienst reicht das Guacamole-Protokoll per WebSocket an den Browser.

Browser-Sitzungen und mstsc-Sitzungen laufen getrennt nebeneinander: RAIL
für mstsc, gewöhnliches RDP für den Browser.

## Dateien und Drucken

Wie beim RD-Web-Client von Microsoft:

- **Dateien**: Die Sitzung bekommt ein umgeleitetes Laufwerk. Es zeigt auf
  `~/Browser-Dateien` des Benutzers auf dem Server (Option `--drive-dir`)
  und erscheint in der Sitzung unter `~/RDP-Laufwerke/Browser`. Im Browser
  lädt man Dateien über das Pfeil-Symbol in der Kopfleiste hoch oder zieht
  sie einfach auf das Fenster.
- **Drucken**: In der Sitzung steht ein Drucker „Browser-Drucker“ bereit
  (Option `--printer-name`). Ein Druckauftrag kommt als **PDF im Browser**
  an und wird heruntergeladen – wie der „Virtuelle Remotedesktop-Drucker“
  bei Microsoft. Dafür braucht `guacd` Ghostscript (installiert
  `tools/install-guacd.sh` mit).

`guacd` läuft als root, weil es die Laufwerksordner in den
Home-Verzeichnissen der Benutzer anlegt; es lauscht nur auf 127.0.0.1.

## Werkzeuge

- `tools/guac-smoketest.py` – spricht das Guacamole-Protokoll direkt mit
  `guacd` und zeigt, was zurückkommt. Beispiel:

```bash
guacd -b 127.0.0.1 -L debug -f &
python3 tools/guac-smoketest.py hostname=127.0.0.1 port=3389 \
    username=BENUTZER password=PASSWORT security=tls ignore-cert=true \
    remote-app='||firefox'
```

## Lizenz

MIT (wie weston-mirror). Guacamole-Bestandteile stehen unter Apache-2.0 und
werden nur benutzt, nicht kopiert.
