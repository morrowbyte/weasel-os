# Tägliche geprüfte Updates

Die tägliche T3-Aufgabe prüft mit dem vorhandenen Codex-Harness alle bekannten
Pins und aktuellen Flake-Inputs. Sie bereitet Kandidaten in eigenen Worktrees
vor. Ein fest installierter Root-Dienst prüft die Quellen und Artefakte erneut,
erstellt einen Home-Snapshot und aktiviert ausschließlich den geprüften
Systempfad. Der Auftrag erlaubt diese Aktivierung ohne tägliche Rückfrage.

## Betriebszustand und Abdeckung

Die Software wird durch `modules/daily-updates.nix` auf `nixy-laptop` installiert.
Vor dem einmaligen Bootstrap fehlen der laufenden Generation der Helper und
Root-Dienst. `weasel-update --status` zeigt danach den öffentlichen Zustand;
ein vorbereitetes oder gebautes Ergebnis beweist noch keine Aktivierung.
T3s Schedulerstatus `succeeded` bestätigt ausschließlich die Zustellung.
Eine tatsächlich ausgeführte Tagesprüfung hat einen privaten Review-Beleg;
eine abgeschlossene Aktivierung zusätzlich ein Root-Journal mit `complete`.

Die Einrichtung am 2026-10-08 prüfte reale Kandidaten Codex 0.162.0 und
ChatGPT 26.1002.52244 einschließlich vollständiger einzelner Laptop-Builds,
nativer/GUI-Gates und struktureller Closure-Vergleiche. Ihre Pin-Commits sind
signiert. Der vorhandene T3-Nightly-Stand bestand Start und eine tatsächliche
synthetische Migration von stabilem 0.0.45 (54 Migrationen) auf die Nightly
(60), ohne persönliche Daten zu verwenden. Es gab keinen neueren T3-Kandidaten.
Diese Befunde belegen noch keinen Root-Switch oder täglichen Hintergrundlauf.
Der manuelle Scheduler-Test wurde durch T3s Capability-Prüfung in `runtime auto`
abgewiesen; kein Ersatz-Dispatch umgeht diese Grenze. Der dauerhaft laufende
Proton-SSH-Benutzerdienst ist davon unabhängig bereits beobachtet.

Der native T3-Task heißt „Weasel OS: tägliche geprüfte Updates“, ist an diesen
Update-Thread gebunden und verwendet `prompts/daily-update-review.md`. Seine
Uhrzeit ist 12:00 Europe/Berlin, erstmals 2026-10-09 um 12:00 MESZ
(10:00 UTC). T3 muss laufen und der Laptop wach und am Netzteil sein;
der Scheduler ist kein Dienst auf einem ausgeschalteten Laptop. Eine geöffnete
ChatGPT-App ist nicht erforderlich. Die konkrete Task-ID und der beobachtete
nächste Termin gehören in den Einrichtungsbeleg, statt einer angenommenen
Zeitzone. Nach Bootstrap wird ein echter Hintergrundlauf separat beobachtet.

`config/update-pins.json` erfasst zunächst 37 Pins und 70 aufgelöste Lock-Nodes;
der virtuelle Root-Knoten kommt im geladenen Graphen zusätzlich hinzu. Das ist
keine Obergrenze: Der Tageslauf liest den aktuellen Quellstand und ergänzt
neue Pins. Alle Einträge werden recherchiert und mit endlichem nächsten
Prüftermin dokumentiert. `--record-review` verlangt zusätzlich zu jedem Pin
einen expliziten Befund für jeden tatsächlichen transitiven Lock-Knoten,
gebunden an den aktuellen Main-Commit und dessen genaue `locked`-Daten.
Fehlende Nodes, veraltete Revisionen und Quellenänderungen während der
Aufzeichnung blockieren den Beleg. Ein mit `investigate` erfasster Knoten
meldet offen fehlende Upstream-Prüfung und wird spätestens morgen untersucht.
**Der normale Tageslauf aktualisiert möglichst viele Quellen als gemeinsamen
Batch:** alle konfigurierten Flake-Inputs und anwendbaren eigenen Paket-Pins.
Stable, Unstable und Nightly behalten jeweils ihre konfigurierte Auswahl.
Nach dem ersten breiten Build werden Fehler gezielt eingegrenzt, repariert
oder einzelne belegte Verursacher zurückgenommen; die übrigen Updates bleiben.
Fehlende Spezialadapter sind keine allgemeine Paket-Updatesperre. Ein kompletter
Build belegt jedoch keine Laufzeitprüfung jedes Programms. Für T3, Codex/ACP
und ChatGPT gibt es zusätzlich echte isolierte Proben geänderter Artefakte.
Die Einzelpin-Lanes für T3 und ChatGPT bleiben als gezielte schnelle Möglichkeit.
Codex nutzt packages/codex-source/ mit Produktions- und Testpatch sowie einer
separat eingefrorenen Rust-Toolchain. --discover codex prüft offizielles
npm-latest nur als Upstream-Versionshinweis und liefert held-local-patch,
candidate: null und den nächsten Review am Folgetag. Ein npm-Archiv ersetzt
diesen Source-Build nicht. --prepare codex ist bis zum geprüften Source-Adapter
ausdrücklich gesperrt; andere Batchupdates laufen mit unverändertem Codex-Bundle
weiter. Der nächste datierte Rebase-Review ist am 10.10.2026 fällig.
Der breite Batch erlaubt bestehende Paket- und Nix-Konfigurationsreparaturen,
aber keine Änderung des privilegierten Updaters, Zugängen oder stateVersion,
keine neuen/gelöschten Dateien und keinen unbenannten Release-Wechsel.

