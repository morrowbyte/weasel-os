# Speicher, Backups und Recovery

Stand: 11. Oktober 2026. Host: `nixy-laptop`. Die Konfiguration liegt in
`modules/storage-recovery.nix`, `modules/home-snapshots.nix` und
`programs/cloud-backup.nix`; Backup-Ausnahmen in `config/backup-excludes.txt`.

## Btrfs-Reserve und Projekt-Ausschlüsse

Die aktive Erweiterung liegt in `modules/btrfs-reserve.nix`,
`programs/storage-health.nix` und den beiden `scripts/weasel-*.py`-Helfern.
Nach dem abgeschlossenen 26.05-Update wurde sie integriert und aktiviert.
Die folgenden Dienste laufen auf dem Laptop; Desktop-Konfiguration unverändert.

`weasel-btrfs-health` misst alle 15 Minuten getrennt Dateisystem-Freiraum,
unzugewiesenen Geräteplatz, belegte Metadaten und temporäre Reservierungen.
Reservierungen werden nicht als zusätzliche belegte Metadaten-Prozentzahl
ausgegeben. Der lesbare Messbeleg liegt unter
`/run/weasel-storage-health/status.json`; private Historie des letzten Laufs unter
`/var/lib/weasel-storage-health/last.json`. Zwei aufeinanderfolgende kritische
Messungen lösen die gedrosselte Benutzerbenachrichtigung aus. Der bekannte alte
Minuten-Timer wird bei Home-Manager-Aktivierung deaktiviert, seine Dateien und
der pausierte Factorio-Companion-Code bleiben erhalten.

`weasel-btrfs-reserve` prüft stündlich: unter 40 GiB unzugewiesenem Platz beginnt
eine kleine, datenbezogene Balance; ab 50 GiB wird beendet. Dies sind lokale
Betriebswerte, keine Btrfs-Garantie. Zuerst werden leere Datenbereiche freigegeben,
danach höchstens drei Durchläufe mit je acht schwach belegten Datenbereichen.
Metadaten-DUP bleibt erhalten. Gerätefehler, weniger als 30 GiB freier Speicher,
aktive Builds, eine bestehende Balance oder ein nicht abgeschlossener Update-
oder Aktivierungsvorgang verhindern die Wartung. Ein laufendes Update hält
denselben Aktivierungs-Lock. Einzelne Durchläufe haben ein Zeitlimit; bei dessen
Überschreitung wird die selbst gestartete Kernel-Balance ausdrücklich abgebrochen.
Ein Lauf kann deshalb unter dem Ziel bleiben und meldet seinen tatsächlichen
Ausgang. Er löscht keine Dateien und vergrößert keine SSD.

Home-Snapshots sind nicht rekursiv über verschachtelte Subvolumes. Deshalb
bekommen **erkannte Buildverzeichnisse** eigene Subvolumes an ihrem bisherigen
Pfad. Quellcode, `.git`, Saves, Testnachweise und Anwendungszustand bleiben in
Home-Snapshots; die Restic-Backup-Ausnahmen gelten davon unabhängig.
Keine allgemeinen Ausschlüsse von `build`, `dist`, `bin`, `obj`, `Documents`,
`.t3`, `.local/state` oder ganzen Projekten.

```bash
weasel-btrfs-health check             # nur lesen, keine Zustandsdatei/Balance
weasel-snapshot-excludes              # Inventar, keine Migration
sudo weasel-snapshot-excludes --migrate /home/evilweasel/source/PROJEKT/node_modules
```

Migration erfolgt **erst nach Freigabe der laufenden Update-Transaktion**.
Der Helfer prüft erkannte Manifest-/Cache-Merkmale, Git-verfolgte Dateien,
Symlink-Komponenten, laufende Builds und offene Prozesszugriffe. Er kopiert per
Reflink, prüft Inhalt per Rsync-Prüfsumme einschließlich Rechten, Hardlinks und
erweiterten Attributen, erhält den Pfad und entfernt nur das danach verifizierte
Duplikat. Private Phasenjournale unter
`/var/lib/weasel-storage-health/migrations` erhalten Fehler/Abbrüche. Nach einem
Abbruch die dort aufgezeichneten Original-/Zwischenpfade prüfen und erhalten;
nicht blind neu ausführen oder pauschal Zwischenverzeichnisse löschen. Bereits
migrierte Verzeichnisse werden im Inventar übersprungen.

