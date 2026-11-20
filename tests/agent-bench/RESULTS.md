# Desktop-Benchmarks auf der Projekt-Boat

Stand 20. November 2026. GPT-6.2 Sol / Ultrafast hat dieselbe Rechnung in
zwei frischen Gästen erfolgreich umbenannt. Vom Modellauftrag bis zur
unabhängigen Bestätigung vergingen im GTK-Dateidialog **65,274 s** und bei
freier Werkzeugwahl mit den paketierten Dateiaktionen **19,425 s**. Auf
demselben Driver v8 erreichte GPT-6.1 Terra / Priority das identische
Dateiergebnis nach **16,562 s**. Auf Driver v10 schloss Sol die Aufgabe
in **15,252 s** ab. Der warme Produktschritt zur Umbenennung dauerte
**2,578 ms**.

Auch der Dokumentauftrag ist gelungen. Auf Driver v11b änderte Sol
den Liefertermin im offenen Mousepad-Dokument und speicherte eine bytegenaue
Kopie. Vom Auftrag bis zum unabhängigen Urteil vergingen **66,564 s**.
Auf Driver v14 wählte dasselbe Modell die private Zwischenablage für den
Text und den Dateinamen. Das unabhängige Urteil lag nach **61,571 s** vor.
Original, Inventar, Vordergrundentwurf und Fokus blieben in beiden Läufen erhalten.

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

### Erster erfolgreicher visueller Dokumentlauf auf Driver v11b

Sol Ultrafast (low) erhielt den unveränderten Dokumentauftrag und die
gültigen Argumente der GUI-Werkzeuge. Als Modell-Eingabe dienten
Mousepad-Bilder und privater Fensterstatus. Die Arbeitsfläche lief in
einem frischen, exklusiven QEMU-Gast mit bereits geöffnetem Dokument.

Das Modell klickte auf den Termin, markierte das Wort mit `Ctrl+Left`
und `Ctrl+Shift+Right` und tippte `Friday`. Im selben Compose öffnete es
den Speichern-unter-Dialog. Nach dessen Bildkontrolle trug es den neuen
Dateinamen ein und bestätigte per Linksklick. Das letzte Mousepad-Bild
zeigt den Satz `Delivery is due on Friday.` und die Referenz 4815.
Vor dem Abschluss las Sol den Fenstertitel
`/exchange/Handover-Friday.txt - Mousepad`.

Die unabhängige Prüfung fand genau Original, Inventardatei und Zielkopie
mit den erwarteten Bytes einschließlich Leerzeilen und Schlussumbruch der
Kopie. Während der privaten GUI-Aktionen behielt der Vordergrundentwurf
seinen Fokus und empfing `draftAB\n`. Es gab fünf Compose-Aufrufe,
davon vier mit Bild, und einen separaten Prüfaufruf. Alle Produktaktionen
wurden erfolgreich quittiert.

| Versuch | Auftrag bis Urteil | Auftrag bis Modellantwort | Modellantworten / äußere Aufrufe bis Urteil | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver inkl. Prüfung |
|---|---:|---:|---:|---:|---:|---:|---:|
| edit-gui-sol-v11 | 66,564 s | 68,500 s | 7 / 7 | 49,502 s | 16,010 s | 2.408 ms | 3.309 ms |

Der Gast beobachtete den korrekten Dateibestand **42,195 s ab dem ersten
Gastaufruf** und **586,424 ms ab Beginn des speichernden Compose**. Dies war
der fünfte eigenständige Dokumentversuch über mehrere Entwicklungsstände
und der erste auf v11b. Der frühere v7-Lauf mit `Fridayy.` bleibt
als fehlgeschlagener Versuch erfasst.

### Dokumentlauf mit privater Zwischenablage auf Driver v14

