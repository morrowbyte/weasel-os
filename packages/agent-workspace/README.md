# weasel-workspace

Ein pro Benutzer benannter Wayland-Desktop für Hintergrundaufträge mit optional dauerhaftem Home.
Einbindung über `pkgs.callPackage ./packages/agent-workspace {}`. Der Host braucht
Linux mit Benutzer-Namespaces, Bubblewrap und ein eigenes privates
`XDG_RUNTIME_DIR`. Der Paket-Wrapper bringt die Laufzeitprogramme mit.

## Ein Fenster öffnen

```sh
weasel-workspace --name bericht start --exchange "$HOME/bericht-austausch"
weasel-workspace --name bericht open -- foot
weasel-workspace --name bericht wait-window foot
weasel-workspace --name bericht text 'Hallo aus dem privaten Desktop'
weasel-workspace --name bericht screenshot bericht.png
weasel-workspace --name bericht stop
```

`foot` steht beispielhaft für ein installiertes natives Wayland-Programm.
`open` löst das ausführbare Programm über den Host-PATH zu einem Nix-Store-Pfad auf.
Es startet direkt mit dem angegebenen Argumentvektor. Shell-Syntax wird nur von
einer explizit geöffneten Shell interpretiert. Der zurückgegebene PID gehört
zum privaten Namespace. `wait-window` wartet auf ein fokussiertes Fenster mit
exakt passender Wayland-App-ID oder X11-Klasse (z. B. `XTerm`). `status` zeigt App-IDs, Titel, Fokus, Ausgabe,
Rechte, laufenden Auftrag und die Eingabesperre.

## Ein privates App-Profil wiederverwenden

```sh
weasel-workspace --name notizen start --persistent
weasel-workspace --name notizen open -- mousepad --disable-server
# In Mousepad unter /home/workspace/notiz.txt speichern.
weasel-workspace --name notizen stop
weasel-workspace --name notizen start --persistent
weasel-workspace --name notizen open -- mousepad --disable-server /home/workspace/notiz.txt
```

Apps können Dokumente und Einstellungen wie gewohnt in ihrem privaten
`$HOME` speichern. Das Home liegt auf dem Host unter
`${XDG_DATA_HOME:-$HOME/.local/share}/weasel-workspace/profiles/<Name>`.
Der Profilpfad muss aus echten Verzeichnissen bestehen. Die drei verwalteten
Verzeichnisse ab `weasel-workspace` gehören dem Benutzer und haben Modus 0700.
Bubblewrap bindet das geöffnete Profilverzeichnis ein und hält es für die
gesamte Sitzung exklusiv belegt. Der Austauschordner bleibt separat.

Nach `stop` oder einem Neustart des Rechners wählt `start --persistent`
dasselbe Home wieder aus. Ein frischer Start ohne Schalter erhält ein leeres,
flüchtiges Home. Ein erneuter Start einer *laufenden* Sitzung meldet deren
tatsächlichen Modus in `persistent`. Eine laufende flüchtige Sitzung lässt
sich erst nach `stop` auf dauerhaft umstellen. `cancel-all` sperrt weiterhin
die Eingabe und lässt gespeicherte Dateien bestehen.

Programme im selben benannten Profil können die darin gespeicherten Daten
lesen, einschließlich dort bewusst angelegter App-Anmeldungen. Verwende
getrennte Namen für getrennte Vertrauensbereiche. Für weitere Apps genügt
derselbe Startvertrag. Nach jedem frischen Start entstehen ein eigener Bus,
ein neuer Compositor und eine neue `session`-Kennung. Fenster-IDs gelten
innerhalb dieser Sitzung. Ein Client identifiziert ein Fenster nach einem
Neustart über das Paar aus `session` und Fenster-ID.

Die eigenständige Gastprobe `tests/agent-first/persistent-profile.py` läuft
als Desktop-Benutzer im Niri-Testgast mit einem fokussierten Operatorfenster.
Sie speichert und öffnet ein Dokument in Mousepad und prüft die gespeicherten
Bytes und den Niri-Fokusstrom. Für einen Reboottest zuerst
`python3 persistent-profile.py --name profile-test-reboot --phase save`
ausführen und nach dem Gastneustart denselben Aufruf mit `--phase reopen`.

## Dieselbe App am Desktop bedienen

Mit `weasel-workspace --name bericht view` öffnest du die laufende private
Arbeitsfläche als normales Wayland-Fenster auf deinem Desktop. Du siehst
den gesamten privaten Sway-Ausgang und bedienst seine Apps mit Maus und
Tastatur. Bei Größenänderungen bleibt das 16:9-Bild proportional. Freie
Ränder gehören zur Ansicht und senden keine Klicks an die App. Das Fenster
trägt den Sitzungsnamen im Titel. **Manuelle Eingabe**
zeigt an, dass der Broker die Bedienung freigegeben hat. Solange du dort
arbeitest, weist er neue Agenteneingaben in dieser Sitzung zurück.

