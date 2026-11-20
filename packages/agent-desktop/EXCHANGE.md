# Dateien auf einer privaten Arbeitsfläche

In der Tastaturpalette von `weasel` zuerst **Private Arbeitsfläche starten** wählen, der Sitzung einen Namen geben und einen privaten Austauschordner freigeben. Das Verzeichnis muss dem aktuellen Benutzer gehören und beim ersten Gebrauch leer sein. In der privaten Sitzung liegt es unter `/exchange`. Die drei Dateiaktionen der Palette verwenden dieselben Aktionen wie `weasel run`, `weasel compose` und die einzelnen MCP-Werkzeuge.

| Aktion | Argumente zusätzlich zum Workspace-Namen `name` | Ergebnis in `steps[i].result` |
| --- | --- | --- |
| `private_exchange_list` | – | `entries` mit sortierten sichtbaren Namen und `truncated` |
| `private_exchange_lstat` | `filename` | `name`, `inode`, `bytes` und `mode` des Verzeichniseintrags |
| `private_exchange_rename_noreplace` | `source`, `destination` | `source`, `destination` und `renamed: true` nach erfolgreichem Systemaufruf |

Alle drei Aktionen liefern die Broker-Kennung `session` und die Auftragskennung `job`. In einer Sequenz bindet der erste erfolgreiche Exchange-Schritt die folgenden Schritte desselben Workspace-Namens an diese Sitzung. Bei getrennten Aufrufen kann die vorherige Kennung als optionales Argument `session` mitgegeben werden. Nach einem Neustart erhält der Workspace eine neue Kennung. Eine laufende Sitzung ohne Austauschfreigabe lehnt alle drei Aktionen ab.

~~~sh
weasel run private_exchange_list '{"name":"rechnung"}'

weasel compose --request-id rechnung-118-1 - <<'JSON'
[
  {"action":"private_exchange_lstat","args":{"name":"rechnung","filename":"Entwurf.txt"}},
  {"action":"private_exchange_rename_noreplace","args":{"name":"rechnung","source":"Entwurf.txt","destination":"Rechnung-118.txt"}},
  {"action":"private_exchange_lstat","args":{"name":"rechnung","filename":"Rechnung-118.txt"}}
]
JSON
~~~

Zulässig sind druckbare Dateinamen mit bis zu 255 UTF-8-Bytes direkt im Freigabeverzeichnis. Unterverzeichnispfade und Namen mit führendem Punkt sind gesperrt. Ein Inventar prüft bis zu 1024 Einträge und liefert höchstens 128 Namen. Bei `truncated: true` kann es weitere Einträge geben. Metadaten eines symbolischen Links beschreiben den Link selbst. Die Dateiaktionen öffnen keine Dateiinhalte oder Linkziele.

Die Umbenennung prüft den Quelleintrag auf eine reguläre Datei und benennt den Namen mit Linux `renameat2(RENAME_NOREPLACE)` atomar um. Existiert das Ziel bereits, schlägt die Aktion fehl. Andere Prozesse mit Schreibrechten im freigegebenen Ordner können Einträge zwischen Metadatenprüfung und Umbenennung ändern. Der Beleg bestätigt die Namensoperation. Bei verlorenem Beleg lässt sich der Zustand mit Inventar und Metadaten erneut prüfen.

Nach `private_cancel_all` bleiben die beiden Leseaktionen verfügbar. Zum Umbenennen müssen private Eingaben freigegeben und die manuelle Bedienung der Ansicht beendet sein. `private_stop` beendet die Sitzung einschließlich ihres Dateizugangs. Die Aktionen benötigen keinen Wechsel des menschlichen Desktopfokus.

## Laufzeiten und Belege

CLI und MCP verbinden sich für jeden Schritt direkt mit dem privaten Unix-Socket. Der Broker prüft Sitzung und Zulassung pro Schritt. Sein Dateiworker bleibt innerhalb der Sitzung für weitere Aufrufe bereit. Der Broker beendet einen unterbrochenen Worker und verwirft dessen Verbindung. Die nächste Aktion erhält bei Bedarf einen neuen Worker.

`steps[i].duration_ns` misst den gesamten ausgeführten Exchange-Schritt im Produkt. Erfolgreiche Schritte führen die folgenden Werte unter `result.timing_ns`, bestätigte Brokerfehler unter `receipt.timing_ns`. Die Intervalle sind in Nanosekunden angegeben und liegen teilweise ineinander.

| Feld | Messgrenzen |
| --- | --- |
| `client_admission` | Laufzeitverzeichnisse und Epochendatei prüfen, unter der Lebenszyklussperre verbinden und Anfrage kodieren |
| `client_roundtrip` | Schreiben der Anfrage bis zum dekodierten Beleg im Client |
| `broker_dispatch` | Beginn der Broker-Verarbeitung bis zum Ergebnis nach Freigabe des aktiven Auftrags |
| `worker_roundtrip` | Auftrag im Broker vorbereiten bis zum dekodierten Worker-Beleg, bei Abbruch einschließlich Bereinigung |
| `worker_start` | Nur bei erfolgreichem Neustart, bis der Worker seine Bereitschaft meldet. In `worker_roundtrip` enthalten |
| `file_operation` | Eintritt in die Worker-Operation bis zur Rückkehr oder zum Fehler. Enthält Python-Validierung, Dateizugriffe und beim Umbenennen die Sperre und erneute Epochenprüfung |

Wenn eine Phase gar nicht begonnen hat oder der zugehörige Beleg fehlt, kann das Feld fehlen. `file_operation` ist eine verstrichene Zeit einschließlich möglicher Wartezeit. Bei `weasel run` und `weasel compose` mit Exchange-Schritten nennt das Ergebnis außerdem `cli_timing_ns.python_entry_to_execute` (ab erstem Python-Zeitstempel einschließlich Importen und Einlesen der Anfrage) und `cli_timing_ns.execute` (Validierung, optionale Reservierung der Request-ID und alle Schritte). Der externe Aufrufer kann Wrapper- und Interpreterstart sowie Ausgabe und Prozessende über die gesamte Prozesslaufzeit erfassen.
