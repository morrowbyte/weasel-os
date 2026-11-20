# Desktop-Benchmarks auf der Projekt-Boat

Stand 20. November 2026. GPT-6.2 Sol / Ultrafast hat dieselbe Rechnung in
zwei frischen Gästen erfolgreich umbenannt. Vom Modellauftrag bis zur
unabhängigen Bestätigung vergingen im GTK-Dateidialog **65,274 s** und bei
freier Werkzeugwahl mit den paketierten Dateiaktionen **19,425 s**. Die
Bearbeitung des Liefertermins im offenen Mousepad-Dokument ist weiterhin
offen. Auf dem Driver des GUI-Laufs speicherte GPT-6.1 Terra / Priority
die Kopie unter dem richtigen Namen mit dem Text `Fridayy.`. In allen drei
Läufen blieben der Vordergrundentwurf und sein Fokus erhalten.

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

### Erster Dokumentversuch auf Driver v7

**Die Kopie wurde unter dem richtigen Namen gespeichert. Ein einzelnes
zusätzliches y blieb stehen.** Die vollständigen Testdaten mit dem Satz
`Delivery is due on Fridayy.` ergeben exakt den SHA-256 der gespeicherten
Kopie. Terra hatte die Wortauswahl korrigiert, drei Zeichen gelöscht und
erneut gespeichert. Im letzten App-Bild lag der Cursor neben dem doppelten y.
Das Original, die Inventardatei, der geschriebene Entwurf und der Fokus
bestanden die unabhängige Prüfung. Der vollständige Sollinhalt trat im
Dateibeobachter nicht auf.

| Versuch | Modell / Tier | Auftrag bis Urteil | Modellantworten / Tools | Äußere Tools | Außerhalb der Tools | Gast-CLI gesamt | Driver inkl. Prüfung |
|---|---|---:|---:|---:|---:|---:|---:|
| edit-seat-v4 | Terra / Priority | 60,333 s | 12 / 12 | 40,107 s | 19,521 s | 4.821 ms | 6.431 ms |

Der Gast hatte einen stabil angekündigten privaten Seat und Tastenaliase.
Das Modell sollte sichtbare Abweichungen vor dem Abschluss selbst korrigieren.
Ein erster Klick-Aufruf ohne `button` scheiterte an der Argumentprüfung. Die
anschließenden Produktquittungen und wechselnden App-Bilder zeigten die
Bearbeitung und die Speicherung.

## Datei im offenen GTK-Dateidialog umbenennen

Für diese Aufgabe soll das Modell `Invoice-draft.txt` in `Invoice-118.txt`
umbenennen. Der Prüfer erwartet den ursprünglichen Rechnungsinhalt unter dem
neuen Namen, das Fehlen des alten Namens, die exakten Bytes der Inventardatei
sowie den erhaltenen Vordergrundentwurf und Fokus.

**files-v1 hat die Startbedingung bisher verfehlt.** Der Driver konnte den
offenen GTK-Dateidialog nicht bestätigen. Es wurde kein Modellauftrag
gestartet. Im kontrollierten Gegenversuch auf dem ansonsten identischen Quick-Driver
öffnete bereits das erste `Ctrl+O` den echten GTK-Dateidialog, nachdem Tastatur
und Zeiger dauerhaft am privaten Wayland-Seat angekündigt wurden. Der
VM-Besitzer meldete das frische Fixture `files-seat-probe` auf Stand
`5020184` mit D-Bus- und Tastenalias-Korrekturen. Der erste Terra-Priority-Lauf zur
Dateiaufgabe startete nach der Kontrolle, dass genau ein QEMU-Gast aktiv war.
Für diesen Versuch übertrug das private Boat-Relay Bilder in Blöcken von
8.100 statt 2.400 Bytes. Damit hat sich auch der Transportaufwand beim
Betrachten von Bildern gegenüber den drei Dokumentversuchen geändert.

### Erster Modellversuch im Öffnen-Dialog

Terra Priority erreichte das unabhängige Urteil nach **23,011 s**.
Das Initialbild zeigte die Rechnung in Zeile zwei. Terra klickte innerhalb
dieser Zeile und bündelte F2, Ctrl+A, den neuen Dateinamen und Enter mit
dem Klick. Der Broker quittierte alle fünf Aktionen in **420 ms**.
Terra forderte nach der Aktionsfolge kein weiteres Bild an. Das ursprüngliche
Rechnungsdokument existierte unter seinem alten Namen und Hash, die Zieldatei
fehlte. Inventar, Entwurf und Fokus waren korrekt.

| Versuch | Modellantworten / äußere Toolaufrufe | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver gesamt inkl. Prüfung |
|---|---:|---:|---:|---:|---:|
| files-seat-probe | 4 / 4 | 13,246 s | 9,203 s | 841 ms | 1.379 ms |

