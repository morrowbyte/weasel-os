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

Nach `private_cancel_all` bleiben die beiden Leseaktionen verfügbar. Zum Umbenennen müssen private Eingaben freigegeben und die manuelle Bedienung der Ansicht beendet sein. `private_stop` beendet die Sitzung einschließlich ihres Dateizugangs. Jeder ausgeführte Exchange-Schritt trägt `duration_ns` für die Laufzeit im Produkt einschließlich Workspace-CLI und Broker-Rundreise. Die Aktionen benötigen keinen Wechsel des menschlichen Desktopfokus.