NixOS 25.11 ist außerhalb des Supports. Der Wechsel zu einer unterstützten
stabilen Basis gehört zusammen mit Home Manager in eine eigene Migration.
`system.stateVersion` wird dabei nicht automatisch geändert. Siehe
[Pin-Prüfung und offizielle Belege](update-pin-review.md).

## Bedienung

Der Modellprozess arbeitet als `evilweasel`. Die Codex-Regel erlaubt nur die
fest installierte Helper-Datei mit den vorgesehenen Aktionen. Sie erlaubt
keine freien Root-Kommandos. Secrets verbleiben in vorhandenen privaten
Stores und gelangen weder in Nix noch Git oder Review-Dateien.

```sh
/run/current-system/sw/bin/weasel-update --status
/run/current-system/sw/bin/weasel-update --review-start
/run/current-system/sw/bin/weasel-update --record-review /absoluter/pfad/review.json
/run/current-system/sw/bin/weasel-update --new-batch --mode batch
/run/current-system/sw/bin/weasel-update --prepare-batch /ausgabe/SOURCE --mode batch
/run/current-system/sw/bin/weasel-update --discover codex
# Codex: nur Discovery/Review; kein --prepare ohne geprüften Source-Adapter.
/run/current-system/sw/bin/weasel-update --discover chatgpt
/run/current-system/sw/bin/weasel-update --submit 20261008T120000Z-0123abcd
```

Die letzte ID ist ein Formatbeispiel. Verwende die tatsächlich ausgegebene ID.
Der Helper erzeugt seinen eigenen Kandidaten unter
`~/.local/state/weasel-updates/candidates/ID/source` und einen signierten
Branch `weasel-update-ID`. Eigene Reviews und Metadaten liegen unter demselben
privaten Zustand. `--output` schreibt ausschließlich neue Receipt-Dateien im
Update-Zustand oder `/tmp`; bestehende Dateien und Symlink-Eltern werden
abgelehnt. `--state /tmp/weasel-updates-NAME` erlaubt private Testzustände.
Der festgelegte Integrationscheckout kann nicht durch einen anderen ersetzt
werden. Keine Kandidatenprüfungen schreiben in Main.

`--submit` veröffentlicht exklusiv eine vollständig geschriebene Anfrage in
`/var/lib/weasel-updates-inbox/request.json`. Sie enthält nur vollständige
Commit-IDs, einen Update-Branch, ID und Baseline-Systempfad. Schema 2 nennt
zusätzlich ausdrücklich `batch` oder `release-migration`; Review-Schema 1
ist davon unabhängig. Eine vorhandene
Anfrage bleibt erhalten. `weasel-update-activate.path` startet darauf den
festen Dienst `weasel-update-activate.service`.

## Prüfungen und Aktivierung

Vorbereitung und unabhängiger Submitter verlangen sauberes Main, kanonisches
Origin, übereinstimmenden Remote-Stand und eine Baseline, die die tatsächlich
laufende und als Bootprofil ausgewählte Generation reproduziert. Änderungen
an Dateien, Index, Branch, Remote oder System während der Prüfung blockieren
Integration bzw. Aktivierung. Auch per `assume-unchanged` versteckte
Editoränderungen werden anhand der Dateiinhalte erkannt.

Der Root-Verifier verlangt einen direkten signierten Nachfolger der Baseline
vom eingerichteten Signierschlüssel und prüft Git-Objekte anhand ihrer
Inhalts-Hashes. Schema 1 erlaubt nur die drei unterstützten Pin-Übergänge;
Schema 2 prüft den breiten Quelldiff und den unabhängig erzeugten Zusatz im
Lernlog. Authentifizierungs-, User-/Gruppen-, SSH-, Sudo-, Nix-Vertrauens- und
Zustandsoptionen werden zusätzlich ausgewertet und ohne Geheimniswerte als
Hash verglichen. Der effektive Root-Dienst und Inbox-Pfad müssen dem
unveränderten geschützten Modul entsprechen. Er friert die geprüften Quellen in
schreibgeschützten Root-Verzeichnissen ein. Nix-Evaluationen, Builds,
Netzwerk-Discovery und App-Prüfungen laufen weiterhin als normaler Benutzer.

