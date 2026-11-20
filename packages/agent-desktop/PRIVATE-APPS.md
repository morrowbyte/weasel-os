# Programme auf privaten Arbeitsflächen

In `weasel palette` zuerst **Private Arbeitsfläche starten** wählen.
Name, Austauschordner und Netzwerkzugang lassen sich separat einstellen.
Eine neue Sitzung startet standardmäßig offline. Falls ein Austauschordner
gewählt wurde, können die Programme der Sitzung unter `/exchange` darauf
zugreifen. Die Anforderungen an den Host-Ordner und die Rechte beim erneuten
Start sind im [Workspace-Handbuch](../agent-workspace/README.md) beschrieben.

Danach **Programm auf privater Arbeitsfläche öffnen** wählen und denselben
Arbeitsflächennamen eingeben. Unter **Ausführbares Programm** steht der
Programmname aus dem Host-PATH oder ein ausführbarer Nix-Store-Pfad.
Programmargumente gehören einzeln in die JSON-Liste, zum Beispiel
`["--app-id=privater-bericht", "--title=Privater Bericht"]` für `foot`.
Ohne Argumente das Feld leer lassen. Das nächste Feld erwartet die exakte
Wayland-App-ID, im Beispiel `privater-bericht`, oder bei X11 die Klasse aus
`WM_CLASS`, etwa `XTerm` für xterm.

Der MCP-Aufruf `private_open` verwendet dieselben Felder wie die CLI.
Eine Folge kann den Start der Sitzung und das Öffnen der App verbinden.

```sh
weasel compose - <<'JSON'
[
  {"action":"private_start","args":{"name":"bericht"}},
  {"action":"private_open","args":{"name":"bericht","program":"foot",
    "argv":["--app-id=privater-bericht","--title=Privater Bericht"],
    "wait_window":"privater-bericht"}}
]
JSON
```

Für einen Austauschordner `exchange` als Host-Pfad und für Netzwerkzugang
`network` als JSON-Boolean bei `private_start` setzen. Programm und
`argv` werden vom Workspace-Broker direkt gestartet. Der Broker meldet den
Prozessstart und das fokussierte Fenster in einem Auftrag. `private_open`
weist bereits vorhandene Fenster mit derselben App-ID zurück. Bei Programmen
mit fester App-ID bietet sich eine eigene benannte Sitzung an. Der Fensterbeleg
enthält die private Sway-Fenster-ID und die zum Warten verwendete Kennung.
Für X11 enthält er zusätzlich `x11_class`, `x11_instance` und die XID in `x11_window`. Die Prozess-ID stammt aus dem privaten
PID-Namespace. Die Zuordnung des Fensters erfolgt über die App-ID und den
Fensterbestand vor dem Start. Für parallele Starts eignet sich eine je App-Start
eindeutige App-ID.

Zum manuellen Weiterarbeiten **Private Arbeitsfläche als Fenster anzeigen**
in der Palette wählen und den Sitzungsnamen angeben. In der Shell öffnet
`weasel-workspace --name bericht view` dieselbe Ansicht. Nach dem Wechsel in ein
anderes Niri-Fenster kann der Agent über die privaten Katalogaktionen in der
laufenden App weiterarbeiten. Beim Schließen der Ansicht bleiben Dokument,
Prozess und private Fenster-ID erhalten.

Wenn der Start unbelegt bleibt, zuerst mit **Private Arbeitsfläche prüfen**
oder `private_status` nachsehen. Der Prozess kann trotz fehlendem Fensterbeleg
bereits laufen. Die Folge stoppt am Fehler und wird nicht automatisch wiederholt.

**Private Eingaben abbrechen und sperren** benutzt das bestehende
`weasel-workspace cancel-all` für alle privaten Arbeitsflächen dieses Benutzers.
Offene Programme bleiben erhalten. **Private Eingaben für neue Aufträge
freigeben** benutzt `weasel-workspace resume`. **Private Sitzung samt ihren
Apps beenden** schließt die Programme der ausgewählten Sitzung.
