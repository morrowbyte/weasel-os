# weasel

`weasel` oder `weasel palette` öffnet die Tastaturpalette. Tippen filtert, Enter wählt, Escape schließt. Am Ende der Liste steht die interaktive Hilfe zu jeder Aktion. Agenten verwenden denselben Katalog und dieselbe Ausführung über CLI oder stdio MCP.

```nix
environment.systemPackages = [ (pkgs.callPackage ./packages/agent-desktop { }) ];
```

Niri-Bindung im vorhandenen `binds`-Block:

```kdl
Mod+Space { spawn "weasel" "palette"; }
```

## Vom Katalog zur Aktion

```sh
weasel help
weasel help window_set_value
weasel catalog
weasel run windows
weasel run window_focus '{"window_id":42}'
weasel run window_inspect '{"window_id":42}'
```

| Klasse | Ziel und Fokuswirkung |
| --- | --- |
| `read` | Liest Inventar, Status, AT-SPI-Elemente oder ein Bild der benannten privaten Sitzung. |
| `foreground` | Fokussiert ein vorhandenes Fenster oder die Niri-Arbeitsfläche seines aktiven Fensters. Ein App-Start kann den Fokus wechseln. |
| `app-background` | Ändert ein eindeutig benanntes editierbares AT-SPI-Objekt im sichtbaren Zielfenster. Der Actor prüft Handle, Kontext und erwarteten alten Text. Weasel prüft den neuen Text und vergleicht den Fensterfokus vor und nach dem Aufruf. |
| `private-workspace` | Eingaben, App-Start und Lebenszyklus einer benannten, separaten Wayland-Sitzung. Die Palette selbst nimmt während der Auswahl Tastaturfokus. Bei einem interaktiv gewählten Screenshot öffnet sie das Bild in imv. |

Für Niri-Fokus, Start oder direkte Bearbeitung braucht die Zielmaschine einen laufenden, betriebsbereiten `weasel-computer-use`-Dienst sowie den funktionierenden Sitzungsindikator. Die Paketinstallation allein richtet diese Dienste nicht ein. Weasel öffnet einen eigenen Task-Scope, bindet jede Aktion an `task_id` und frische Beobachtung und übernimmt Stopp, Übernahme und Freigabestatus des Actors. Ein Scope ist an seine Anzeige gebunden. Ein erneuter Desktopauftrag auf einer anderen Anzeige beginnt mit einer neuen Sequenz.

`window_set_value` erwartet `window_id`, `label`, `expected_text` und `text`. Das Label muss genau ein editierbares Feld treffen. Auch ein leeres Label ist bei eindeutiger Zuordnung gültig. Die direkte Rückleseprüfung unterstützt bis 511 Zeichen Zieltext. Eine fehlende AT-SPI-Unterstützung, ein unvollständiger Baum, unerwarteter Fokus oder unbestätigte Wirkung stoppen den Schritt. Niri-Arbeitsflächen ohne Fenster haben beim Actor kein Fokusziel. `app_launch` nutzt dessen Startziele Zed und Chrome. `actor_status` zeigt die tatsächlich vorhandenen Binärpfade unter `desktop_launch.pinned_binaries_present`. Ein fehlender Pin oder eine unbestätigte Fensterzuordnung beendet den Auftrag.

## Aktionen zusammensetzen

```sh
weasel compose --request-id notiz-001 - <<'JSON'
[
  {"action":"private_start","args":{"name":"notiz"}},
  {"action":"private_editor","args":{"name":"notiz"}},
  {"action":"private_text","args":{"name":"notiz","text":"Einkaufen: Haferflocken"}},
  {"action":"private_status","args":{"name":"notiz"}}
]
JSON
```

Der Editor ist GNU nano in einem eigens gestarteten foot-Fenster. Weasel wartet auf dessen einmalige Wayland-App-ID. Die private Sitzung verwendet standardmäßig die vom Workspace-Paket eingerichtete Isolation. Weitere private Anwendungen lassen sich mit `weasel-workspace --name notiz open -- PROGRAMM ARGUMENT …` öffnen. `weasel run private_stop '{"name":"notiz"}'` beendet die Sitzung einschließlich laufender Apps. Vorher benötigte Dokumente speichern.

Eine Sequenz validiert alle Aktionsnamen, Felder und Katalogtypen vor dem ersten Schritt. Laufzeitprüfungen führt der jeweilige Actor zusätzlich am konkreten Ziel aus. Der Rückgabewert enthält `steps`, `remaining`, `automatic_replay:false` und den Status `completed`, `partial` oder `stopped`. Der erste fehlerhafte Schritt enthält den verfügbaren Backend-Beleg. Bei einem Verbindungsverlust kann seine Wirkung unbekannt sein. Spätere Schritte entfallen. Bereits ausgeführte Aktionen bleiben wirksam. Private Tasten-, Text- und Zeigeraktionen liefern zunächst Eingabequittungen. Ein Bild oder das gespeicherte Dokument liefert den Nachweis der App-Wirkung.