Die private Dienst-Umask bleibt `0077`. Das frisch angelegte Root-Verzeichnis
für die beiden Quellbäume erhält ausdrücklich Modus `0711`, damit der normale
Prüfbenutzer es durchqueren kann. Die eingefrorenen Verzeichnisse bleiben
`0555`, Dateien `0444` beziehungsweise `0555`; Journale und Arbeitsverzeichnisse
behalten ihre privaten Rechte. Ein tatsächlicher Test unter einer anderen UID
prüft Lesbarkeit ohne Schreibrecht und den weiterhin gesperrten Journalzugriff.

Private Kandidatenbelege werden vollständig als kompaktes JSON geschrieben.
Der Leser behält seine Grenze von 1 MiB; Closure-Einträge und Quellmanifeste
werden nicht gekürzt. Auch die Übergänge nach Submit und beim späteren
Abschluss verwenden dieselbe Ausgabe. Die nächste Kandidatenausgabe muss
bereits vor Veröffentlichung der Anfrage in diese Grenze passen.

Ein normaler Laptop-Batch prüft und baut nur `nixy-laptop`; die anderen Hosts
werden dabei nicht aktiviert und gehören nicht in seinen täglichen Prüfpfad.
Die separate Release-Migration behält ihren Vier-Host-Vergleich. Nix-Syntax,
Flake-Check und der komplette Laptop-Build bleiben erforderlich. Der frische
SSH-Vergleich prüft die Authentifizierungsdaten bereits zweimal; seine Ergebnisse
werden direkt verwendet statt dieselben Daten zusätzlich viermal auszuwerten.
Die abschließenden vollständigen Quellmanifeste sichern beide Aufrufer weiterhin
gegen Änderungen während der Prüfung ab. Die Closure-Prüfung hält alle
hinzugefügten und entfernten Store-Artefakte mit vollständigen Inventar-Hashes
fest; sie behauptet keine Gleichheit außerhalb dreier Apps. Die bisherigen
aktiven Einzelpin-Lanes für T3/ChatGPT prüfen strukturell identische erzeugte Ausgaben außerhalb
der ausgewählten App-Closures. Ein bloßer Namens-Whitelist-Treffer reicht nicht.

Der Kandidat validiert die ausgewertete Niri-Konfiguration mit dem tatsächlich
gebauten Niri in einer privaten Kopie aller Includes. Das ist Syntax-/Config-
Validierung, keine Laptop-GPU-/Overlay-Probe. Der Root-Verifier wiederholt sie.
Geänderte Display-Manager-/Niri-Units einschließlich Drop-ins müssen die laufende
Sitzung beim Switch erhalten. Der Updater installiert dafür Niris
`restartIfChanged = false`. Kernel und laufender Compositor übernehmen neue
Binärdateien erst nach einem späteren Boot beziehungsweise neuer Anmeldung;
der Tageslauf startet diese nicht automatisch.

Die realen Funktions-Gates verwenden leere Profile in privaten Benutzer-,
PID- und Netzwerk-Namespaces. Sie lesen keine persönlichen App-Daten und
stellen keine Modellanfragen. Codex startet den CLI- und app-server-Handshake
sowie den an den Kandidaten gebundenen ACP-Adapter. ChatGPT prüft Renderer,
Verpackung des nativen Watchers und Audio-Bibliotheken. T3 prüft Renderer,
Backend und eine synthetische Migration von der alten zur neuen Datenbank
mit Projekt, Thread, Nachricht und deaktiviertem Zeitplan. Nur T3s öffentlicher
Clerk-Frontend-Host ist über einen begrenzten Proxy erreichbar; andere
Internetziele bleiben gesperrt. Ein leerer Recovery-Screen besteht nicht.
Software-Rendering auf Xvfb beweist keine GPU-/Niri-/Overlay-Kompatibilität.

Vor Builds sind mindestens 65 GiB verfügbar; vor Snapshot und Aktivierung
mindestens 50 GiB auf Home-, Store- und Zustandsdateisystemen. Netzstrom und
mindestens 30 Prozent Akku sind erforderlich. Der Snapshot muss tatsächlich
ein schreibgeschütztes Btrfs-Subvolume auf dem Home-Mount sein. Ein
Generationenrollback schützt mutable Home-Daten nicht.

Die Quelle wird unter Compare-and-swap-Prüfungen integriert. Die ursprünglichen
Datei- und Index-Inodes verbleiben geschützt unter
`/home/.weasel-update-transactions/ID`, damit Saves über bereits offene
Dateideskriptoren erhalten bleiben. Journale werden vor jedem Übergang
synchronisiert. Erst danach wählt der Dienst den exakten getesteten Storepfad
und prüft Netzwerkdienste und DNS. Er veröffentlicht ausschließlich den
geprüften Commit ohne Force-Push.

