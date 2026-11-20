# Desktop-Benchmarks auf der Projekt-Boat

Stand 20. November 2026. Die drei bisherigen Modellversuche sollten im geöffneten
Mousepad-Dokument den Liefertermin von Monday auf Friday ändern und daneben
`Handover-Friday.txt` speichern. In allen drei Versuchen fehlte die Kopie.
Das Original und die Kontrolldatei stimmten bytegenau, im Vordergrund kam
`draftAB\n` an und der aufgezeichnete Niri-Fokus blieb bis zur Prüfmarke
auf dem Entwurfsfenster. Die schnellste erfolgreiche Erledigung ist damit offen.

## Dokument bearbeiten und als Kopie speichern

Je Zeile ein echter T3-Modellauftrag auf `agent-first-os-20261120`, mit
frischem QEMU-Gast und bereits geöffnetem Dokument. Während der Messung lief
jeweils ein Gast. Der Cachezustand des Boat-Hosts wurde nicht gemessen.
Beide Modelle verwendeten niedrigen Reasoning-Aufwand. T3 bestätigt das
ausgeführte Modell und die bei der Delegation gesetzte Tier-Option.

| Versuch | Modell / angeforderter Tier | Auftrag bis unabhängiges Urteil | Modellantworten / äußere Toolaufrufe | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver gesamt inkl. Prüfung |
|---|---|---:|---:|---:|---:|---:|---:|
| edit-v1 | GPT-6.1 Terra / Priority | 25,103 s | 4 / 4 | 14,823 s | 9,479 s | 784 ms | 1.376 ms |
| edit-v2 | GPT-6.1 Terra / Priority | 27,848 s | 4 / 4 | 15,310 s | 11,974 s | 1.070 ms | 1.682 ms |
| edit-v3 | GPT-6.2 Sol / Ultrafast | 90,727 s | 10 / 10 | 70,888 s | 19,305 s | 2.469 ms | 3.646 ms |

Die Auftragszeit beginnt bei der T3-Anforderung. Die Codex-Zeiten beginnen
beim Start des ausführenden Turns. Die Gast-CLI läuft innerhalb des Drivers.
Die äußeren Tools umfassen unter anderem die private Boat-Übertragung, lokale
Bildansicht und Werkzeugzustellung. Die Zeit außerhalb dieser Tools enthält
Modell und Harness. Reine Anbieter-Inferenzzeit wurde nicht erfasst. Die
drei Läufe liefern keine Latenz bis zu einem erfolgreichen Dateieffekt.
[Zeitdefinitionen und Prüfmethode](./README.md) · [Einzelbelege mit Hashes und Zeitstempeln](./results.jsonl)

* **edit-v1, vor der Shortcut-Reparatur.** Der Stand war laut VM-Besitzer
  `837d687` mit der Korrektur für den privaten D-Bus. Terra bündelte
  acht Aktionen einschließlich Textersatz und Speichern in einem Brokerjob.
  Die Gast-CLI quittierte alle acht nach 394 ms. Der Ordner enthielt
  anschließend nur Original und Kontrolldatei. Der konkret wirkungslose
  Schritt lässt sich aus diesem Lauf nicht bestimmen.
* **edit-v2, reparierter Stand `59f58d9`.** Der Produkttest hatte auf diesem
  Driver eine echte Bearbeitung und das Speichern der geöffneten Datei belegt.
  Im Modellversuch quittierte der Broker sieben Schritte und verwarf den
  achten Tastendruck `ENTER` mit `Unknown XKB keysym`. Der zusammengesetzte
  Aufruf dauerte 461 ms im Gast. Laut Originalquittung auf Boat blieb der
  nachfolgende Screenshot aus. Der Transport versuchte trotzdem, ihn zu lesen.
  Das Modell erhielt `frame size NaN` und verlor die Information zum falschen
  Tastennamen. Die unabhängige Prüfung lief anschließend erfolgreich durch
  und bewertete den Auftrag als fehlgeschlagen.
* **edit-v3, derselbe Produkt-Driver.** Der Transport gab nun auch
  Teilquittungen ohne Bild weiter. Sol versuchte den Speicherdialog, den
  Menüaufruf und ein neues Dokument, insgesamt vier Eingabeversuche.
  Es sandte keinen Textersatz. Sieben Screenshots waren bytegleich, auch
  im vom VM-Besitzer geprüften Boat-Original. Der SHA-256 lautet
  `036dfc2d84a680bb4abe2266fab48cfe3e4c61b6b83820948df82cb108b5b1ef`.
  Die Fensterabfragen meldeten weiter nur das offene Übergabeprotokoll.
  Dieser Lauf erfasste die Kosten wiederholter Beobachtungen eines
  unveränderten Bilds. Ein Geschwindigkeitsvorteil eines Modells lässt sich
  aus den erfolglosen Einzelversuchen nicht ableiten.

## Datei im offenen GTK-Dateidialog umbenennen

Für diese Aufgabe soll das Modell `Invoice-draft.txt` in `Invoice-118.txt`
umbenennen. Der Prüfer erwartet den ursprünglichen Rechnungsinhalt unter dem
neuen Namen, das Fehlen des alten Namens, die exakten Bytes der Inventardatei
sowie den erhaltenen Vordergrundentwurf und Fokus.

**files-v1 hat die Startbedingung bisher verfehlt.** Der Driver konnte den
offenen GTK-Dateidialog nicht bestätigen. Es wurde kein Modellauftrag
gestartet. Für diese Familie gibt es damit bisher keine Modellzeit, keinen
gemessenen Dateieffekt und keine Modellstichprobe. Der VM-Besitzer prüft
die Bereitstellung der virtuellen Eingabegeräte am privaten Wayland-Seat.