`--request-id` bzw. MCP-`request_id` schützt eine Sequenz auch nach Verbindungsverlust und über CLI/MCP hinweg vor doppelter Einreichung. Die ID wird vor Ausführung einmalig im privaten `XDG_RUNTIME_DIR/weasel-sequences` verbraucht und bleibt dort auch nach Stop oder Fehler belegt. Beim erneuten Senden dieselbe ID verwenden. Eine neue ID bezeichnet einen neuen Auftrag. Ohne ID werden getrennte Aufrufe als eigenständige Aufträge behandelt.

`private_screenshot` gibt unter MCP einen PNG-Inhaltsblock zurück. Über die CLI stehen die Bilddaten in `images[0].data`. Die temporäre Bilddatei wird nach dem Lesen entfernt.

## Codex CLI, Crew und ChatGPT Desktop

Den absoluten Pfad des installierten Pakets verwenden. Bei Installation im Systemprofil:

```toml
[mcp_servers.weasel_os]
command = "/run/current-system/sw/bin/weasel"
args = ["mcp"]

[mcp_servers.weasel_desktop]
command = "/run/current-system/sw/bin/weasel"
args = ["desktop-mcp"]
```

Ein bestehender `weasel_desktop`-Eintrag für `weasel-computer-use mcp` kann direkt weiterverwendet werden. `weasel desktop-mcp` startet exakt diesen nativen Dienst als stdio-Proxy. Dort stehen die originalen Werkzeuge für Beobachtung, Bilder, Aktionen, Abbruch und Wiederaufnahme zur Verfügung. `weasel_os` stellt zusätzlich die Palette-Aktionen einzeln und das Tool `compose` bereit. Dessen `steps` entsprechen dem CLI-Array.

- **Codex CLI** liest den TOML-Eintrag aus dem aktiven Codex Home. Alternativ registriert `codex mcp add weasel_os -- /run/current-system/sw/bin/weasel mcp` den Einstieg. Eine frische Sitzung lädt die Konfiguration.
- **Crew/Morrowbyte** verwendet beim Codex-Provider ein konfigurierbares Codex-Binary und dessen `homePath` als `CODEX_HOME`. Den Eintrag im Home der tatsächlich ausgewählten Providerinstanz auf der Zielmaschine setzen. Der Adapter startet den Codex-App-Server und liest sein MCP-Inventar. Ein anderer Provider, ein anderer Host oder ein isoliertes Voice-Profil hat eine eigene Werkzeugauswahl.
- **ChatGPT Desktop für Linux** bringt im RPM ein eigenes `lib/chatgpt/resources/codex` mit. Der lokale Work-/Codex-Kontext benötigt den Eintrag in seinem aktiven Codex Home. Die hier geprüfte Schnittstelle ist das lokale Backend. Nutzung im normalen Web-Chat, ein Remote-Connector und der sichtbare Aufruf aus der Desktop-Oberfläche sind durch diesen Test nicht belegt. [Offizielle MCP-Konfiguration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

### Reproduzierbare Prüfung ohne GUI oder Modellturn

`checks/client.py` startet das angegebene echte Backend mit temporärem `CODEX_HOME`. Es initialisiert den App-Server, öffnet einen ephemeren Thread, liest `mcpServerStatus/list` und ruft `mcpServer/tool/call` für `weasel_os.catalog` auf. Es prüft alle zurückgelieferten Aktionsnamen. Falls der direkte Actor verfügbar ist, prüft es auch dessen Inventar. `--source` verwendet die Quellen aus diesem Verzeichnis. Ohne diese Option wird das installierte `weasel` verwendet.

```sh
python3 packages/agent-desktop/checks/client.py --source "$(command -v codex)"
```

Am 20.11.2026 lief derselbe Aufruf mit drei lokalen Binaries:

| Tatsächlicher Clientpfad | Geprüftes Backend | Ergebnis |
| --- | --- | --- |
| CLI aus dem Nix-Profil | Codex 0.162.0 | 17 Aktionen, 19 weasel_os-Tools, 12 direkte Actor-Tools |
| Codex-Binary des laufenden Crew-Prozesses | 0.169.1-morrowbyte.1 | gleiches Inventar, Katalogaufruf erfolgreich |
| Codex aus dem installierten ChatGPT-RPM 26.1111.21308 | 0.169.0-alpha.11.1 | gleiches Inventar, Katalogaufruf erfolgreich |

Diese drei Aufrufe verwendeten den Quellstand über stdio. Die Installation, die grafische Palette und die sichtbare Wirkung auf dem Desktop sind eigene Prüfungen auf dem Projekt-Boat.
