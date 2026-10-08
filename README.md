# weston-rail-web

Web-Zugang zu den RemoteApps von
[weston-mirror RAIL](https://github.com/zwiebelchen/weston-mirror), im Stil
von RD Web Access: Anmeldung im Browser, Liste der veröffentlichten
Programme, Start im Browser-Tab.

Die Anwendung selbst läuft weiter in der ganz normalen Session des Users auf
dem RAIL-Server – mit Laufwerken, Druckern und allem, was ein mstsc-Nutzer
auch bekommt. Der Browser ist nur ein weiterer Client.

## Stand

Durchstich mit `guacd` gemacht; Oberfläche noch nicht begonnen.

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

## Lösungsweg

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