**Der gewählte Dialog war für diese Aufgabe ungeeignet.** GTK 3.24.52 blendet
seinen Kontextmenüeintrag Umbenennen im Öffnen-Modus ausdrücklich aus. Der
Modellversuch dokumentiert einen Fehler im Benchmark-Aufbau. Für folgende
Vergleiche öffnet der Runner den Speichern-unter-Dialog. Für die GUI-Prüfung
muss dort die Dateiliste mit verfügbarer Umbenennen-Aktion belegt sein.
[GTK-Quellcode zu den Dialogmodi](https://github.com/GNOME/gtk/blob/3.24.52/gtk/gtkfilechooserwidget.c#L2365-L2372)

### Funktionsprobe im korrigierten Dialog

Der VM-Besitzer bestätigte die Bedienbarkeit im echten GTK-Speicherdialog.
In einer vorbereitenden Terra-Funktionsprobe wurde der sichtbare
Umbenennen-Eintrag des Kontextmenüs verwendet. Vor dem Abschluss war der
neue Rechnungsname in der Liste zu sehen. Die unabhängige Prüfung fand den
neuen Namen mit den ursprünglichen Rechnungsbytes, den erwarteten
Inventar-Hash, den korrekten Vordergrundentwurf und durchgehenden Fokus.
Die Vorprobe zählt separat von den unbeeinflussten Modellstichproben.

### Unabhängiger GUI-Lauf im Speichern-unter-Dialog

Sol Ultrafast (low) benannte die Rechnung im frischen Gast auf Driver v7 um
und überprüfte den neuen Namen in der Dateiliste. Einen abgewiesenen
Rechtsklick mit numerischem Button-Wert korrigierte das Modell im selben
Versuch. Die Abschlussprüfung fand genau `Invoice-118.txt` und
`Inventory.txt` mit den erwarteten SHA-256-Werten. Im Vordergrund kam
`draftAB\n` an, der Niri-Fokus blieb beim Entwurf.

| Versuch | Auftrag bis unabhängiges Urteil | Modellantworten / äußere Toolaufrufe | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver gesamt inkl. Prüfung |
|---|---:|---:|---:|---:|---:|---:|
| files-gui-v2 | 65,274 s | 8 / 8 | 52,125 s | 12,446 s | 2.509 ms | 3.765 ms |

Der Gast beobachtete die korrekten Dateien **49,415 s nach Beginn des ersten
Gastaufrufs** und **341 ms nach Beginn des auslösenden Compose-Aufrufs**.
Beide Werte enden beim selben Dateibeleg. Der Driver prüfte danach den
Vordergrund und übermittelte das Urteil. Die abschließende Modellantwort
lag 67,245 s nach der T3-Anforderung vor. Für diesen Versuch waren
Mousepad und Dialog geöffnet, genau ein QEMU-Gast aktiv und die
Übertragungsgröße je Bildblock auf 8.100 Bytes gesetzt.

### Freie Werkzeugwahl mit paketierten Dateiaktionen

Der Auftrag auf Driver v8 verwendete denselben geöffneten Dialog, dasselbe
Modell und denselben Tier. Sol rief zuerst `private_exchange_list` auf
und sah den ursprünglichen Rechnungsnamen. Im zweiten Compose bündelte es
`private_exchange_rename_noreplace` mit einer weiteren Liste und prüfte die
beiden Namen. Dafür benötigte es keine Bilder. Der unabhängige Prüfer
bestätigte denselben vollständigen Dateibestand samt Hashes und
denselben Vordergrundzustand wie im GUI-Lauf. Alle drei Schritte gelangen
beim ersten Versuch.

| Versuch | Auftrag bis unabhängiges Urteil | Modellantworten / äußere Toolaufrufe | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver gesamt inkl. Prüfung |
|---|---:|---:|---:|---:|---:|---:|
| files-os-1 | 19,425 s | 3 / 3 | 12,439 s | 6,315 s | 4.747 ms | 6.669 ms |

Die drei Produktzeiten betrugen in Ausführungsreihenfolge **1.184 ms**
für die erste Liste, **811 ms** für die Umbenennung und **763 ms** für die
zweite Liste. Sie umfassen jeweils Workspace-CLI und Broker. Der Gast
beobachtete den korrekten Dateibestand **8,000 s** nach Beginn des ersten
Gastaufrufs und **1,140 s** nach Beginn des auslösenden Compose. Bis zur
abschließenden Modellantwort vergingen 20,810 s ab Auftragsübergabe.

In diesem Paar verkürzte sich die Auftragszeit bis zum Urteil um **45,849 s
(70,2 %)**. Sol kam mit zwei Compose-Aufrufen und der Abschlussprüfung aus.
Die fünf eingesparten äußeren Aufrufe und der entfallene Bildtransport gingen
mit einer kürzeren Gesamtdauer einher, obwohl die summierte Gast-CLI-Zeit
von 2,509 auf 4,747 s stieg. Pro Zugangsart liegt ein Lauf vor. Modell, Tier,
Testdaten, Prüfer und Einzelgastbetrieb stimmen überein. Driver v8 enthält
die zusätzlich verfügbaren Produktaktionen, der Vergleichslauf im Dialog
lief auf v7. Die Stichproben erlauben keine allgemeine Erfolgsquote oder
isolierte Schätzung des Effekts einzelner Werkzeug- oder Driveränderungen.
