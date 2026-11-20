# weasel-agent

Der x86_64-Linux-Gast startet einen Niri-Desktop mit Codex CLI, ChatGPT Desktop für Linux und Crew. Der VM-Benutzer heißt `operator`. Die VM verwendet ihre eigene beschreibbare Disk. Anmeldung bei einem Anbieter erfolgt im gewählten Client.

## VM starten

Im ausgecheckten weasel-os-Repository auf einem grafischen x86_64-Linux-Rechner mit Nix, Flakes und KVM:

```sh
nix build --no-write-lock-file .#nixosConfigurations.weasel-agent.config.system.build.vm
./result/bin/run-weasel-agent-vm
```

Die QEMU-VM verwendet eine virtuelle Grafikausgabe. Weston stellt dafür einen Monitor mit Software-Rendering bereit, Niri verwaltet die Benutzerfenster. Für einen eigenen NixOS-Rechner lässt sich das exportierte Modul `nixosModules.agent-first` mit dessen vorhandener Hardware-, Benutzer- und Anmeldekonfiguration kombinieren.

| Taste | Aktion |
| --- | --- |
| Super+Space | Systemaktion suchen |
| Super+D | Installierte Anwendung starten |
| Super+Return | Terminal öffnen |
| Super+E | Mousepad öffnen |
| Super+F1 | Tastenkürzel anzeigen |
| Super+Bild↑ / Bild↓ | Arbeitsfläche wechseln |
| Alt+Escape | Aktuellen Desktop-Auftrag abbrechen und private Eingaben sperren |
| Strg+Alt+Escape | Die Steuerung des persönlichen Desktops übernehmen |
| Super+Umschalt+A | Den persönlichen Desktop nach Sitzungsstart oder Übernahme für neue Aufträge freigeben |

Nach der Anmeldung gibt Super+Umschalt+A den persönlichen Desktop frei. Die gleiche Aktion ist im Terminal als `weasel resume-desktop` verfügbar. Das erste Beispiel benötigt kein Modellkonto:

```sh
weasel catalog
weasel run windows
```

Ein Agent erreicht die gemeinsamen Aktionen über den lokalen MCP-Server `weasel_os`. Für fenstergebundene Beobachtungen und die direkte Steuerung steht `weasel_desktop` bereit. Beide sind in der Systemvorgabe unter `/etc/codex/config.toml` eingetragen. Maßgeblich für die Bedienung ist das lokale Backend in der laufenden Sitzung. Eine Cloud-Sitzung oder ein entfernter Crew-Provider läuft auf einem anderen Rechner.

## Im Hintergrund ein Dokument bearbeiten

Für eine bereits geöffnete Anwendung kann der Actor ein von der App bereitgestelltes Textfeld anhand der Fenster-ID lesen, gezielt ändern und erneut lesen. Fenster, Arbeitsfläche und Fokusereignisse dienen zur Prüfung der Wirkung. Die Möglichkeiten eines vorhandenen Fensters hängen von der App und den angebotenen Wayland- und Accessibility-Schnittstellen ab.

Für eine neue Aufgabe kann eine private Sitzung beliebige installierte native Wayland-Apps mit eigenem Fokus, Home, Bus und Clipboard öffnen:

```sh
weasel-workspace --name bericht start --exchange "$HOME/bericht-austausch"
weasel-workspace --name bericht open -- mousepad /exchange/notiz.txt
weasel-workspace --name bericht status
```

Das Statusresultat nennt Titel und App-ID. Nach Prüfung des Zielfensters lassen sich Text und Speichern als Folge ausführen:

```sh
printf '%s\n' '[["text","Notiz aus der privaten Sitzung"],["key","Ctrl+s"]]' |
  weasel-workspace --name bericht batch notiz-speichern
cat "$HOME/bericht-austausch/notiz.txt"
weasel-workspace --name bericht stop
```

Ein neues Austauschverzeichnis beginnt leer. Programme mit Netzzugriff benötigen bei Sitzungsstart `--network`. Einmalaufträge erhalten eine eindeutige ID. Nach einem Abbruch zeigt ein frischer Blick in Datei oder Fenster, welche Schritte bereits wirksam waren.

## Reproduzierbare Abnahme

```sh
nix build -L --no-write-lock-file .#checks.x86_64-linux.agent-first-desktop
```

Der Test bootet einen Wegwerfgast, erfasst Niris Fokusereignisse und prüft ein gespeichertes Mousepad-Dokument sowie den gleichzeitig eingegebenen Vordergrundtext. Er prüft die getrennte Sitzung und den AT-SPI-Pfad eines vorhandenen Fensters. Die Ausgaben der einzelnen Client-Backends werden zusätzlich im Aktionspaket geprüft.

[Gemeinsamer Katalog](../../packages/agent-desktop/README.md) · [Private Anwendungen](../../packages/agent-desktop/PRIVATE-APPS.md) · [Workspace-Befehle und Datenübergabe](../../packages/agent-workspace/README.md) · [Unterstützte Clientpakete](../../packages/agent-clients/README.md)