## Parallel arbeiten, anhalten und Recovery

Andere T3-Threads bekommen jeweils einen eigenen Worktree. Sie dürfen parallel
editieren, evaluieren und bauen. Der Laptop hat aber nur eine laufende
Systemgeneration: Aktivierungen und Updates werden koordiniert. Vor `fr`,
`fu`, `nh os switch` oder einem manuellen Switch zuerst den Updatestatus prüfen
und einen laufenden Lauf abwarten. Der Dienst prüft andere Aktivierungsprozesse,
aber ein beliebiger Adminprozess respektiert seinen Lock nicht automatisch.
Veröffentlichte, noch nicht aktivierte Quellen blockieren den nächsten
Update-Switch, bis die Baseline wieder den laufenden Stand reproduziert.

Zum Pausieren den bestehenden T3-Task deaktivieren, nicht einen zweiten
anlegen. Das beendet keine bereits laufende Root-Transaktion. Der normale
Terminalbefehl `sudo systemctl stop weasel-update-activate.path` verhindert
weitere Trigger. Einen aktiven Dienst nicht ohne Diagnose abbrechen;
Unterbrechungen nach dem Snapshot oder während Integration benötigen Recovery.

```sh
weasel-update --status
systemctl status weasel-update-activate.path weasel-update-activate.service
sudo journalctl -u weasel-update-activate.service
sudo cat /var/lib/weasel-updates/runs/ID/journal.json
sudo snapper -c home list
```

Jedes ungeklärte Journal blockiert weitere Updates. Bei Aktivierungsfehlern
versucht der Dienst nur, die vorherige Systemgeneration wieder auszuwählen.
Home wird niemals automatisch restauriert. Originale, Snapshot-ID und beide
Systemclosures bleiben erhalten. Zuerst aktive Generation, Bootprofil,
Quellen, Remote und behaltene Originale vergleichen; fremde Editoränderungen
separat sichern bzw. integrieren. Danach einen konkreten Reparaturkandidaten
bauen und prüfen. Journale nicht einfach löschen oder auf `complete` setzen.
Der Helper bietet bewusst keinen pauschalen Recovery-/Home-Reset-Befehl.

Eine separat geprüfte Bootstrap-Fortsetzung ist mit `weasel-update-bootstrap
--receipt IMMUTABLE_RECEIPT --resume --journal-sha256 SHA256` möglich. Die
Root-Operation akzeptiert ausschließlich das exakte eigene `recovery-required`-
Journal mit vollständig ausgeführtem Systemrollback und unverändertem Receipt,
Commit, altem/neuem System, behaltenen Closure-Roots und readonly Original-
Snapshot. Andere offene Transaktionen bleiben gesperrt. `--check` prüft diese
Bindungen ohne Aktivierung. Normale User-Receiptprüfung kann keine Fortsetzung
autorisieren. Default-Updates und normale Bootstrap-Wiederholung behalten ihre
bisherigen Sperren.

Die Fortsetzung archiviert die ursprünglichen Journalbytes, prüft alle bisherigen
Quellen-/Signatur-/Remote-/Strom-/Speichergates, erstellt einen frischen
Home-Snapshot und verlangt erneut nativen Exit 0, Health und unveränderte Quellen.
Ein früher oder erneuter Resume-Fehler bleibt `recovery-required`; ein alter
Fehlversuch wird nie durch `failed-before-integration` oder einen anderen
Commit-Namensraum ersetzt. Ein identisches bereits geschriebenes Root-Archiv
wird sicher wiederverwendet; veränderte Archive und Symlinks werden abgewiesen.

Der Updater begrenzt ausschließlich seine markierten aufgelösten Kandidaten
und abgeschlossenen Transaktionen auf drei. Geänderte Originale, offene
Dateideskriptoren und ungeklärte Transaktionen werden erhalten. Gelöscht werden
nur eindeutig zugeordnete Updater-Snapshots und eigene Artefakte. Wurde ein alter
Snapshot ausdrücklich gelöscht oder lässt er sich nicht mehr eindeutig zuordnen,
bleibt die zugehörige alte Transaktion bei der Bereinigung unverändert erhalten.
Das blockiert kein neues Update. Persönliche Daten, Spiele, Modelle, VM-Disks
und fremde Worktrees sind keine Cleanup-Ziele.
Ein Stromausfall während Cleanup hinterlässt ebenfalls einen Recoveryfall.

## Einmaliger Bootstrap