Sol Ultrafast (low) erhielt denselben Auftrag im frischen, exklusiven Gast
mit geöffnetem Mousepad. Es konnte die beiden privaten Clipboard-Aktionen
zusätzlich zu den bisherigen GUI-Werkzeugen wählen. Beim ersten Versuch,
die Auswahl mit `Ctrl+c` und `private_clipboard_read` zu kontrollieren,
meldete Schritt 7 `private clipboard selection unavailable`. Das nächste
Bild zeigte eine Markierung in der Überschrift, während im Liefertermin
noch `Monday` stand. Der Leseschritt lieferte keinen Text. Die Ursache für
die fehlende Zwischenablageauswahl bleibt offen.

Sol setzte die Auswahl neu, schrieb `Friday` in die private Zwischenablage
und fügte den Text per `Ctrl+v` ein. Über denselben Weg trug es
`Handover-Friday.txt` in den Speicherdialog ein. Die Schreibbelege
bestätigten 6 und 19 Bytes. Vor `finish` zeigte das letzte Mousepad-Bild
`Delivery is due on Friday.` und die Referenz 4815. Der Fensterstatus
bestätigte den Pfad `/exchange/Handover-Friday.txt`. Die unabhängige
Prüfung bestätigte dieselben drei Datei-Hashes wie auf v11b, die Eingabe
`draftAB\n` und den durchgehenden Fokus auf dem Vordergrundentwurf.

| Dokumentauftrag, je ein Versuch | Auftrag bis Urteil | Auftrag bis Modellantwort | Modellrunden / äußere Aufrufe bis Urteil | Compose / Bilder | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver inkl. Prüfung |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Visuell, v11b | 66,564 s | 68,500 s | 7 / 7 | 5 / 4 | 49,502 s | 16,010 s | 2.408 ms | 3.309 ms |
| Mit privater Zwischenablage, v14 | 61,571 s | 63,284 s | 9 / 9 | 7 / 6 | 46,392 s | 14,210 s | 4.464 ms | 5.883 ms |

Auf v14 beobachtete der Gast den bytegenauen Dateibestand **41,002 s ab
dem ersten Gastaufruf** und **957,419 ms ab dem speichernden Compose**.
Das unabhängige Urteil kam in diesem Versuchspaar 4,993 s früher an.
Sol brauchte dafür zwei zusätzliche Modellrunden. Der Leseversuch, die
unterschiedlichen Driverstände und die Einzelstichproben begrenzen die
Aussage zur Geschwindigkeit der Zwischenablage.

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

### Gleicher Auftrag und Driver mit Terra Priority und Sol Ultrafast

Beide Modelle erhielten denselben Auftrag und dasselbe Angebot an
GUI- und Dateiwerkzeugen. Jeder Lauf begann mit einem frisch gestarteten
Gast auf Driver v8, bereits geöffnetem GTK-Speicherdialog und exklusiver
QEMU-Nutzung. Beide Modelle erreichten im ersten Versuch genau die beiden
erwarteten Dateien mit unveränderten Bytes. Der Entwurf enthielt
`draftAB\n` und behielt den Niri-Fokus.

| Modell / Tier (je ein Lauf, low) | Auftrag bis Urteil | Auftrag bis Modellantwort | Modellantworten / äußere Aufrufe bis Urteil | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver inkl. Prüfung |
|---|---:|---:|---:|---:|---:|---:|---:|
| GPT-6.1 Terra / Priority | 16,562 s | 17,782 s | 2 / 2 | 7,133 s | 8,780 s | 1.333 ms | 1.856 ms |
| GPT-6.2 Sol / Ultrafast | 19,425 s | 20,810 s | 3 / 3 | 12,439 s | 6,315 s | 4.747 ms | 6.669 ms |

Terra listete zuerst den Ordner und fragte den privaten Fensterstatus ab.
Im zweiten Compose bündelte Terra Umbenennung, erneutes Auflisten und
Fensterstatus. Der Modelltrace zeigt, dass Terra den unabhängigen Abschluss
im selben Shellaufruf unbedingt anschloss. Die korrekte Kontrollliste und
das Urteil erreichten das Modell gemeinsam. Bei einem falschen Ergebnis
hätte Terra vor Abschluss keine Gelegenheit zum Korrigieren gehabt. Sol
erhielt die Liste vor seinem separaten Abschlussaufruf.

