# Offene CD-Umstellung: Boat-Artefakte geschützt annehmen

Stand 2026-11-08. Abgegrenzter Vorschlag, keine implementierte oder
aktivierte Änderung des privilegierten Updaters.

## Bestehende Ausführungsorte

| Pfad | Aufgabe | Boat-only-Status |
| --- | --- | --- |
| `scripts/weasel-update-prepare.py`, `build()` | `nix build --max-jobs 1 --cores 2`; Einzel-/Batchvorbereitung baut System und Apps lokal. Hostevaluation und Flakecheck können IFD realisieren. | Offen: nach Boat verlagern. |
| `scripts/weasel-update-activate.py`, `nix_build()`, `build_application()`, `activate()` | Nach Signatur-/Baselineprüfung erneut Host-/App-Builds, Flakecheck und Anwendungstests am Ziel. | Offen: Produktion nicht Boat-only. |
| `scripts/weasel-update-migration.py`, SSH-Policy-Prüfung | Baut ausgewählte SSH-, PAM- und Unit-Artefakte mit IFD. | Auf Boat qualifizieren; lokale Prüfung ohne Ersatzbuild. |
| `scripts/weasel-update-activate.py`, `check_*`, Signatur-/Manifest-/Closureprüfung, `activate_exact()` | Identität, aktives System, direkte Abstammung, autorisierter Signierer, Freigabe, Drift, Sperre, Snapshot, geschützter Switch und Zustand danach. | Auf dem Ziel erhalten; keine Aktivierung beauftragt. |
| `programs/t3code-fork.nix`, stündlicher `t3code-weasel-update --update` | Extern gepinnter Installer aus `morrowbyte/t3code`, Revision `a37df4a04247fea01340b2546e99bb4c4f6ffb1b`. | Indirekter Updateweg; dessen Nix-Realisierung separat vor Gesamtabschluss bewerten. |

Manuelle `fr`/`fu`-Befehle sind keine GitHub-CI und werden hier nicht ausgeführt.

## Kleinster sicherer Umbau

1. Bestehende Kandidatenvorbereitung behält Quellfreeze, Auswahl, Review und
   signierte direkte Abstammung. Ein begrenzter Boat-Job bekommt genau diesen
   Commit, Lock-Hash, Baseline, Zielhost und Modus. Dort laufen bisherige
   Build-/Testschritte einschließlich SSH-Policy-Ausgaben. Keine Aktivierung.
2. Vollständige Closures plus Manifest exportieren: Commit/Tree/Lock-Hash,
   Baseline, Host, Derivations-/Outputpfade, Nar-Hashes, Archivprüfsummen,
   tatsächliche Tests und GitHub-Job-/Boat-Sandbox-Zuordnung. Frei schreibbares
   `source-commit.txt` allein ist kein Vertrauensnachweis.
3. Versionierter Annahmeadapter erweitert Kandidatenschema und Root-Prüfung
   in `weasel-update-prepare.py` und `weasel-update-activate.py`. Nur Artefakte,
   die an den freigegebenen Kandidaten gebunden und durch einen ausdrücklich
   vertrauten Signierer oder gleichwertige Attestierung belegt sind, annehmen.
   Keine globalen Signierschlüssel auf Boat kopieren.
4. Nach verifiziertem Import Hashes, vollständige Closure, Host-/Systemidentität
   und sämtliche lokalen Sicherheitsgates erneut prüfen. Ziel-Evaluation mit
   `--option allow-import-from-derivation false`; fehlende Ausgaben ablehnen.
   Keine lokale Kompilation und kein Compile-Fallback. Notwendige Tests des
   realen Zielzustands bleiben als lokale Abnahme getrennt.
5. Falscher SHA/Lock/Host/Nar/Signierer, unvollständige Closure, geänderte
   Baseline, abgelaufene Freigabe und parallele Transaktion müssen abweisen.
   Isolierter Pilot, anschließend separat autorisierte Installation des
   geprüften Helfers. Installierte Root-Helfer bleiben hier unverändert.

Das ist ein eigener sicherheitsrelevanter Helferumbau mit neuem
Artefakt-Vertrauensvertrag. `nix build` durch einen beliebigen Store-Pfad oder
nur `--max-jobs 0` zu ersetzen, reicht nicht. Der Eigentümer der Updaterpfade
muss den Vorschlag übernehmen, bevor dort geschrieben wird.