Die Installation oder Änderung des festen Root-Helpers braucht die passenden
Hostrechte und eine Freigabe für den privilegierten Updater. Ein normaler
Paket-Batch darf weder diesen Helper noch Sicherheitsregeln ändern. Seine
Aktivierungsfreigabe ersetzt eine solche gesonderte Bootstrap-Freigabe nicht.

Der tatsächliche Harness-Zustand und die passende Sudo-Berechtigung sind pro
Aufruf zu prüfen. Im alten Harness blockierte `NoNewPrivs: 1` auch freigegebenes
sudo; das wurde zuletzt am 2026-10-09 beobachtet. Am 2026-10-11 zeigte ein
normaler Hostprozess `NoNewPrivs: 0`; der normale NixOS-Wrapper
`/run/wrappers/bin/sudo -n id -u` lieferte tatsächlich `0`. Dieser Wrapper hat
den Setuid-Root-Modus `4511`. `/run/current-system/sw/bin/sudo` ist dagegen
Modus `555` ohne Setuid und ist nicht der Pfad für den Terminal-Bootstrap.
Der konkrete Bootstrap-Aufruf verwendet `/run/wrappers/bin/sudo`; aktuelle
Hostrechte und die gesonderte Freigabe bleiben erforderlich. Scheitert die
konkrete Ausführung weiterhin an fehlenden Rechten, braucht der vollständig
gebaute und quellgeprüfte Bootstrap einen Aufruf im normalen Terminal. Kein
User-Executor, alternativer Dispatch oder Umgehen einer verweigerten
T3-Capability ersetzt fehlende Befugnisse.

Der konkrete Bootstrap-Befehl nennt einen unveränderlichen Store-Wrapper und
einen unveränderlichen Beleg mit exaktem signiertem Commit, Quellmanifest,
vorherigem System und gebautem neuen System. Der Bootstrap prüft diese
Bindung erneut, erstellt den Home-Snapshot und aktiviert exakt diesen Pfad.
Wenn Main, Remote oder aktive Generation inzwischen gewechselt haben, muss
zuerst neu gebaut und der Beleg erneuert werden. Ein späteres unbeschränktes
`nixos-rebuild switch` aus mutable Quellen ersetzt diesen Nachweis nicht.

## Authentifizierung bei Paketupdates und Release-Migration

Normale Batches erhalten den exakten Rohvergleich von Authentifizierungs- und
State-Version-Einstellungen. Zusätzlich werden die ausgewählten SSH-, Xauth-
und Systemd-Paketausgaben, die erzeugte SSH-Konfiguration und die wirksamen
SSH-Units gegen unabhängig zusammengesetzte offizielle Quellen gebunden.
Der Vergleich umfasst die tatsächlichen Ausgabepfade und ihre Verwendung;
ein übereinstimmender `drvPath` allein reicht nicht. Wirksame `/etc`-Ziele,
SSH-Authentifizierungsdateien, PAM und Systemd-Managerkonfiguration gehören
zu dieser Prüfung. Authentifizierungsdaten werden nur als
Hashes erfasst; ihre Werte werden nicht ausgegeben. Diese Prüfungen sind keine Sandbox für beliebige weitere
Nix-Aktivierungsskripte oder Dienste; die konkrete Quellprüfung bleibt nötig.

Die feste Migration von 25.11 auf 26.05 erhält eine eng begrenzte Ausnahme
für die exakt geprüften offiziellen Lock-Revisionen, NAR-Hashes und
Modulquellen. Sie erklärt die zusätzlichen SSH-Nullwerte `AcceptEnv` und
`Banner` und den tatsächlichen Upstream-Sudo-Zusatz
`Defaults env_keep+=NIXOS_NO_CHECK`. Die unten beschriebene feste OpenSSH-Prüfung
bindet zusätzlich die konkret geprüften Daemon-Defaults dieser Migration;
sonstige gemeinsame Einstellungen bleiben gleich. Dieser Sudo-Zusatz verändert die erlaubte
Umgebungsübernahme: Ein bereits berechtigter Sudo-Aufruf kann eine Variable
erhalten, mit der die neue Switch-Implementierung ihre Vorprüfungen überspringt.
Die geschützten Helper weisen diese Variable deshalb bei jeder Präsenz,
auch mit leerem Wert, ab; der Root-Dienst entfernt sie zusätzlich aus seiner
Umgebung. Die Installation dieser Ausnahme benötigt eine gesonderte
Freigabe für den privilegierten Updater. Ein Paket-Batch darf sie nicht selbst
hinzufügen oder ändern.