Wechsle in ein anderes Niri-Fenster. Die Ansicht bleibt sichtbar und der
Agent kann im Hintergrund mit `text`, `key` und den übrigen Workspace-Aktionen
in derselben privaten App weiterarbeiten. Schließen beendet nur die Ansicht.
`cancel-all` trennt auch eine offene Ansicht. Nach `resume` kannst du sie
ausdrücklich neu öffnen. Host und Workspace behalten ihre eigenen Zwischenablagen.

## Befehle

Der optionale globale Schalter `--name NAME` steht vor dem Befehl. Standard
ist `default`. Erfolgreiche Befehle liefern JSON auf stdout. Fehler liefern
einen Exitcode ungleich null. Bereits belegte Screenshot-Zielpfade werden
zurückgewiesen.

| Befehl | Wirkung |
| --- | --- |
| `start [--persistent] [--exchange DIR] [--network]` | Startet die Sitzung oder meldet die vorhandene Sitzung mit ihrem Profilmodus und ihren tatsächlichen Rechten. |
| `status` | Prüft den privaten Compositor und listet seine Fenster. |
| `open -- PROGRAM [ARG ...]` | Startet das Programm im privaten Home. |
| `view` | Öffnet eine interaktive Ansicht der laufenden Sitzung bis zum Schließen des Fensters. |
| `wait-window APP_ID` | Wartet höchstens 30 Sekunden auf passenden Fokus. |
| `screenshot DATEI.png` | Schreibt das vollständige private Bild als neue Datei auf dem Host. |
| `clipboard-read` | Liest explizit bis zu 10 KiB UTF-8 aus der privaten Zwischenablage. |
| `clipboard-write` | Setzt explizit bis zu 10 KiB UTF-8 aus stdin in der privaten Zwischenablage. |
| `key Ctrl+Shift+s`, `text TEXT` | Sendet einen XKB-Tastennamen mit optionalen Modifikatoren bzw. Unicode-Text an das fokussierte private Fenster. |
| `click X Y [left\|middle\|right]` | Klickt in absoluten Pixelkoordinaten, standardmäßig links. |
| `move X Y` | Bewegt den privaten Zeiger zur angegebenen Position. |
| `drag START_X START_Y END_X END_Y [left\|middle\|right]` | Drückt am Start die gewählte Taste (Standard links), zieht bis zum Ziel und lässt dort los. Bei Abbruch lässt der Helfer an der erreichten Position los. |
| `scroll X Y up\|down\|left\|right [SCHRITTE]` | Scrollt 1 bis 32 Rastschritte an dieser Position. |
| `batch JOB` | Liest ein JSON-Array von Aktionsarrays aus stdin. |
| `cancel JOB` | Bricht diesen aktuell laufenden Auftrag der benannten Sitzung ab. |
| `cancel-all` | Sperrt Eingabe für alle Sitzungen dieses Benutzer-Runtime-Verzeichnisses und bricht aktive Broker-Eingaben ab. |
| `resume` | Öffnet die benutzerweite Eingabe ausdrücklich für neue Aufträge. |
| `stop` | Beendet den PID-Namespace einschließlich gestarteter Apps und wartet auf die Freigabe seiner Lebensdauersperre. |

Die Ausgabe hat 1280 × 720 Pixel bei Skalierung 1. Koordinaten beginnen links
oben bei (0, 0). Ein Screenshot darf auch im gesperrten Zustand erfolgen.

`key` verwendet eine virtuelle US-PC-Tastatur im privaten Seat. So erhält
eine Anwendung für `Ctrl+s` die üblichen Tastencodes für Strg und S.
Explizites `Shift` bleibt beim Haupttastendruck aktiv. Für XKB-Symbole,
die auf der Karte weder direkt noch mit Shift erreichbar sind, trägt der
Helfer das gewünschte Symbol für diesen Aufruf auf F24 ein. `text` sendet
Unicode in Wayland-Fenster über wtype. Für ein X11-Fenster liest der
Texteingabehelfer die Tastaturbelegung des privaten X-Servers und prüft
vor jedem Zeichen den X-Eingabefokus gegen die von Sway gemeldete Fenster-ID.

Gängige Schreibweisen wie `Enter`, `Esc`, `PgUp`, `PgDn`, `Ins`, `Del`,
`Bksp`, `Spacebar` und `ArrowLeft` sind auch in Großbuchstaben und mit
Modifikatoren gültig. Ein unbekannter Tastenname verwirft die gesamte
Aktionsfolge vor der ersten Eingabe.

## Einmalaufträge und Abbruch

```sh
printf '%s\n' '[["open","foot"],["wait-window","foot"],["text","Hallo"]]' |
  weasel-workspace --name bericht batch fenster-01
```