Terras Produktschritte für erste Liste, Umbenennung und zweite Liste
dauerten **308, 212 und 180 ms**. Der Gast beobachtete die Dateiwirkung
4,393 s nach Beginn des ersten Gastaufrufs und 340 ms nach Beginn des
auslösenden Compose. Beide Modelle verwendeten zwei Compose-Aufrufe und
eine unabhängige Abschlussprüfung. Terras Urteil kam in diesem Paar
2,863 s früher an. Auch die direkten Produktlaufzeiten unterschieden sich
deutlich. Je eine Beobachtung erlaubt keine belastbare Rangfolge der Modelle.

### Driver v10 mit wiederverwendetem Dateiworker

Sol Ultrafast (low) erhielt denselben Nutzerauftrag mit den gültigen
Argumentsignaturen der verfügbaren Werkzeuge. Es verwendete dieselben
Aktionen in derselben Gruppierung wie im v8-Lauf mit freier Werkzeugwahl.
Die erste Liste zeigte `Inventory.txt` und `Invoice-draft.txt`, die Liste
nach der Umbenennung zeigte `Inventory.txt` und `Invoice-118.txt`.
Der unabhängige Prüfer bestätigte die erwarteten Bytes beider Dateien,
den Entwurf `draftAB\n` und seinen durchgängigen Fokus. Der Einzelversuch
lief mit einem frischen, exklusiven Gast und geöffnetem Dialog.

| Versuch | Auftrag bis Urteil | Auftrag bis Modellantwort | Modellantworten / äußere Aufrufe bis Urteil | Zeit in äußeren Tools | Codex-Zeit außerhalb der Tools | Gast-CLI gesamt | Driver inkl. Prüfung |
|---|---:|---:|---:|---:|---:|---:|---:|
| files-os-warm-v10 | 15,252 s | 16,945 s | 3 / 3 | 6,405 s | 7,795 s | 703 ms | 1.522 ms |

Die erste Liste startete den Dateiworker. Beim nächsten Compose konnte
der Broker denselben Worker für die Umbenennung und die Kontrollliste
verwenden. Alle Zeiten der folgenden Tabelle sind direkt gemessene
Millisekunden. Die Intervalle überlappen sich. Die Dateioperation umfasst
auch die Validierung und beim Umbenennen die Sperre samt Epochenprüfung.

| Zeitfeld | Erste Liste | Warme Umbenennung | Warme Kontrollliste |
|---|---:|---:|---:|
| Gesamter Produktschritt | 105,948 | 2,578 | 2,392 |
| Client-Zulassungsprüfung | 0,621 | 0,618 | 0,440 |
| Client-Anfrage bis Antwort | 105,135 | 1,737 | 1,825 |
| Broker-Verarbeitung | 104,265 | 1,248 | 0,712 |
| Worker-Anfrage bis Antwort | 103,979 | 0,681 | 0,440 |
| Workerstart | 103,602 | bereits gestartet | bereits gestartet |
| Dateioperation im Worker | 0,060 | 0,337 | 0,054 |

| Compose-Aufruf | Gast-CLI gesamt | Python-Eintritt bis Ausführungsbeginn | Ausführung einschließlich aller Schritte |
|---|---:|---:|---:|
| Erste Liste | 451,678 ms | 243,343 ms | 108,427 ms |
| Umbenennung und Kontrollliste | 251,041 ms | 183,818 ms | 5,998 ms |

Der Gast beobachtete den exakten Dateizustand **4,206 s ab dem ersten
Gastaufruf** und **233,246 ms ab dem auslösenden Compose**. Für Sol
lagen auf v8 dieselben Messgrenzen bei 8,000 s und 1.140,492 ms.
Der unabhängig bestätigte Gesamtauftrag dauerte auf v10 in diesem Paar
4,173 s weniger. Die neue Instrumentierung trennt den Workerstart beim
ersten Aufruf von der eigentlichen Umbenennung und von den Kosten eines
neuen CLI-Prozesses. Die beiden Einzelversuche liefern keine Verteilung
der Laufzeiten.
