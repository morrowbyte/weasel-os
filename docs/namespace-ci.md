# CI auf boat.dev

Seit 2026-11-08 gilt für `morrowbyte/weasel-os`: CI-Ausführung ausschließlich
auf Boat. GitHub löst Jobs aus und speichert Status, Logs und Artefakte.
Es gibt keinen Blacksmith-, GitHub-hosted- oder Laptop-Fallback. Frühere
Variablen `CI_RUNNER`, `BLACKSMITH_RUNNER` und `BLACKSMITH_ENABLED` werden
nicht mehr zur Runnerwahl verwendet. `CI_ENABLED=true` bleibt die bestehende
Freigabe der beiden Nix-Workflows.

## Ressourcen und Fehler

Alle Jobs verwenden wörtlich `runs-on: boat`: 4 vCPU, 8 GiB RAM, eine frische
Ubuntu-VM pro Job. Boat löscht die CI-VM nach dem Job. Die gemeinsame
Concurrency-Gruppe `weasel-os-boat-ci` mit `queue: max` serialisiert die drei
normalen Workflowläufe; innerhalb der Hostmatrix gilt zusätzlich `max-parallel: 1`.
GitHubs verwaltete Copilot-Sitzungen übernehmen diese Concurrency nicht.
Wartende Läufe bleiben bis zur GitHub-Grenze von 100 Einträgen erhalten.
Neue Pushes brechen laufende Jobs nicht ab.

Zeitlimits: 30 Minuten für Computer Use und Copilot-Setup, 180 Minuten je
Nix-Host. Kapazitäts-/Startlimits, fehlende App-Freigabe oder Speicher müssen
sichtbar warten oder fehlschlagen. Keine Ausweichrunner. Größere Maschinen
oder Budgets brauchen eine eigene Entscheidung. Die Crew-Entwicklungs-VM
und die kurzlebigen GitHub-CI-VMs sind getrennt; das Crew-Projektlimit ersetzt
keine CI-Serialisierung.

## Workflowinventar

| Einstieg | Ausführung und Ergebnis |
| --- | --- |
| `namespace.yml`: Main-Push/PR bei Nix-, Lock-, Workflow-/Skriptänderungen | Beide Hosts auswerten, bestehendes `nix flake check`, unveränderte Lockdatei prüfen. |
| `namespace.yml`: Dispatch `build=false` | Dieselben Hostprüfungen. |
| `namespace.yml`: Dispatch `build=true` | Zusätzlich beide ausgewerteten Systemderivationen bauen. Kein Switch/Release. |
| `namespace.yml`: `probe_only=true`, mit beiden `build`-Werten | Runnerherkunft und Speicher; ausdrücklich kein Host-/Buildnachweis. |
| `computer-use.yml`: Pfad-gefilterter Push/PR und Dispatch | Beide installierten CU-Pakete bauen/testen, Closure exportieren, Prüfsummen und Commit als 7-Tage-Artefakt. Kein Import/keine Aktivierung. |
| `copilot-setup-steps.yml`: Pfad-gefilterter Push/PR und Dispatch | Boat-Herkunft und Nix-Setup; keine eigenständige Agentensitzung. |
| `dynamic/copilot-swe-agent/copilot` | GitHubs verwalteter Coding-Agent liest den Job `copilot-setup-steps` vom Default-Branch; dort ist Boat festgelegt. |

Keine lokalen Composite Actions, `workflow_call`, `workflow_run`,
`repository_dispatch`, geplanten Releasejobs oder referenzierten reusable
Workflows vorhanden. Externe Checkout-, Nix-, Cache- und Upload-Actions bleiben
auf ihre bisherigen vollständigen Commit-Hashes fixiert. Neue Workflows müssen
dieselbe Runnerregel erfüllen.

## Herkunft und Abnahme

`.github/scripts/assert-boat-runner.py` verweigert unerwartete Runner oder
einen vom Workflow-SHA abweichenden Checkout. `boat-runner.json` enthält
SHA, Run/Attempt, Job, Runner und Plattform und wird separat hochgeladen.
Das ist keine kryptografische Attestierung. Echte Abnahme gleicht zusätzlich
GitHub-Job-ID und Runnername mit dem Sandbox-Eintrag aus
`GET /ci/jobs/{jobId}` ab.

Am 2026-11-08 bestätigte die bestehende Boat-CI-App Zugriff auf
`morrowbyte/weasel-os`; öffentliche Läufe sind nur für `EvilWeasel` erlaubt.
Schlüssel bleiben im privaten Store; keine Boat-/SSH-Schlüssel in CI kopieren.
Andere Autoren werden gegebenenfalls sichtbar abgewiesen. Diese
Kosten-/Zugriffsgrenze wird nicht automatisch erweitert.

Die ersten echten Läufe des signierten Routingstands `62a3ad13` wurden am
2026-11-08 noch vor einer Runnerzuteilung von der zentral geerbten
Action-Allowlist abgewiesen (`startup_failure`). Die bereits verwendeten
Checkout-/Upload-/Nix-/Cache-SHA-Pins fehlen dort. Eine repo-lokale Ergänzung
wurde mit HTTP 409 abgelehnt; eine zentrale Abfrage mit HTTP 403.
Vor weiterer Abnahme muss der Policy-Eigentümer die vier exakten Pins aus
den Workflows additiv erlauben, ohne Wildcards, allgemeine Freigabe oder
Wechsel auf veränderliche Tags. Danach dieselben fehlgeschlagenen Läufe
wiederholen. Diese Ausgangsläufe sind kein bestandener Routingnachweis.

GitHub verlangt für Copilot auf Self-hosted-Runnern eine kompatible externe
Netzwerkabsicherung und deaktivierte integrierte Copilot-Firewall. Dieser
Auftrag verändert die Firewall nicht. Ein erfolgreicher Setup-Dispatch
belegt deshalb noch keine vollständige Copilot-Sitzung. Ein solcher Blocker
darf nicht durch `ubuntu-latest` umgangen werden. Die Konfiguration betrifft
Copilot auf GitHub Actions, keine separat gewählten Cloud-Sandboxes.

**Offene verwaltete Copilot-Abnahme:** GitHub kann die Repo-Runnerwahl durch
eine Organisationsrichtlinie überschreiben und startet den Agenten auch nach
fehlgeschlagenen Setup-Schritten. Der Receipt-Guard ist dort daher keine
Agentenstartsperre. Vor einer Boat-only-Gesamtabnahme muss lesend bestätigt
sein, dass die Organisation die Repo-Runnerwahl zulässt oder Boat erzwingt.
Zusätzlich fehlt eine wirksame Begrenzung verwalteter Sitzungen gegen parallele
CI. Solange diese Voraussetzungen nicht belegt sind, bleibt dieser verwaltete
Pfad offen; weder die Organisationsrichtlinie noch Copilot werden hier
deaktiviert oder außerhalb des Repo-Auftrags umgestellt.

## CD bleibt offen

Diese Workflows veröffentlichen keine Releases und deployen keine Systeme.
Das CU-Artefakt ist ein Build-Ergebnis. Lokale Updatewege und der kleinste
sichere Folgeauftrag stehen in [boat-cd-handoff.md](boat-cd-handoff.md).
Solange dort lokal kompiliert wird, ist die gesamte Umstellung nicht fertig.
Identitäts-, Signatur-, Review- und Aktivierungsgates bleiben unverändert.
Grüne CI ist keine Aktivierungsfreigabe.

Referenzen: [Boat CI](https://docs.boat.dev/ci),
[GitHub-Concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency),
[Copilot-Umgebung](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-environment).