Der feste normale User-Executor baut zuerst ausschließlich die ausgewählten
SSH-Pakete, Konfigurationen und den kleinen SSH-Unit-Baum mit den vollständigen
Quellbehauptungen. Diese Phase ist ein Build und kann auch eine benötigte
Quellableitung für Import-from-Derivation realisieren. Die vollständige
Systemd-Quellprovenienz wird dabei ausdrücklich ausgewertet. Danach lesen beide
Produktionskontexte die Provenienz mit der festen Argumentliste aus
`batch.sensitive_eval_args`: `--offline --max-jobs 0 --builders '' --option
substitute false --option allow-import-from-derivation true`. Nix 2.31 verweigert
kontextgebundene IFD-Lesezugriffe bei `false` auch dann, wenn die Ausgabe bereits
existiert; ein erfolgreiches Lesen über einen kontextlosen Literalpfad wäre
kein gültiger Ersatz für die Quellenbindung. Die neue Prüfung erlaubt den
Kontextzugriff auf bereits vorhandene, quellengebundene Store-Ausgaben, jedoch
keine lokalen oder entfernten Compiler-Jobs und keine Substitution.
`--offline` erzeugt keine Netzwerkisolation; ungecachete Evaluator-Fetcher werden
damit nicht pauschal blockiert. Fehlt eine benötigte gecachte Build-Ausgabe,
scheitert die Prüfung.
Es gibt keinen
Fallback nach einem Evaluationsfehler und keine vom Aufrufer vorgegebene
Ableitung. Es müssen keine vier vollständigen Host-Systeme allein für diesen
SSH-Nachweis gebaut werden. Serielle Build-Koordination, Strom-, Platz-,
Zeit- und Quellenprüfungen gelten auch für diese Phase; eine fortlaufende
RAM-Überwachung ist keine zusätzliche Eigenschaft dieses SSH-Helpers.

Die frisch gebauten SSH-Daemons laufen mit `-G -T` in privaten Bubblewrap-
Umgebungen ohne Listener, Host-Schlüssel, vorhandene SSH-Dateien oder
Agent-Sockets. Der Vergleich prüft die vollständige wirksame Ausgabe und
mehrere feste Verbindungskontexte. Nur die unabhängig gebundenen Xauth-,
SFTP-, `sshd-auth`- und `sshd-session`-Programmpfade dürfen mit ihrem exakten
Paketbuild wechseln. Hinzu kommt ausschließlich die oben beschriebene feste
Migration-Ausnahme für kanonische Defaults. Unbekannte oder konfigurierte
Policy-Änderungen und fehlende beziehungsweise mehrdeutige Pflichtwerte blockieren die
Vorbereitung. Root wiederholt den Nachweis unabhängig; ein Caller-Beleg ersetzt
ihn nicht.

Die systemweiten SSH-Client-Ergänzungen behalten ihre konfigurierten Bytes.
Nur die vollständige offiziell erzeugte Libvirt-`Include`-Strophe und der
vollständige GnuPG-`Match`-Block werden auf feste Rollen abgebildet. Dazu müssen
die jeweiligen Aktivierungsflags boolesch und unverändert sein, der aktivierte
Block exakt einmal vorkommen und die vollständigen Paketidentitäten aus dem
kanonischen gesperrten nixpkgs stimmen. Der GnuPG-Block bindet zusätzlich Bash
und den exakten `runtimeShell`. Vorgegebene Rollenmarker blockieren die Prüfung;
alle übrigen Bytes, einschließlich `Host *` und `AddKeysToAgent`, bleiben Teil
des Vergleichs. Die neuen Paketbindungen müssen im frischen Vier-Host-Nachweis
bestehen; synthetische Fixtures oder historische Daemon-Proben ersetzen ihn nicht.

Der exakt bekannte historische Baseline-Lock enthält einen byteidentischen
doppelten Eintrag. Nur die gebundene vollständige Legacy-Datei darf auf der
alten Seite verwendet werden. Neue Kandidaten-Locks bleiben frei von
doppelten JSON-Einträgen.

## Laufende Treiber und pausierte Dienste

Vor dem Abschluss der Vorbereitung und erneut unmittelbar vor der
Aktivierung im unabhängigen Root-Ablauf bindet der
Treiber-Guard die erzeugte Graphics-Verknüpfung und die tatsächlich ausgewählte
NVML-Bibliothek an die exakte System-Closure. Die Ausgabe wird über unveränderliche Pfade, ELF-Form, Metadaten und Hash
gebunden; ihre ausgewiesene Bibliotheksversion wird mit der festen geladenen
NVIDIA-Modulversion verglichen. Mehrdeutige, fehlende, entfernte oder inkompatible Ausgaben und
beobachtete Änderungen während der Prüfung blockieren eine Live-Aktivierung.
Der Guard führt keine GPU-Abfrage, kein Modul-Laden und keinen Neustart aus.
Diese Metadatenprüfung beweist weder Hardwarefunktion noch die tatsächliche
Verwendung eines neuen Treibers nach einem Boot.

