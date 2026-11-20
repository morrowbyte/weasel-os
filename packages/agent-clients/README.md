# Codex CLI, ChatGPT und Crew für NixOS

Das Profil installiert drei Programme für `x86_64-linux`. Im Anwendungsmenü öffnet **Codex CLI** eine eigene Foot-Sitzung. **ChatGPT** und **Crew** starten die jeweilige Desktop-App. Auch aus einem Terminal lassen sich alle drei starten.

| Menüeintrag | Befehl | Quelle am Projekt-Pin |
| --- | --- | --- |
| Codex CLI | `codex` | 0.146.0 aus [nixpkgs](https://github.com/NixOS/nixpkgs/blob/718f670e845328e9c2cecdd832835712b2cbd065/pkgs/by-name/co/codex/package.nix) |
| ChatGPT | `chatgpt` | OpenAI-RPM 26.1111.21308, SHA-256 und Hersteller-Menüeintrag aus [packages/chatgpt](../chatgpt/default.nix) |
| Crew | `t3code-morrowbyte` | AppImage 0.1.11-nightly.20261118.131 von [morrowbyte/t3code](https://github.com/morrowbyte/t3code/releases/tag/v0.1.11-nightly.20261118.131) |

## Einbindung

Im Hostmodul unter `hosts/<name>/` wird das Profil so eingebunden.

```nix
{ lib, pkgs, ... }:
{
  nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "chatgpt";
  environment.systemPackages = [ (pkgs.callPackage ../../packages/agent-clients { }) ];
}
```

Der Systembau bezieht die Programme aus öffentlichen Quellen. Die Clients richten beim Start im Benutzerkonto ihre eigenen Profile ein und führen die Anmeldung über ihre jeweilige Oberfläche durch. Das Profil enthält keine Benutzerdaten. Das Hostmodul verwaltet die gemeinsame Codex-Konfiguration.

Beim ersten Desktopstart in einem leeren Crew-Profil verwendet der lokale Codex-Provider `/run/current-system/sw/bin/crew-codex`. Das Agent-Profil liefert dafür die von Crew .131 für Linux x64 gepinnte Distribution **0.163.0-morrowbyte.2** mit Code-Mode-Host und Ripgrep. Der Pfad folgt dem aktiven NixOS-System. Bereits eingerichtete Profile behalten ihre Einstellungen und ihre verwalteten Installationen. Die separate Codex CLI im Anwendungsmenü verwendet weiterhin das Paket aus nixpkgs. [Runtime-Release](https://github.com/morrowbyte/t3code/releases/tag/codex-runtime-0.163.0-morrowbyte.2) · [Versionsprüfung in Crew .131](https://github.com/morrowbyte/t3code/blob/f3dff0bb10d4661d9da9a69c616c90e330693bc8/apps/server/src/provider/model-manifest.json)

## Crew aktualisieren

Bei einem neuen Morrowbyte-Release Version und SHA-256 in [crew.nix](./crew.nix) aktualisieren. Für den obigen Pin stimmen der Digest des GitHub-Assets und der AppImage-Eintrag in den [Release-Prüfsummen](https://github.com/morrowbyte/t3code/releases/download/v0.1.11-nightly.20261118.131/SHA256SUMS) überein. Nix prüft beim Abruf die Bytes gegen den Pin.

Die Derivation übernimmt Extraktion, Laufzeitbibliotheken und Accessibility-Startoption aus dem [NixOS-Rezept des Releases](https://github.com/morrowbyte/t3code/blob/v0.1.11-nightly.20261118.131/packaging/nixos/flake.nix). Crew erhält Aktualisierungen über das NixOS-Profil. Der Link-Handler verwendet dessen systemweiten Launcher. Der separat angebotene Closure-Download ist ein komprimierter `nix-store --export` für den manuellen Store-Import. [Release-Bauskript](https://github.com/morrowbyte/t3code/blob/v0.1.11-nightly.20261118.131/scripts/morrowbyte/build-nixos-closure.sh)

## Prüfung

Der Profilbau prüft die drei ausführbaren Befehle und ihre Menüdateien. Für die Abnahme im installierten Wayland-Gast die drei Menüeinträge als frischer Benutzer starten und bei den grafischen Clients bis zur Ersteinrichtung gehen. Der [Katalogtest](../agent-desktop/checks/client.py) prüft separat den Codex-App-Server und seine MCP-Werkzeuge.
