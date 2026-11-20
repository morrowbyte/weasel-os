# Reale Desktopaufträge im NixOS-Testgast

Jeder Versuch erhält einen frisch gebooteten Gast aus demselben gebauten
`.#checks.x86_64-linux.agent-first-desktop.driver`, eine neue private
Offline-Arbeitsfläche und eine bereits geöffnete App. Die gemessene Aufgabe
beginnt bei der Übergabe an einen frischen Modellthread. Maschinenboot und
Fixture-Aufbau gehören zur Vorbereitung. Der Benchmark verwendet ausschließlich
eigene Beispieldaten und emulierte Vordergrundtastatur im Gast.

## Zwei Nutzeraufträge

* **edit** — „Ändere im offenen Übergabeprotokoll den Liefertermin von Monday
  auf Friday. Speichere eine Kopie als Handover-Friday.txt im selben Ordner.
  Der Entwurf im Vordergrund soll ungestört bleiben.“
* **files** — „Benenne im offenen Dateidialog Invoice-draft.txt in
  Invoice-118.txt um. Lass die anderen Dateien und den Entwurf im Vordergrund
  unverändert.“

Die erste Aufgabe läuft im Texteditor Mousepad, die zweite im GTK-Dateidialog
dieser App. Im Gast liegt der Ordner unter `/exchange` im privaten Desktop.
Die Dateiaufgabe prüft die Entfernung des alten Namens und den Erhalt des
Rechnungsinhalts. Beim Editieren müssen die ursprünglichen Bytes, Leerzeilen,
Referenznummer, Schlussumbruch und eine weitere Datei erhalten bleiben.
Die unabhängige Prüfung vergleicht den gesamten Ordner bytegenau. Der Austauschmarker
des Workspace-Brokers ist ausgenommen. Der Nutzerentwurf beginnt mit `draft`;
QEMU hält Shift über die Modellaktionen hinweg. Vor und nach den Aktionen
erhält Foot je einen Buchstaben. Der Prüfer erwartet `draftAB\n` sowie
durchgängigen Niri-Fokus bis zum abschließenden Titelereignis. Diese Eingaben
messen keinen Menschen.

## Privater Lauf

Auf der freigegebenen Projekt-Boat als normaler Benutzer, jeweils mit einem
noch nicht existierenden Laufverzeichnis unter einem privaten Elternverzeichnis.
Der Transport verwendet einen lokalen Unix-Socket mit serieller Ausführung über
den eigenen NixOS-Testdriver. Der Modellzugang akzeptiert nur Beobachtung,
Tasten, Text, Klick und Scrollen auf der vorbereiteten Arbeitsfläche.

```sh
umask 077
bench=/home/user/crew/tests/agent-bench/bench.py
parent=$(mktemp -d /tmp/agent-bench.XXXXXX)
run=$parent/attempt
# DRIVER ist der vom VM-Besitzer qualifizierte Store-Pfad. Als Boat-Hintergrundjob starten.
python3 "$bench" start "$DRIVER" "$run" edit
```

Sobald der Socket erreichbar ist, aus einem separaten Aufruf auf derselben Boat
eine vom Modell gewählte Aktion oder Folge senden. Ein Schritt erhält automatisch
den Arbeitsflächennamen. Die Produkt-CLI validiert Argumente und verbraucht die
Anfrage-ID genau einmal. Nach unklarer Antwort zuerst das Ergebnis beobachten.

```sh
python3 "$bench" compose "$run" view-01 <<'JSON'
[{"action":"private_status"}]
JSON
python3 "$bench" finish "$run"
```

`private_screenshot` legt das ursprüngliche PNG mit der Anfrage-ID im
privaten Laufverzeichnis ab. Das Modell darf Screenshots seines Versuchs lesen.
Es bedient die App ausschließlich über die vorbereiteten Aktionen. Ein
Modellversuch endet mit `finish`, auch wenn sein Inhalt falsch ist. Der
Driver schließt diesen Gast. Ein neuer Versuch startet mit neuem Verzeichnis.
Ein Skriptlauf zur Qualifikation wird getrennt von Modellläufen protokolliert.

## Messwerte

* Der T3-Laufbeleg liefert Auftragseingang, Modellname, angeforderten Tier,
  belegte Ausführung und Toolaktivität. End-to-End ist die Zeit zwischen
  Auftragsübergabe und Eingang der unabhängigen Prüfung; für die Nutzerlatenz
  bis zur Antwort kann zusätzlich das Turn-Ende ausgewiesen werden. Startmodus,
  Modellrunden (falls sichtbar), Toolaufrufe und Fehlversuche einzeln angeben.
* `events.jsonl` enthält Anfragen, Antworten und zwei Boat-Zeitstempel.
  `host_call_ms` umfasst seriellen Transport, Gastarbeit und Antwortaufbereitung
  im Driver; die Zeit für die Boat-Toolzustellung kommt hinzu.
  `guest_end_ns - guest_start_ns` misst die Produkt-CLI inklusive App-Eingabe.
  Diese Intervalle überlappen und dürfen nicht addiert werden.
* `effect_ns` ist der erste monotone Gastzeitstempel, an dem der unabhängige
  Prüfer exakt den erwarteten Ordner vorfand, bei einem Abtastintervall von
  20 ms zuzüglich Scheduling. Die Differenz zum Beginn des ersten oder eines
  zeitlich zuordenbaren Speicheraufrufs verwendet dieselbe Gastuhr. Aus einer
  Eingabequittung allein folgt kein Dateieffekt. Bei fehlendem Zeitstempel
  bleibt die Wirkungslatenz unbekannt. Die Restzeit außerhalb der gemessenen
  Gastaufrufe enthält Modell-, Orchestrierungs- und Transportzeit.
* Der Gaststart ist frisch, die geöffnete App ist warm. Hostcache, Hostname,
  Driver-Store-Pfad und konkurrierende QEMU-Prozesse zum Lauf festhalten. Für
  Vergleiche immer nur einen Gast aktivieren. Retry, ungültiges Ergebnis oder
  fehlende Lauftelemetrie explizit im Vergleich angeben.

Die Logs und PNGs liegen nur im privaten Laufverzeichnis. Nach Übernahme der
benötigten aggregierten Belege entfernt der Besitzer sein Laufverzeichnis.

`python3 tests/agent-bench/trace.py /privater/pfad/zum/einzelnen-rollout.jsonl`
wertet genau den zugeordneten Modelllauf aus. Es zählt verschiedene
`token_usage_record.response_id` bis zur Rückkehr des unabhängigen Prüfergebnisses.
Die Differenz zwischen Codex-Auftragsstart und Prüfrückkehr benutzt eine
einzige Uhr. Parallele äußere Werkzeugaufrufe gehen als zeitliche Vereinigung
ein. Die Restzeit enthält Modell und Harness. Die Zustellung vor dem
Codex-Auftragsstart wird über die T3-Zeitstempel separat angegeben. Private
Rollouts bleiben am Ursprungsort. Für einen abgebrochenen Lauf kann der
Auswerter auf die ID der letzten belegten Toolantwort begrenzt werden.