Für die bereits laufenden pausierten Computer-Use-, CUA- und
Entscheidungsdienste ist als separate Kandidatenreparatur die Home-Manager-
Regel `Unit.X-SwitchMethod=keep-old` vorgesehen. Sie ist kein Bestandteil
dieses Updater-Bootstraps. Die Regel erhält aktive
Instanzen; sie verhindert nicht das Starten bisher inaktiver, angeforderter
Units. Ihr aktueller Zustand muss deshalb vor der Aktivierung erneut geprüft
werden. Ein NixOS-`Service.X-RestartIfChanged=false` ersetzt diese Home-Manager-
Regel nicht. Neue Programmausgaben im Profil bedeuten bei erhaltenen Instanzen
nicht, dass deren neue Version bereits läuft. Eine spätere Übernahme braucht
eine eigene Koordination mit dem pausierten Setup.

Die separat vorbereiteten ergänzenden Migrationsproben lesen Kernel-,
Initrd-, Modul- und Konfigurationsartefakte aus der gebauten Closure. Sie sind
keine dauerhaft installierten zusätzlichen Helper-Gates. Der private Beleg
vom 2026-10-10 bestätigt die Analyse des alten Initrd-Archivs, einschließlich
des archivierten Stage-1-Skripts; er beweist keinen Kandidaten-Boot. Ein Stage-1-Symlink wird an
die im Initrd-Archiv enthaltenen Skriptbytes gebunden; der Host-Pfad wird dabei
nicht ausgeführt. Vollständige Archivprüfung, Boot-Modulbestand und Vermagic
ersetzen keinen tatsächlichen Boot oder Hardwaretest. Ein Home-Snapshot deckt
mutable Dienstedaten unter `/var` und `/etc` nicht ab. Erhaltene laufende
Docker-/VM-Dienste und isolierte Schema-Proben sind daher jeweils mit ihren
tatsächlichen Grenzen zu berichten.

## Remote Builds und mehrere Maschinen

Die aktuelle Installation aktiviert ausschließlich `nixy-laptop`. Sie richtet
noch keinen VPS-Builder und keinen Flotten-Rollout ein. Die dafür geeignete
Trennung steht in [remote-update-builds.md](remote-update-builds.md): zentrale
Kandidaten-/Build-/VM-Tests, maschinenspezifische Gates und lokale Aktivierung.
Ein VPS-Test ersetzt weder echte Laptop-Hardwareprüfungen noch Backups von
produktiven VM-Daten.


SSH provenance preserves the typed client `setXAuthLocation` boolean and client
`extraConfig`, plus the ordered typed PAM `loginLimits`, when independently
composing the official locked modules. The complete protected `/etc` sources,
metadata and generated PAM policy must still match that independent composition;
caller-supplied PAM text, rules or replacement sources are not seeds. The exact
canonical `limits.conf` artifact is an optional fourth output of the bounded SSH
mini-build. Its content hash and typed limits remain in the cross-source policy
fingerprint; only its independently bound store path is normalized.

When `setXAuthLocation = false`, the exact canonical daemon is first parsed with
`/dev/null` in the same isolated read-only sandbox. Its compiled XAuth default
stays literal in the complete effective-output comparison. The typed boolean,
a different compiled default, or an arbitrary configured executable cannot be
normalized into the enabled option's canonical store-path substitution. These
adapter fixtures do not replace the actual full policy and system build gates.


Die OpenSSH-Prüfung normalisiert ausschließlich die Groß-/Kleinschreibung der
Ausgabeschlüssel; Werte, Mehrfachwerte, Pflichtfelder und vollständige
Ausgabe-Hashes bleiben geprüft. Leere `RevokedKeys` erscheinen in der exakt
gebundenen Version `10.5p1` nicht als Zeile. Diese Schreibweise wird nur mit
einem frischen `/dev/null`-Parse desselben kanonischen Daemons als deaktiviert
anerkannt. Die Pfade von `sshd-auth` und `sshd-session` müssen genau aus dessen
unabhängig gebundenem OpenSSH-Paket stammen. Numerische Reihenfolge-Kommentare
in erzeugtem PAM-Text werden nur im verifizierten Fingerprint vereinheitlicht;
Regeln, tatsächliche Reihenfolge, vollständige Quellen und Metadaten bleiben
an die unabhängig zusammengesetzten offiziellen Module gebunden.