Ein Batch akzeptiert die Aktionsnamen `open`, `wait-window`, `text`, `key`,
`click`, `scroll`, `clipboard-read` und `clipboard-write`. Im Batch nimmt
`clipboard-write` den Text als einzelnes Argument an. Alle Aktionen werden
vor der ersten Ausführung geprüft. Maximal 64 Aktionen, 64 KiB Anfrage und
30 Sekunden Gesamtlaufzeit. Pro Desktop läuft höchstens eine Aktionsfolge.
Konkurrierende Eingaben melden „busy“. Eine verlorene Client-Verbindung
beendet die laufende Folge. Der erste Fehler beendet die Folge.

Die CLI verbraucht die angegebene Job-ID vor dem Senden. Eine zweite
Übermittlung derselben ID im selben benannten Workspace schlägt auch nach
einem Neustart fehl. Die privaten Belege liegen im Benutzer-Runtime-Verzeichnis
bis zu dessen Entfernung. Bei unbekanntem Ausgang zuerst den Desktop prüfen.

`cancel-all` schließt zuerst die gemeinsame Zulassung und widerruft deren
Generation. Jeder aktive Broker überwacht diese Datei zusätzlich zur direkten
Abbruchnachricht. Vor jeder neuen Teilaktion prüft er die Zulassung erneut.
Eine verspätete Anfrage mit alter Generation bleibt auch nach `resume`
ungültig. Ein erfolgreicher Abbruch beendet noch laufende Eingabehelfer.
Bereits zugestellte Ereignisse, Dateiänderungen und geöffnete Apps bleiben
bestehen. `stop` beendet auch diese Prozesse. `cancel-all` meldet unerreichbare
laufende Broker einzeln und mit Fehlerstatus. Ein separat gestarteter Actor
auf dem menschlichen Desktop hat seinen eigenen Abbruchweg.

## Isolations- und Datengrenzen

- Sway läuft headless mit Software-Renderer. Benutzer-, Mount-, PID-, IPC- und
  Netzwerk-Namespace, Prozessumgebung, Home, Runtime, D-Bus, Clipboard,
  App-Konfiguration und Singleton-Sockets gehören zur privaten Sitzung.
  Der Broker nimmt nur seinen vererbten lokalen Kontrollsocket entgegen.
- Vor der ersten Anwendung meldet der private Seat eine virtuelle Tastatur
  und einen virtuellen Zeiger an. Ein Helfer hält beide Geräte bis zum Ende
  der Sitzung, damit Anwendungen sie auch zwischen einzelnen Aktionen sehen.
- Der Nix-Store ist schreibgeschützt lesbar. Es gibt ein minimales privates
  `/dev` und ein privates `/proc`. Host-Home, Host-D-Bus, Display-Sockets,
  Host-Konfigurationsprofile, Audiogeräte und physische Eingabegeräte werden
  nicht eingebunden. Für X11-Anwendungen startet Sway bei Bedarf Xwayland
  im selben Namespace. Der Broker setzt `DISPLAY` anhand des Unix-Sockets
  im privaten `/tmp/.X11-unix`. wlroots übergibt Xwayland die lokalen
  Listener; Zugang und Zwischenablage gehören zu dieser privaten Sitzung.
- Programme derselben Sitzung teilen sich Home, Bus und Display. Ein weiterer
  Aufruf eines Singletons kann ein vorhandenes Fenster **dieser Sitzung**
  verwenden. Eine separate Sitzung erhält ein eigenes Profil. Ausdrücklich
  gestartete Programme und ihre Kindprozesse können im Rahmen derselben
  Sitzungsrechte arbeiten. Das Paket setzt keine anwendungsspezifischen
  CPU-/RAM-Limits und isoliert Apps innerhalb einer Sitzung nicht voneinander.
- `--exchange DIR` erstellt ein eigenes privates Verzeichnis oder übernimmt
  bei Erstverwendung ein leeres benutzereigenes Verzeichnis mit Modus 0700.
  Danach erkennt die CLI es am Marker `.weasel-workspace-exchange`.
  Unter `/exchange` erhalten alle Apps der Sitzung Lese-, Schreib- und
  Löschrechte für den Inhalt. Lege freigegebene Dateien dort ab. Der Host
  behält das Verzeichnis nach `stop`. Nur gewöhnliche Austauschdateien
  hineinlegen. Eingebrachte Sockets und andere IPC-Endpunkte teilen deren
  Fähigkeiten ebenfalls. Ohne diese Option gibt es keine Datenfreigabe.
- Der Standardbetrieb ist offline. `--network` verbindet den eigenen
  Netzwerk-Namespace über slirp4netns (IPv4, DNS, Host-Loopback-Weiterleitung
  deaktiviert). Dies erlaubt ausgehenden Netzwerkverkehr einschließlich
  erreichbarer LAN-Dienste. Host-Auflösung und die Netzanbindung des Hosts
  beeinflussen erreichbare Ziele. Der Host-Netzwerk-Namespace und dessen
  abstrakte Unix-Sockets werden nicht geteilt. Beim Ende des Workspace
  beendet sich der Netzhelfer über einen Prozessdeskriptor.