Wichtig für Recovery: In neuen Home-Snapshots erscheint ein ausgeschlossener
Buildpfad leer. Beim Zurückrollen diese Subvolumes erhalten oder erneut am
ursprünglichen Pfad bereitstellen. Alte Snapshots halten ihre alten Cache-Blöcke
bis zur normalen Aufbewahrungsbereinigung fest; eine Migration schafft daher
nicht sofort 12 GiB freien Platz. Ein Werkzeug, das den ganzen Cache-Ordner
löscht und neu erstellt, kann die Subvolume-Grenze beseitigen. Nach `cargo clean`
oder einer entsprechenden Neuinstallation erneut inventarisieren und einen
gegebenenfalls normalen Ordner im ruhenden Zustand erneut migrieren.

Am 11. Oktober wurden alle 24 Verzeichnisse tatsächlich migriert. Ein echter
read-only Home-Snapshot bestätigte sämtliche Ausschlüsse; die Live-Caches und
Projektquellen bleiben vorhanden. Aktivierung und Migration sind abgeschlossen.

Die erste Bestandsaufnahme vom 10. Oktober 2026 ergab 24 erkannte Verzeichnisse
mit nominell 12,37 GiB. Dies ist keine Messung exklusiv freigebbarer Bytes.
Die großen Factorio-Akzeptanzartefakte und Computer-Use-Nachweise wurden nicht
als Cache eingestuft. Home braucht für diese Änderung keine neuen Projektpfade
und keine pauschale Neuorganisation.