Nur die feste Release-Migration von OpenSSH `10.2p1` auf `10.5p1` kann die exakt
hinterlegten WebAuthn-Ergänzungen in vier Algorithmus-Defaults und den
`PerSourcePenalties`-Default mit `invaliduser` akzeptieren. Beide Seiten müssen
den frisch beobachteten Defaults ihrer eigenen kanonischen Daemons exakt
entsprechen. Eine konfigurierte Abweichung, ein anderes Versionspaar oder ein
weiterer Default-Wechsel wird abgewiesen. Normale Batches erhalten keine solche
semantische Ausnahme. Die [offiziellen OpenSSH-10.5-Releasehinweise](https://www.openssh.com/txt/release-10.5)
beschreiben diese Upstream-Änderungen; die Freigabe beruht zusätzlich auf den
konkreten gebundenen Modulen und frischen lokalen Daemon-Ausgaben. Parser- und
Adapterproben beweisen weder erfolgreiche Authentifizierung noch einen
Systembuild, Boot oder Hardwaretest. Die vollständige Vier-Host-Richtlinienprüfung
und alle System-Gates bleiben erforderlich.

The separately reviewed 25.11-to-26.05 migration adapter also proves two finite official service defaults against independently composed locked NixOS modules: the new empty `wpa_supplicant` system account/group and the exact final fwupd refresh rule. It binds credential-free canonical accounts, effective names and non-login shell, empty supplementary/group memberships, plain package/output identities, wireless control flags and canonical service/unit artifacts. The fwupd rule grants only `get-remotes` and `refresh-remote` to the existing `fwupd-refresh` system account; its complete stanza must occur once at the end, with the existing policy prefix unchanged. Both locked module revisions are explicitly hashed. All other users, groups, SSH authorization data and Polkit policy stay in the compared fingerprint. Ordinary batches retain their original strict policy comparison. Synthetic fixtures prove the narrow predicates; fresh four-host policy gates and the exact bootstrap build remain required before any separately approved installation.

The automatic-interface flag is version-specific: locked 25.11 has no `networking.wireless.autoDetectInterfaces`, while locked 26.05 defines a boolean whose standalone WPA default is `true`. The flag is not seeded into independent composition. The new side must equal its independently composed effective official value, which is `false` in the exact NetworkManager-managed WPA mode below, with strict boolean typing even when wireless is disabled. The old side never reads a nonexistent option. This correction does not admit custom wireless behavior or alter ordinary-batch policy.

Independent service composition also receives the typed supported-locale list, which remains in the compared migration fingerprint. The actual generated locale archive must have the same complete package/output identity as the independently composed clean package, and the complete global service environment must match. Raw environment values, archive paths and unit text are never seeded. Full unit text/path/metadata and vendor-byte comparisons remain required, including locale-dependent service lines.

The finite NetworkManager/WPA transition is independently composed as a coupled official module configuration. Only enabled NetworkManager with an empty unmanaged list, WPA backend and empty declarative WPA networks receives this projection: the locked old module leaves WPA disabled, while the new module enables DBus-controlled WPA with automatic interface detection disabled. Actual derived flags must equal the independently generated effective values; they are never copied into the seed. NetworkManager selectors, interface lists and locales remain in the compared fingerprint, and a plain canonical NetworkManager package is required. Outside this exact mode the previous strict wireless comparison remains. Both NetworkManager module revisions are explicitly hashed; all account, unit, credential, provider, vendor and authored-policy checks continue unchanged.

The fixed release-migration proof explicitly realizes the exact complete system-unit tree in its existing bounded build before compiler-free policy evaluation. This prevents a fresh migration from relying on a historical full-host cache for the fwupd vendor-file check. The exact aggregate output is also required in the returned build roots; missing, substituted or additional outputs fail. Ordinary batches retain their previous SSH/configuration/selected-unit output tuple. All source, vendor-byte, unit-selection and authentication comparisons remain unchanged; no build fallback is enabled.


### Durchsuchbare Root-Quellen beim Niri-Gate

Der Niri-Prüfhelper öffnet ausschließlich Parent-Verzeichnisse mit Linux
`O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`. Dadurch kann der gewöhnliche
Prüfbenutzer die absichtlich nur durchsuchbaren Root-Quellnamespaces `0711`
passieren. Die abschließenden Source-Verzeichnis- und Dateiöffnungen behalten
`O_RDONLY`; Symlink-, Größen-, Inode- und Race-Prüfungen bleiben erhalten.
Fehlendes `O_PATH` wird abgewiesen. Root-Journale und Worker mit `0700` bleiben
privat; es werden keine Berechtigungen erweitert.

Die Root-Prüfung des Kandidaten `20261011T051536Z-ea164243` bestand die
Migrationsrichtlinien, den vollständigen Flake-Check und alle drei App-Proben,
stoppte anschließend aber bereits vor der Niri-Fixture-Erstellung mit `EACCES`.
Sie integrierte und aktivierte nichts. Gewöhnliche UID mit eigenem `0111`-Parent
und eine tatsächliche Root-Fixture mit Wechsel auf UID/GID `65534` prüfen den
gezielten Zugriff sowie weiterhin verweigerte private Verzeichnisse,
Parent-Symlinks und übergroße Dateien. Diese Regressionstests ersetzen weder
die frische Vorbereitung nach dem gesonderten Bootstrap noch die unabhängige
Root-Transaktion des neuen Paketkandidaten.