Grundlagen: [Btrfs Balance und ENOSPC](https://btrfs.readthedocs.io/en/latest/btrfs-balance.html#enospc)
und [verschachtelte Subvolumes](https://btrfs.readthedocs.io/en/latest/btrfs-subvolume.html#nested-subvolumes).

## Warum die SSD voll war und was geändert wurde

20 Home-Snapshots hielten viele inzwischen überschriebene oder gelöschte Daten
fest. 17 alte Snapshots wurden entfernt. Direkt danach sank die Belegung von
94 % auf 77 %, und der freie Platz stieg von etwa 62 auf 216 GiB. Die großen
manuellen Update-Snapshots unterlagen bisher keiner allgemeinen Aufbewahrung.
Projektdateien, lokale Build-Caches, installierte Spiele und originale Saves
wurden nicht gelöscht. Eine frühere kleine Daten-Balance schuf wieder mehrere
GiB unzugeteilten Btrfs-Spielraum; sie vergrößert nicht den Nutzspeicher.

Ziel ist weniger als 80 % Belegung. Das ist ein Zielwert, kein harter garantierter
Deckel: neue Daten oder große Builds können ihn überschreiten. Snapshots haben
keine feste Größe: sie halten geänderte/gelöschte Blöcke fest. Btrfs kann einzelne
Verzeichnisse nicht einfach per Ausschlussliste aus einem Home-Snapshot nehmen.
Die kleine Aufbewahrung begrenzt die Dauer dieses Effekts; Cloud-Backups schließen
Caches ausdrücklich aus.

## Lokale Recovery-Punkte

* **Home:** täglich ein Timeline-Snapshot, höchstens zwei Timeline-Tagesstände;
  zusätzlich begrenzt `weasel-storage-retention` die allgemeinen Snapshots auf
  die drei neuesten. Manuelle Snapshots zählen mit. `weasel-retain=yes` kann
  einen bewusst festgehaltenen Snapshot schützen. Transaktionale Snapshots des
  täglichen Updaters (`weasel-daily-update=yes`) bleiben dessen eigener,
  nach erfolgreicher Transaktion begrenzter Aufbewahrung unterstellt.
* **NixOS:** vier neueste Generationen plus das aktuell laufende und das
  tatsächlich gebootete System. Deshalb kann die Anzahl vorübergehend größer
  als vier sein. Die erste Bereinigung behielt 216 und 234–237. Gebaut bedeutet
  nicht nachgewiesen bootfähig; `/run/booted-system` ist der echte Bootnachweis.
* **Bereinigung:** täglich, mit bis zu 15 Minuten Verzögerung; verpasste Läufe
  werden nachgeholt. Unabgeschlossene Updates blockieren die Bereinigung.
* **Nix Store:** samstags um 12:00 Uhr, bis zu 30 Minuten Verzögerung, höchstens
  40 GiB unreferenzierte Store-Objekte pro Lauf. Aktive Generationen, GC-Roots,
  `result`-Links und laufende Builds bleiben geschützt. Nix-Derivationen der
  lebenden Outputs bleiben erhalten. Cargo-Targets, `node_modules`, virtuelle
  Umgebungen und sonstige Projekt-Buildverzeichnisse werden nicht angefasst.
* **`ncg`:** auf dem Laptop verwendet der Befehl diese geschützte Aufbewahrung,
  statt alle alten Generationen zu entfernen.

```bash
sudo weasel-storage-retention             # konkreter Plan ohne Bereinigung
sudo snapper -c home list
sudo nix-env -p /nix/var/nix/profiles/system --list-generations
df -h /
sudo btrfs filesystem usage -T /
```

## Automatische Proton-Drive-Backups

Der Cloud-Bereich ist `/my-files/Weasel-Recovery/nixy-laptop-restic-v1`.
Restic verschlüsselt, komprimiert, dedupliziert und versioniert die Dateien;
die offizielle Proton Drive CLI 0.9.0 überträgt die Repository-Objekte mit
Protons Ende-zu-Ende-Verschlüsselung. Ein lokaler, nur auf Loopback erreichbarer
REST-Adapter verbindet beide. Ein zufälliger Token schützt jeden Lauf; er wird
nicht protokolliert. Dieser Adapter ist eigener Repository-Code und muss bei
CLI-Änderungen erneut mit einem echten Restore geprüft werden.

* **Systemzustand nach Bedarf:** Erst innerhalb eines tatsächlichen System-Backups
  erstellt der Root-Helfer einen schreibgeschützten Root-Btrfs-Snapshot für
  `/etc`, `/root`, `/var/lib` und `/var/spool`. Installierte Flatpaks,
  Nix-Verwaltung, Coredumps und Root-Caches sind von der Übertragung ausgenommen.
  VM-Zustand und registrierte VM-Datenträger bleiben eingeschlossen. Der
  temporäre Snapshot wird auch nach Übertragungs-/Prüffehlern entfernt; der
  lebende Systemzustand bleibt erhalten. Der bisherige 02:30-Timer entfällt: Ein
  wartender Ganz-Root-Snapshot hatte gelöschte Store-/VM-Blöcke weiter festgehalten.
  Ein bewusst manuell erstellter Snapshot kann mit
  `sudo weasel-system-state-export cleanup` wieder entfernt werden.
* **03:30 Uhr:** persönliches Home inklusive Dokumenten, Quellcode, Git-Historie,
  App-Zustand, Zugangsdaten, lokalen AI-/T3-Verläufen, Spielständen und Mods.
  Anschließend sichert ein Root-Restic-Prozess den frisch aufgenommenen
  Systemzustand direkt durch denselben Proton-Transport. Bis zu zehn Minuten
  Verzögerung; verpasste Läufe werden nachgeholt. Die Erfolgsmarkierung wird
  erst nach beiden Datenbeständen und Repository-Check geschrieben.
* **Ausgenommen:** lokale Snapshots, temporäre Daten, bekannte Caches,
  installierte Steam-Spieldateien, Shadercache, heruntergeladene LM-Studio-Modelle,
  `node_modules`, Rust-Debug-/Release-Artefakte und virtuelle Umgebungen.
  Steam-`userdata`, `compatdata`, Saves und eigene Mods bleiben eingeschlossen.
  Keine pauschalen Ausschlüsse für `build`, `bin`, `dist` oder `obj`, weil dort
  auch eigene Dateien liegen können. Backup-Ausnahmen löschen lokal nichts.
* **Sonntag 06:00 Uhr:** entfernte Datenstichprobe (5 %), dann Aufbewahrung von
  sieben Tages-, vier Wochen- und drei Monatsständen und Entfernen der nicht
  mehr benötigten Repository-Objekte. Das beginnt erst nach erfolgreichem
  Erstbackup und echtem Restore-Nachweis. Keine fremden Drive-Dateien und kein
  accountweiter Papierkorb werden gelöscht.
* **Lokal:** nur Restic-Metadaten-Cache und vorübergehende Upload-/Download-Packs,
  keine vollständige zweite Kopie des Cloud-Repositorys. Abbrüche behalten keine
  Erfolgsmarkierung. Gleichzeitige Cloud-Jobs werden durch ein Lock verhindert.
* **Fehler:** Desktop-Meldung; private Diagnose in
  `~/.local/state/weasel-backup/last-restic.log`. Die Drive-Sitzung liegt im
  Betriebssystem-Keyring. Bei abgelaufener Sitzung oder gesperrtem Keyring kann
  eine erneute Browser-Anmeldung erforderlich sein; das wird nicht umgangen.

Das Home wird im normalen Backup live gelesen. Restic meldet unlesbare oder
während des Lesens problematische Dateien als fehlgeschlagenen Lauf. Für eine
gezielte Wiederherstellung von Datenbanken die betreffende App vorher schließen;
die Funktionsfähigkeit jeder laufenden Anwendung kann ein generischer Dateibackup
nicht garantieren. Der Root-Snapshot ist ein konsistenter Dateisystemstand, ersetzt
aber keine anwendungsspezifische Datenbankprüfung. Laufende VMs werden nur als
Dateisystemstand gesichert; für einen garantiert sauberen VM-Stand die VM vorher
geordnet herunterfahren oder ihre eigenen Sicherungsfunktionen verwenden.

```bash
systemctl --user list-timers 'weasel-cloud-backup*'
systemctl list-timers 'weasel-storage-*' 'weasel-system-state-export*'
systemctl --user status weasel-cloud-backup
cat ~/.local/state/weasel-backup/last-success.json
weasel-cloud-backup snapshots
weasel-cloud-backup check
```

`last-success.json` beweist einen abgeschlossenen Backup-Lauf, nicht allein einen
gestarteten Timer. Der Erstlauf kann je nach Datenmenge und Upload viele Stunden
dauern. Bei zu wenig Drive-Kapazität schlägt er sichtbar fehl; es werden keine
Credits oder Speicherabonnements gekauft.

## Kleine Probleme lokal beheben

Bei einem Systemproblem im Bootmenü eine erhaltene Generation wählen. Für den
aktuell gebooteten Referenzstand:

```bash
readlink /run/booted-system
sudo nix-env -p /nix/var/nix/profiles/system --list-generations
```

Nach erfolgreichem Test einer Generation kann sie dauerhaft gewählt werden:

```bash
sudo nix-env -p /nix/var/nix/profiles/system --switch-generation NUMMER
sudo /nix/var/nix/profiles/system/bin/switch-to-configuration switch
```

NixOS-Rollback setzt Home und `/var` nicht zurück. Für eine einzelne persönliche
Datei zuerst den Snapshot auswählen und **als separate Datei** zurückholen:

```bash
sudo snapper -c home list
sudo cp --reflink=auto --preserve=all \
  /home/.snapshots/NUMMER/snapshot/evilweasel/RELATIVER_PFAD \
  /home/evilweasel/RELATIVER_PFAD.recovered
```

Erst vergleichen, dann die Anwendung schließen und die gewählte Datei ersetzen.
Keinen vollständigen Home-Snapshot blind über den aktuellen Arbeitsstand kopieren.

## Cloud-Restore und vollständiger Laptopverlust

Die Recovery-Passwortdatei liegt lokal privat unter
`~/.local/share/weasel-backup/nixy-laptop-restic-password.txt` und zusätzlich im
privaten E2E-verschlüsselten Drive-Ordner `Weasel-Recovery`. Sie ist nicht in Git
oder in dieser Anleitung enthalten. Der Backup-Runner lädt sie nach einem
Laptopverlust automatisch aus Drive, wenn sie lokal fehlt. Das Proton-Konto
und dessen eigene Recovery-Methode müssen daher unabhängig erreichbar bleiben.

1. NixOS installieren und das `weasel-os`-Repo wieder auschecken.
2. Die Laptop-Konfiguration bauen und aktivieren. Kein alter Laptop-Key ist
   erforderlich, um die Git-Historie zu besitzen; SSH-Zugang zu privaten Repos
   gegebenenfalls über Proton wiederherstellen.
3. `proton-drive auth login` im Terminal starten, im Browser anmelden.
4. `weasel-cloud-backup snapshots` listet die vorhandenen persönlichen Stände.
5. In ein **neues** Verzeichnis wiederherstellen:

```bash
weasel-cloud-backup restore --snapshot latest --target /home/evilweasel/recovery-cloud
```

Die ursprünglichen absoluten Dateipfade liegen darunter, etwa
`recovery-cloud/home/evilweasel/Documents`. Einzelne Dateien zuerst prüfen und
danach übernehmen. Systemzustand ist ein eigener Snapshot-Tag:

```bash
weasel-cloud-backup snapshots --tag system-state
weasel-cloud-backup restore --tag system-state --target /home/evilweasel/recovery-system
```

Die gesicherten Systemdateien liegen darunter in
`recovery-system/var/lib/weasel-system-state/snapshot/`. Beim normalen Restore
als Benutzer bleiben die Inhalte prüfbar; für die endgültige Übernahme von
Root-Dateien müssen Besitz und Rechte mit Root-Rechten korrekt gesetzt werden.
Der Root-Restore-Probe prüft die tatsächliche Erhaltung von Eigentümer root und
Modus 0600 unter Root. Keine Zugangsdaten über die Konsole ausgeben.

Service-Datenbanken und Zugangsdaten gezielt übernehmen, während die betroffenen
Dienste gestoppt sind. Das generierte `/etc` und alte Nix-Dateien nicht pauschal
über eine frische deklarative Installation legen. Die gespeicherten Rechte sind
bei Systemdateien wichtig; Root-Daten erst nach Prüfung als Root installieren.

## Selten gebrauchte Dateien auslagern

Der frühere Archiv-/Eviktionsmodus von `weasel-cloud-backup` ist entfernt.
Bestehende Archive unter `/my-files/Weasel-Archive` und ihre Restorekataloge
bleiben erhalten. Vorhandene `.tar.zst`-Archive lassen sich über die offizielle
Proton-Drive-CLI herunterladen und in ein neues Verzeichnis entpacken.

Die mit `/` beginnenden Ausschlüsse in `config/backup-excludes.txt` sind relativ
zur Sicherungsquelle (standardmäßig Home). Der Helper normalisiert auch relative
`--source`-Pfade und erweitert diese Regeln vor Restic zu absoluten Pfaden.
Die Datei darf deshalb nicht unverändert an Restic übergeben werden.
Glob-Regeln wie `**/node_modules` bleiben unverändert.

Paperless ist stillgelegt. Die ursprünglichen Rohdaten bleiben lokal erhalten;
der verifizierte, lesbare Recovery-Stand liegt unter
`~/.local/share/weasel-retired-services/`. Nur der konkrete stillgelegte
Rohdatenpfad ist vom Home-Backup ausgeschlossen, nicht der Recovery-Stand und
keine anderen Quellen oder Datenbanken. Seine ursprünglichen Compose-Dateien
sind im Recovery-Archiv enthalten; es gibt keinen automatischen Neustart.

## Quellen und Transportprüfungen

* [Offizielle Proton Drive CLI](https://proton.me/support/drive-cli)
* [CLI-README und Keyring-Speicherung](https://github.com/ProtonDriveApps/sdk/blob/main/cli/README.md)
* [Restic REST-Backend-Protokoll](https://restic.readthedocs.io/en/v0.17.0/REST_backend.html)

Der Test `TEST_RESTIC=/pfad/zu/restic python3 tests/test-storage-recovery.py -v`
prüft echten Restic-Backup/Check/Restore gegen den lokalen Transport, Ausschluss
von `node_modules`, Symlinks, Zugriffsschutz und die geschützte Generationenwahl.
`weasel-cloud-backup probe --probe-system` prüft zusätzlich einen vollständigen echten
Cloud-Roundtrip mit zufälligen Bytes, einem Symlink und einer privaten Root-Datei
mit Besitz-/Rechteprüfung. Das ist ein kleiner
Restore-Nachweis, kein Nachweis über eine bereits vollständig gesicherte Home-Menge.
