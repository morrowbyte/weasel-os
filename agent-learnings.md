# Agent Learnings

Append-only log of implementation lessons for future agents working in this repo.

## Entry Format
- `Date`: YYYY-MM-DD
- `Change`: short description of what was changed
- `Pitfall/Root cause`: what could fail or what failed
- `Verification`: exact command(s) used

## Entries

### 2026-05-05 (AP diagnostics workflow)
- Date: 2026-05-05
- Change: Reworked `wifi-connect-ap-blain-local.sh` into a full AP diagnostics runner that switches to `AP_Blain_local`, captures NM/IP/DNS/ping/curl/journal evidence, and always reconnects to `Blain_Guest` afterward.
- Pitfall/Root cause: The earlier helper only reported MAC state; the actual failure looked like a DNS/routing issue, so the script now collects the data needed to distinguish DHCP, gateway, and resolver problems before returning to the guest network.
- Verification: `bash -n wifi-connect-ap-blain-local.sh`

### 2026-05-05 (wifi helper fix)
- Date: 2026-05-05
- Change: Hardened `wifi-connect-ap-blain-local.sh` so it uses a unique NM connection name, falls back to explicit wifi profile creation for secured APs, and reports MAC status from `/sys/class/net/<iface>/addr_assign_type` instead of the nonexistent `GENERAL.PERM-HWADDR` field.
- Pitfall/Root cause: `nmcli device show` on this version does not expose a permanent-hardware-address field, and reusing an existing WPA2 profile can surface `802-11-wireless-security.key-mgmt` errors when `nmcli dev wifi connect` tries to infer security from a stale profile.
- Verification: `bash -n wifi-connect-ap-blain-local.sh`

### 2026-05-05
- Date: 2026-05-05
- Change: Added a repo-root Wi-Fi helper script for `AP_Blain_local` that connects via `nmcli` and prints the current and permanent MAC address plus a simple static-vs-dynamic assessment.
- Pitfall/Root cause: The repo already contains Wi-Fi helper patterns, but the user needed a short copy-pasteable entrypoint instead of a long inline shell command.
- Verification: `bash -n wifi-connect-ap-blain-local.sh`

### 2026-03-31
- Date: 2026-03-31
- Change: Migrated the flake to `flake-parts`, split shared baselines into `common` plus client/server layers, added an `ew-cloud` VPS host with `disko`, `sops-nix`, Tailscale-only defaults, and a reusable OpenClaw role scaffold.
- Pitfall/Root cause: New files must be staged before Git-backed flake evaluation sees them; Home Manager users created inside NixOS do not automatically inherit package overlays when `home-manager.useGlobalPkgs = false`, so external packages such as `openclaw` must be passed explicitly; a first-boot `sops-nix` secret cannot rely on a host SSH key that does not exist yet.
- Verification: `nix flake show --all-systems`, `nix flake check`, `nix-instantiate --parse profiles/system/common.nix`, `nix-instantiate --parse profiles/system/server.nix`, `nix-instantiate --parse profiles/home/common.nix`, `nix-instantiate --parse profiles/home/server.nix`, `nix-instantiate --parse hosts/ew-cloud/config.nix`, `nix-instantiate --parse hosts/ew-cloud/disko.nix`, `nix-instantiate --parse modules/nixos/roles/openclaw.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.diskoScript.drvPath`.

### 2026-03-30
- Date: 2026-03-30
- Change: Enabled Tailscale once in the shared system base profile so all hosts inherit the service automatically.
- Pitfall/Root cause: Host-level placement would have duplicated the same networking service across multiple machine configs; the shared base profile is the right single source of truth for repo-wide services.
- Verification: `nix-instantiate --parse profiles/system/base.nix` and `nix eval --no-write-lock-file .#nixosConfigurations.<host>.config.system.build.toplevel.drvPath` for each affected host.

### 2026-02-24
- Date: 2026-02-24
- Change: Fixed laptop flake module wiring and added bridge netfilter sysctl values.
- Pitfall/Root cause: `nix-instantiate --parse` only validates syntax and does not catch invalid NixOS option paths like `configuration = { ... };`.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath` and `nix build --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel --dry-run`.

### 2026-03-02
- Date: 2026-03-02
- Change: Added Home Manager global Playwright setup on `nixy-laptop` (`pkgs.playwright-driver`, `PLAYWRIGHT_BROWSERS_PATH`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`).
- Pitfall/Root cause: Syntax parse alone does not validate Home Manager/NixOS option integration; use semantic evaluation for module changes.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-03
- Date: 2026-03-03
- Change: Stabilized `fu` updates by pinning `handy` input to a known-good revision and fixing Ventoy insecure-package allowlist handling.
- Pitfall/Root cause: `fu --update` pulled a newer `handy` revision with a broken Rust/Tauri dependency hash setup; after pinning, updates still failed because `permittedInsecurePackages` hardcoded an old Ventoy version (`1.1.07`) while nixpkgs moved to `1.1.10`.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath` and `nix eval --update-input handy .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-03 (follow-up)
- Date: 2026-03-03
- Change: Pinned Handy to its own fixed nixpkgs input (`handy-nixpkgs`) so `fu` updates of main nixpkgs channels do not repeatedly rebase/rebuild Handy.
- Pitfall/Root cause: Pinning only the Handy repo revision is not enough if `handy.inputs.nixpkgs` follows a moving channel; that still triggers frequent large rebuilds.
- Verification: `nix eval --update-input handy .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath` and `nix eval --update-input nixpkgs-unstable .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-07
- Date: 2026-03-07
- Change: Added a local `packages/t3code` subflake for the upstream AppImage and installed it only on `nixy-laptop`.
- Pitfall/Root cause: The repo expects package additions to live under `./packages` as their own flake, and syntax-only checks are not enough to validate cross-file flake wiring into a host package list.
- Verification: `nix-instantiate --parse flake.nix`, `nix-instantiate --parse packages/t3code/flake.nix`, `nix-instantiate --parse hosts/nixy-laptop/config.nix`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-11
- Date: 2026-03-11
- Change: Moved `nixy-laptop` Niri startup to a declarative `programs/niri.nix` config that starts DMS and no longer spawns Waybar, removed leftover Hyprland/Waybar Home Manager wiring, disabled DMS user-systemd startup, and dropped the manual portal override so `programs.niri.enable` can supply the upstream Niri portal defaults.
- Pitfall/Root cause: The live `~/.config/niri/config.kdl` was still explicitly spawning `waybar`, and the host-level `xdg.portal` override forced Hyprland/wlr portal settings that conflict with Niri's recommended `xdg-desktop-portal-gnome` setup. Also, new files must be `git add`ed before `nix eval` sees them through the local Git-backed flake source.
- Verification: `nix-instantiate --parse programs/niri.nix`, `nix-instantiate --parse hosts/nixy-laptop/home.nix`, `nix-instantiate --parse hosts/nixy-laptop/config.nix`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-14
- Date: 2026-03-14
- Change: Added a repo convention for temporary Nixpkgs/workaround overrides: keep them in a dedicated override path (for example `modules/overrides/`) instead of burying them in host config, and put the upstream issue/PR removal note directly in the module file.
- Pitfall/Root cause: Temporary fixes placed inline in host modules are easy to forget and harder to remove once upstream resolves the regression.
- Verification: `nix-instantiate --parse modules/overrides/linux-zen-preempt-fix.nix`, `nix-instantiate --parse hosts/nixy-laptop/config.nix`, `nix-instantiate --parse hosts/nixy-desktop/config.nix`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-14 (linux-zen PREEMPT follow-up)
- Date: 2026-03-14
- Change: Updated `modules/overrides/linux-zen-preempt-fix.nix` to match the upstream nixpkgs 6.18 preemption workaround by overriding `PREEMPT = no` and allowing `PREEMPT_LAZY` / `PREEMPT_VOLUNTARY` as optional selections.
- Pitfall/Root cause: Linux 6.18 changed preemption Kconfig handling, so forcing `PREEMPT = y` now fails config validation for `linux_zen`; disabling only `PREEMPT_LAZY` was insufficient because the old hard `PREEMPT` expectation still aborted the build.
- Verification: `nix-instantiate --parse modules/overrides/linux-zen-preempt-fix.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, and `nix build --no-link .#nixosConfigurations.nixy-laptop.config.boot.kernelPackages.kernel.configfile`.

### 2026-03-17
- Date: 2026-03-17
- Change: Integrated `origin/main` into the local branch with local `nixy-*` host naming preserved, then migrated `nixy-desktop` off stale `config/*` imports and Hyprland wiring onto the current `programs/*` / `pictures/*` layout with Niri plus DMS.
- Pitfall/Root cause: The remote branch still carried an older host rename (`weaselos-*`) and Hyprland-era files, while the local laptop-first tree had already moved to Niri/DMS and different path conventions; desktop evaluation also exposed unrelated stale package and option drift (`protonup`, `greetd.tuigreet`, `noto-fonts-emoji`, insecure `stremio`, removed libvirtd OVMF options).
- Verification: `nix-instantiate --parse hosts/nixy-desktop/config.nix`, `nix-instantiate --parse hosts/nixy-desktop/home.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`.

### 2026-03-17 (modularization pass)
- Date: 2026-03-17
- Change: Introduced shared `lib/` host generation, `profiles/system/` and `profiles/home/`, moved Niri base KDL fragments into `programs/niri/`, added mutable DMS bootstrap scaffolding, replaced absolute local flake package paths with repo-relative ones, and rewrote the README around fork/bootstrap/rebuild flow.
- Pitfall/Root cause: Local flake evaluation will not reliably see newly added files until they are `git add`ed, and DMS/Niri portability breaks if the repo manages only `config.kdl` without also shipping the stable `base/*.kdl` fragments while leaving `dms/*.kdl` mutable.
- Verification: `nix-instantiate --parse flake.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix-instantiate --parse profiles/home/base.nix`, `nix-instantiate --parse programs/niri.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`.

### 2026-03-18
- Date: 2026-03-18
- Change: Fixed Home Manager activation for the declarative Niri base layer by making the repo-owned `~/.config/niri/config.kdl` and `base/*.kdl` entries `force = true`, and synchronized the committed base KDL files back to the exact live laptop versions.
- Pitfall/Root cause: Evaluation succeeded, but activation failed because Home Manager refused to clobber pre-existing mutable files in `~/.config/niri/base/`; additionally, the first repo import of some base KDL files had been truncated to partial content instead of mirroring the full live configuration.
- Verification: `systemctl status home-manager-evilweasel.service --no-pager --full`, `journalctl -u home-manager-evilweasel.service -n 200 --no-pager`, `diff -u ~/.config/niri/base/binds.kdl programs/niri/base/binds.kdl`, `nix-instantiate --parse programs/niri.nix`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-19
- Date: 2026-03-19
- Change: Switched VS Code `Code/User/settings.json` to `config.lib.file.mkOutOfStoreSymlink` so the committed repo file stays in the flake while the live file remains writable from the editor UI.
- Pitfall/Root cause: A normal Home Manager `source` link points into the Nix store and is read-only, so UI edits cannot persist; the symlink target must stay inside the writable working tree.
- Verification: `nix-instantiate --parse programs/vscode.nix` and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-19 (README bootstrap note)
- Date: 2026-03-19
- Change: Added the missing bootstrap note to `README.md` telling new users to enable `nix-command` and `flakes` before the first flake rebuild.
- Pitfall/Root cause: Fresh NixOS installs still need the experimental features enabled in their existing config before `nixos-rebuild --flake` works.
- Verification: README content update only.

### 2026-03-19 (michapc rollback)
- Date: 2026-03-19
- Change: Removed the temporary `michapc` host scaffolding and Nvidia module after the host-specific change was no longer wanted.
- Pitfall/Root cause: The new host files were only a temporary integration step and should not remain in the shared repo without the full machine-specific setup.
- Verification: `nix-instantiate --parse lib/hosts.nix`.

### 2026-03-19 (zed-editor)
- Date: 2026-03-19
- Change: Added `pkgsUnstable.zed-editor` to the shared Home Manager base package list so both hosts get Zed from the unstable pin.
- Pitfall/Root cause: The common home base module needed `pkgsUnstable` threaded through before it could reference the unstable package set.
- Verification: `nix-instantiate --parse profiles/home/base.nix` and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-20 (neovim + shell stack rebuild)
- Date: 2026-03-20
- Change: Reworked the custom Neovim stack with `which-key`, Catppuccin theming, LSP wiring for TS/JS/Bun, Rust, Python, C#, Nix, Bash, YAML, TOML, Markdown, Lua, and Dockerfiles; added a repo-owned terminal stack with Bash/Zsh/Nushell, Starship, Atuin, Carapace, Yazi, and Zellij; and added a portable `devShell` plus `nix run .#dev` launcher and quick-start docs.
- Pitfall/Root cause: Home Manager shell init options are shell-specific (`bash.initExtra`, `zsh.initContent`), the shared Starship config had to avoid clashing with DMS' own palette, and the repo needed a `git restore` cleanup after `nix fmt` touched unrelated files.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix flake check`, `nix develop .#dev --command bash -lc 'command -v nvim yazi zellij bash zsh nu starship atuin carapace codex claude-code cargo python3 rustc pnpm dotnet >/dev/null && printf "dev-shell-ok\n"'`, and `nix develop .#dev --command nvim --headless -u NONE -c 'lua package.preload["which-key"] = function() return { add = function() end, setup = function() end } end; package.preload["lspconfig"] = function() return setmetatable({}, { __index = function() return { setup = function() end } end }) end; package.preload["cmp_nvim_lsp"] = function() return { default_capabilities = function() return {} end } end; package.preload["catppuccin"] = function() return { setup = function() end } end; vim.cmd = { colorscheme = function() end }; dofile("programs/nvim/keymaps.lua"); dofile("programs/nvim/plugins/theme.lua"); dofile("programs/nvim/plugins/lsp.lua")' -c q`.

### 2026-03-20 (home-manager reactivation backup)
- Date: 2026-03-20
- Change: Added a centralized `home-manager.backupFileExtension = "hm-backup";` in the system base profile so existing hand-managed `~/.config/atuin`, `~/.config/yazi`, and `~/.config/zellij` files are backed up instead of aborting activation.
- Pitfall/Root cause: Home Manager refuses to clobber pre-existing config files when new declarative modules take ownership, which caused `fr` to fail during reactivation on the laptop.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, and `nix flake check`.

### 2026-03-20 (runtime deprecations cleanup)
- Date: 2026-03-20
- Change: Switched the session keymaps and Alpha dashboard button from deprecated `SessionSave`/`SessionRestore` commands to `AutoSession save`/`AutoSession restore`, migrated the Neovim LSP setup off `require("lspconfig")` to `vim.lsp.config`/`vim.lsp.enable`, and updated Nushell history config to `history.file_format = "sqlite"`.
- Pitfall/Root cause: Neovim 0.11 deprecates the old lspconfig framework API, AutoSession renamed its commands, and Nushell 0.111 rejects the old `history.format` field.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix flake check`, and `nix develop .#dev --command nvim --headless -u NONE -c 'lua package.preload["which-key"] = function() return { add = function() end, setup = function() end } end; package.preload["cmp_nvim_lsp"] = function() return { default_capabilities = function() return {} end } end; package.preload["auto-session"] = function() return { setup = function() end } end; vim.cmd = { colorscheme = function() end }; dofile("programs/nvim/plugins/auto-session.lua"); dofile("programs/nvim/keymaps.lua"); dofile("programs/nvim/plugins/lsp.lua")' -c q`.

### 2026-03-20 (wifi bssid helper)
- Date: 2026-03-20
- Change: Added a reusable `wifi-bssid` shell helper package and exposed it as a shared `bssid` alias in Bash, Zsh, and Nushell.
- Pitfall/Root cause: New files in this git-backed flake must be staged before `nix eval` can see them, and the first evaluation attempt was blocked by the sandboxed Nix daemon/cache path.
- Verification: `nix-instantiate --parse scripts/wifi-bssid.nix`, `nix-instantiate --parse programs/terminal-stack.nix`, `nix-instantiate --parse profiles/home/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, and `nix flake check`.

### 2026-03-20 (bubblewrap availability)
- Date: 2026-03-20
- Change: Added `bubblewrap` to the shared NixOS package set and the flake dev shell so Codex and system installs can find `bwrap` without falling back to the vendored copy.
- Pitfall/Root cause: `codex` was launched from an environment that did not have `bubblewrap` available on `PATH`, and the common desktop system profile did not include it yet.
- Verification: `nix-instantiate --parse flake.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, and `nix flake check` (desktop eval still hit an unrelated Home Manager derivation parse failure).

### 2026-03-20 (shell helpers + niri overlay docs)
- Date: 2026-03-20
- Change: Restored the old shell helpers as real PATH commands, added a startup alias table after `fastfetch` for Bash/Zsh/Nushell, and expanded the native Niri hotkey overlay bindings with clearer titles and an extra `Mod+Shift+/` shortcut.
- Pitfall/Root cause: This chat is voice-transcribed through another model, so unusual spellings in user requests may need context-based interpretation; also, helper additions and Niri bind changes need to stay synchronized with the alias table and overlay titles.
- Verification: Not yet run after this batch of edits.

### 2026-03-20 (local ai app switch)
- Date: 2026-03-20
- Change: Added `lmstudio` as a shared Home Manager package for both hosts and removed the laptop-specific `services.llama-cpp` wiring from the active configuration while keeping the module in the repo.
- Pitfall/Root cause: `llama-cpp` is useful as a service module but expensive to rebuild and unnecessary for the current desktop workflow; the desktop app path is a better fit for quick local-model iteration.
- Verification: Not yet run after this batch of edits.

### 2026-03-20 (xorg deprecation cleanup)
- Date: 2026-03-20
- Change: Added `libxcb` as an explicit argument in the vendored Asahi Mesa package so the evaluation no longer needs to resolve `xorg.libxcb`.
- Pitfall/Root cause: The deprecation warning came from the vendored Mesa file using `with xorg;` and implicitly reaching `xorg.libxcb`, which Nixpkgs now warns about.
- Verification: Not yet run after this batch of edits.

### 2026-03-20 (brave removal)
- Date: 2026-03-20
- Change: Removed `brave` from the shared system package list so the active laptop and desktop configs stop pulling in the package that triggered the `xorg.libxcb` warning.
- Pitfall/Root cause: The warning was coming from an unused but still-installed browser package, not from the Asahi support tree.
- Verification: Not yet run after this batch of edits.

### 2026-03-20 (kitty long-run notifications)
- Date: 2026-03-20
- Change: Enabled Kitty shell integration explicitly and added `notify_on_cmd_finish = "invisible 5.0 notify"` in the shared Home Manager Kitty config so long-running commands in unfocused or invisible Kitty windows emit desktop notifications.
- Pitfall/Root cause: Kitty's finish notifications depend on shell integration, and the config needs to live in the shared home profile to cover both hosts consistently.
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`; the desktop eval also hit an existing `weasel-rebuild` derivation parse error (`expected string 'D'`).

### 2026-03-20 (backburner ideas log)
- Date: 2026-03-20
- Change: Added `docs/backburner-ideas.md` as a concise holding area for deferred project ideas, starting with the Kitty/background-command notification idea and its relevant references.
- Pitfall/Root cause: This is intentionally a docs-only addition, so no Nix evaluation was needed for the new note file itself.
- Verification: `git diff --check`

### 2026-03-20 (michapc hosts + session debug tooling)
- Date: 2026-03-20
- Change: Added `michapc` and `michapc-debug` hosts, a conservative Micha-specific Nvidia module, configurable Niri DMS startup, shared session debug collection scripts, and repo-tracked Micha VT debug instructions.
- Pitfall/Root cause: `nixy-desktop` initially kept failing on `fu.drv` because the referenced store derivation was corrupted to a 1-byte file; changing `scripts/weasel-rebuild.nix` to emit a new derivation hash cleared the stale store path and restored evaluation.
- Verification: `nix-instantiate --parse lib/hosts.nix`, `nix-instantiate --parse profiles/home/base.nix`, `nix-instantiate --parse programs/niri.nix`, `nix-instantiate --parse modules/michapc-nvidia.nix`, `nix-instantiate --parse hosts/michapc/config.nix`, `nix-instantiate --parse hosts/michapc/home.nix`, `nix-instantiate --parse hosts/michapc/hardware.nix`, `nix-instantiate --parse hosts/michapc/users.nix`, `nix-instantiate --parse hosts/michapc/variables.nix`, `nix-instantiate --parse hosts/michapc-debug/config.nix`, `nix-instantiate --parse hosts/michapc-debug/home.nix`, `nix-instantiate --parse hosts/michapc-debug/variables.nix`, `nix-instantiate --parse scripts/weasel-dms-session.nix`, `nix-instantiate --parse scripts/weasel-collect-session-debug.nix`, `nix-instantiate --parse scripts/weasel-rebuild.nix`, `git diff --check`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix build .#nixosConfigurations.michapc.config.system.build.toplevel --no-link`, and `nix build .#nixosConfigurations.michapc-debug.config.system.build.toplevel --no-link`

### 2026-03-20 (documented Micha DMS follow-up plan)
- Date: 2026-03-20
- Change: Added a detailed follow-up plan to `docs/backburner-ideas.md` describing when to escalate from `michapc-debug` to targeted DMS/Quickshell diagnostics, which files to touch, and which evidence to capture.
- Pitfall/Root cause: The next debugging step only makes sense if `michapc-debug` works and `michapc` fails; documenting that trigger avoids another generic compatibility pass in a future chat.
- Verification: `git diff --check`

### 2026-03-20 (niri hotkey overlay bind regression)
- Date: 2026-03-20
- Change: Removed the experimental `Mod+Shift+/` hotkey overlay bind from `programs/niri/base/binds.kdl` after it broke the shared Niri config on both `nixy-laptop` and Micha-related hosts.
- Pitfall/Root cause: Niri's current key syntax did not accept the literal `/` token in that bind, so `base/binds.kdl` failed to parse and the whole config loaded in broken mode with missing hotkeys and no DMS startup.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix build .#nixosConfigurations.nixy-laptop.config.system.build.toplevel --no-link`, `nix build .#nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.activationPackage --no-link`, `niri validate -c /tmp/niri-validate/config.kdl`, and `journalctl --user -b --no-pager | rg -i "niri|error loading config|error parsing KDL"`

### 2026-03-21 (DMS/Quickshell hang from qt5ct platform theme)
- Date: 2026-03-21
- Change: Forced `QT_QPA_PLATFORMTHEME` to an empty string in the shared Home Manager session environment and defensively unset it in `scripts/weasel-dms-session.nix` before launching DMS.
- Pitfall/Root cause: Home Manager's Qt setup exported `QT_QPA_PLATFORMTHEME=qt5ct`; Quickshell then spun forever before loading `shell.qml`, repeatedly `statx`-ing `~/.config/qt6ct/qt6ct.conf`, which made both the local laptop session and Micha's DMS session look dead while Niri itself still worked.
- Verification: `strace -f -tt -s 160 -o /tmp/quickshell.strace quickshell -v -p $(dirname $(dirname $(readlink -f $(which dms))))/share/quickshell/dms/shell.qml`, `env -u QT_QPA_PLATFORMTHEME quickshell -v -p $(dirname $(dirname $(readlink -f $(which dms))))/share/quickshell/dms/shell.qml`, `nix-instantiate --parse profiles/home/base.nix`, `nix-instantiate --parse scripts/weasel-dms-session.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, `nix build --no-link .#nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.activationPackage`, `systemctl --user set-environment QT_QPA_PLATFORMTHEME=`, and `env QT_QPA_PLATFORMTHEME= dms run -d`

### 2026-03-21 (shared laptop DisplayLink module)
- Date: 2026-03-21
- Change: Added a reusable `modules/displaylink.nix` module, imported it from the shared system base, and enabled it in `profiles/system/laptop.nix` so `nixy-laptop`, `michapc`, and `michapc-debug` all get the same DisplayLink and `evdi` wiring.
- Pitfall/Root cause: New module files must be added to Git before flake evaluation sees them consistently, and the DisplayLink driver list should be merged with existing host GPU drivers instead of replacing them outright.
- Verification: `nix-instantiate --parse modules/displaylink.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix-instantiate --parse profiles/system/laptop.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-03-21 (DisplayLink EULA build fix)
- Date: 2026-03-21
- Change: Split the shared `modules/displaylink.nix` module into an always-on `evdi` kernel path plus an opt-in `features.displaylink.proprietaryUserspace.enable` path for the Synaptics DisplayLink userspace pieces.
- Pitfall/Root cause: Enabling `pkgs.displaylink`, `services.xserver.videoDrivers = [ "displaylink" ]`, and `dlm` unconditionally pulls `displaylink-620.zip`, which aborts the build until the proprietary Synaptics archive has been prefetched locally to satisfy the EULA.
- Verification: `nix-instantiate --parse modules/displaylink.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix build --dry-run --no-link .#nixosConfigurations.nixy-laptop.config.system.build.toplevel`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-03-21 (automatic DisplayLink source override)
- Date: 2026-03-21
- Change: Reworked `modules/displaylink.nix` to use the official NixOS DisplayLink module path (`services.xserver.videoDrivers = [ "displaylink" ]`) for all laptop hosts, but overrode `pkgs.displaylink.src` with a fixed-output `fetchurl` to the Synaptics 6.2 archive so the proprietary driver builds without a manual `requireFile` prefetch step.
- Pitfall/Root cause: The earlier half-measure only enabled `evdi`, which did not activate the full DisplayLink userspace stack; the real fix is to keep the upstream module behavior and replace only the source acquisition step that was failing on the EULA gate.

### 2026-04-22 (Helium URL handling)
- Date: 2026-04-22
- Change: Fixed the Helium desktop entry in `packages/helium/flake.nix` to pass `%U` through `Exec` and advertise the browser MIME types so terminal link clicks open the target URL instead of a blank window.
- Pitfall/Root cause: The desktop file was launching the wrapper without any URL placeholder, so `xdg-open` could start Helium but had nothing to hand it when a link was clicked.
- Verification: `nix-instantiate --parse packages/helium/flake.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`.

### 2026-04-20
- Date: 2026-04-20
- Change: Wrapped `alarm-clock-applet` so `GST_PLUGIN_SYSTEM_PATH_1_0` also includes `gst-plugins-good`, which provides `autoaudiosink` for the alarm preview/playback path.
- Pitfall/Root cause: The package wrapper already set `GST_PLUGIN_SYSTEM_PATH_1_0`, but only to `gstreamer` plus `gst-plugins-base`; `autoaudiosink` lives in `gst-plugins-good`, so the app failed with `No such element or plugin 'autoaudiosink'`.
- Verification: `GST_PLUGIN_SYSTEM_PATH_1_0=$(nix eval --raw .#nixosConfigurations.nixy-desktop.pkgs.gst_all_1.gst-plugins-base.outPath)/lib/gstreamer-1.0 $(nix eval --raw .#nixosConfigurations.nixy-desktop.pkgs.gst_all_1.gstreamer.outPath)/bin/gst-inspect-1.0 autoaudiosink`, `nix-instantiate --parse profiles/system/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-04-20 (formatter comparison fixture)
- Date: 2026-04-20
- Change: Added a temporary `fmt-eval.nix` fixture and formatted it with `nixfmt-rfc-style`, `alejandra`, and `nixpkgs-fmt` to compare layout, line breaking, and attrset/lambda style on the same input.
- Pitfall/Root cause: `alejandra` and `nixpkgs-fmt` rewrite in place, while `nixfmt-rfc-style` also needed a real file input; using throwaway working copies avoids accidentally destroying the baseline fixture.
- Verification: `nix run nixpkgs#nixfmt-rfc-style -- fmt-eval.nixfmt-rfc-style.work.nix`, `nix run nixpkgs#alejandra -- fmt-eval.alejandra.work.nix`, and `nix run nixpkgs#nixpkgs-fmt -- fmt-eval.nixpkgs-fmt.work.nix`.
- Verification: `nix-prefetch-url --name displaylink-620.zip https://www.synaptics.com/sites/default/files/exe_files/2025-09/DisplayLink%20USB%20Graphics%20Software%20for%20Ubuntu6.2-EXE.zip`, `nix-instantiate --parse modules/displaylink.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix build --dry-run --no-link .#nixosConfigurations.nixy-laptop.config.system.build.toplevel`, `nix build --no-link -L .#nixosConfigurations.nixy-laptop.config.system.build.toplevel`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-03-21 (audio-aware session debug bundle)
- Date: 2026-03-21
- Change: Extended `weasel-collect-session-debug` to capture PipeWire/WirePlumber service state, audio journals, `wpctl`, `pactl`, ALSA device listings, and `/dev/snd` nodes in addition to the existing session logs.
- Pitfall/Root cause: The original debug bundle was strong for greetd/Niri/DMS issues but too generic for intermittent audio failures; without targeted PipeWire/WirePlumber snapshots, it was hard to distinguish a dead audio daemon from a wrong default sink or missing ALSA devices.
- Verification: `nix-instantiate --parse scripts/weasel-collect-session-debug.nix`, `nix build --no-link .#nixosConfigurations.michapc.config.home-manager.users.micha.home.activationPackage`, and `nix build --no-link .#nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.activationPackage`

### 2026-03-21 (general incident bundle expansion)
- Date: 2026-03-21
- Change: Expanded `weasel-collect-session-debug` into a broader incident collector with system and user journals, failed-unit snapshots, DRM and USB topology, DisplayLink and `evdi` state, `niri`/`xrandr` output info, network basics, and extra user-state directories for Niri, DMS, PipeWire, WirePlumber, and Pulse.
- Pitfall/Root cause: A support bundle aimed only at one known failure mode stops being useful as soon as a second subsystem starts failing; for distro-style issue reports, the collector has to capture enough cross-cutting state to separate compositor, kernel, device, service, and application-level regressions.

### 2026-03-23 (nixy-laptop NVIDIA branch)
- Date: 2026-03-23
- Change: Forced `nixy-laptop` onto `hardware.nvidia.package = nvidiaPackages.production` so it uses the stable NVIDIA branch instead of the shared `latest` default.
- Pitfall/Root cause: The repo-level NVIDIA module still defaults to `latest`, so host-specific branch selection must be overridden in the laptop host config; the resolved package version stayed at `580.119.02` because the pinned nixpkgs revision currently exposes the same build under both aliases.
- Verification: `nix-instantiate --parse hosts/nixy-laptop/config.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath --raw`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.hardware.nvidia.package.version --raw`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.hardware.nvidia.package.name --raw`.

### 2026-03-22 (current plan capture)
- Date: 2026-03-22
- Change: Created `current-plan.md` as the working plan for the Michael laptop issues, including global defaults for bindings/DisplayLink/audio, a structured debug-collection cleanup, and Hyprland cleanup notes.
- Pitfall/Root cause: The plan needed to reflect the user's addenda before implementation started, otherwise the work would drift toward a Michael-only fix instead of the requested shared-default approach.
- Verification: `sed -n '1,240p' current-plan.md`
- Verification: `nix-instantiate --parse scripts/weasel-collect-session-debug.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, `nix build --no-link .#nixosConfigurations.michapc.config.home-manager.users.micha.home.activationPackage`, and `nix build --no-link .#nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.activationPackage`

### 2026-03-22 (German language convention)
- Date: 2026-03-22
- Change: Added an explicit repository rule in `AGENTS.md` that German communication must use real umlauts and `ß` instead of transliterations like `ue`, `oe`, `ae`, or `ss`, except where ASCII-only interfaces require a fallback.
- Pitfall/Root cause: Without an explicit rule, German writeups can silently drift into ASCII transliterations, which makes the repo's communication style inconsistent.
- Verification: `sed -n '1,220p' AGENTS.md`

### 2026-03-22 (German transliteration cleanup)
- Date: 2026-03-22
- Change: Rewrote the German text in `current-plan.md` to use proper umlauts and `ß` instead of ASCII transliterations.
- Pitfall/Root cause: The plan file had the usual `ue`/`oe`/`ae` style substitutions throughout, which is easy to miss if only the code files are checked.
- Verification: `rg -n '\\b(fuer|ueber|noetig|laeuft|Geraete|zusaetzlich|pruefen|oeffnet|Loesungen|grosse|aufraeumen)\\b' current-plan.md README.md docs/*.md AGENTS.md agent-learnings.md`

### 2026-03-22 (repo-owned MIME and DMS defaults)
- Date: 2026-03-22
- Change: Added a repo-owned `mimeapps.list`, moved the stable Niri/DMS default fragments into `programs/niri/dms/`, and linked them into `~/.config` with out-of-store symlinks while leaving DMS profile state mutable.
- Pitfall/Root cause: The useful split is between stable defaults and mutable profile state; repo-owning the wrong DMS layer would fight the settings UI or runtime-generated profiles.
- Verification: `nix-instantiate --parse programs/niri.nix`, `nix-instantiate --parse profiles/home/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, and `nix flake check`

### 2026-03-21 (keyboard inventory in debug bundle)
- Date: 2026-03-21
- Change: Added input-device inventory to `weasel-collect-session-debug`, including `/proc/bus/input/devices`, `/dev/input/by-*`, filtered udev input metadata, and per-keyboard sysfs/uevent dumps to identify problematic internal laptop keyboards.
- Pitfall/Root cause: To disable a broken built-in keyboard reliably, you need a stable identifier such as the kernel device path, input handler, or udev properties; generic logs alone do not tell you which exact keyboard node to target.
- Verification: `nix-instantiate --parse scripts/weasel-collect-session-debug.nix`, `nix build --no-link .#nixosConfigurations.michapc.config.home-manager.users.micha.home.activationPackage`, and `nix build --no-link .#nixosConfigurations.nixy-laptop.config.home-manager.users.evilweasel.home.activationPackage`

### 2026-03-21 (nixy-laptop Nvidia + DisplayLink merge fix)
- Date: 2026-03-21
- Change: Restored an explicit `nvidia` entry for `nixy-laptop` in `profiles/system/laptop.nix` while keeping the shared DisplayLink module active, so the laptop's effective `services.xserver.videoDrivers` list becomes `["displaylink" "nvidia"]`.
- Pitfall/Root cause: The `nixos-hardware` Yoga hybrid profile sets `services.xserver.videoDrivers = mkDefault [ "nvidia" ]`; a normal-priority shared DisplayLink entry overrode that default instead of extending it, which dropped the Nvidia driver closure from the laptop generation even though `hardware.nvidia.open = true` and PRIME offload were still configured.
- Verification: `nix eval --json --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.services.xserver.videoDrivers`, `nix eval --json --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.hardware.nvidia.open`, `nix build --dry-run --no-link .#nixosConfigurations.nixy-laptop.config.system.build.toplevel`, and `nix build --no-link -L .#nixosConfigurations.nixy-laptop.config.system.build.toplevel`

### 2026-03-22 (fastfetch logo backend switch)
- Date: 2026-03-22
- Change: Switched Fastfetch from `kitty-direct` to `chafa` in `programs/fastfetch/default.nix` so the logo renders as terminal text instead of Kitty graphics in scrollback.
- Pitfall/Root cause: `fastfetch` is started from shell init in bash, zsh, and nushell; with a Kitty graphics logo, the image remains visible in scrollback and reappears when scrolling back through terminal history.
- Verification: `nix-instantiate --parse programs/fastfetch/default.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-03-22 (shell helper alias cleanup)
- Date: 2026-03-22
- Change: Updated `weasel-shell-helpers` so `v` invokes the configured `nvim` from `PATH` and `eza` wrappers pass `--color=auto` explicitly.
- Pitfall/Root cause: The `v` helper had been bypassing the Home Manager-managed Neovim wrapper by calling the package binary directly, and the `eza` wrappers were inheriting a broken color setting from elsewhere.
- Verification: `nix-instantiate --parse scripts/weasel-shell-helpers.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-03-22 (Stylix removal and DMS/Matugen migration)
- Date: 2026-03-22
- Change: Removed Stylix from the flake graph, switched shared desktop theming to DMS + Matugen, and added repo-owned Matugen templates plus generated target files for Waybar, Rofi, SwayNC, and Wlogout.
- Pitfall/Root cause: The DMS/Matugen config needed an explicit `[config]` table even for a dry-run, and Rofi already owns `~/.config/rofi/config.rasi` through the Home Manager module, so the final wiring had to split config ownership from the theme file itself.
- Verification: `nix-instantiate --parse flake.nix`, `nix-instantiate --parse lib/mk-host.nix`, `nix-instantiate --parse profiles/home/base.nix`, `nix-instantiate --parse profiles/home/desktop.nix`, `nix-instantiate --parse profiles/home/laptop.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix-instantiate --parse profiles/system/laptop.nix`, `nix-instantiate --parse programs/fastfetch/default.nix`, `nix-instantiate --parse programs/niri.nix`, `nix-instantiate --parse programs/rofi/rofi.nix`, `nix-instantiate --parse programs/swaync.nix`, `nix-instantiate --parse programs/waybar.nix`, `nix-instantiate --parse programs/wlogout.nix`, `nix-instantiate --parse scripts/weasel-dms-session.nix`, `nix-instantiate --parse scripts/weasel-shell-helpers.nix`, `nix-instantiate --parse programs/matugen.nix`, `matugen image "$HOME/weasel-os/pictures/wallpapers/beautifulmountainscape.jpg" --config "$HOME/weasel-os/programs/matugen/config.toml" --dry-run --type scheme-vibrant --mode dark`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix flake check`

### 2026-03-22 (theme cleanup follow-up)
- Date: 2026-03-22
- Change: Added Monaspace + Nerd Monaspace fonts, hooked Kitty into Matugen, added GTK `gtk.css` imports and a dark Adwaita GTK anchor, switched Qt off the missing Kvantum path to Adwaita-Dark, and corrected the DMS launcher icon theme default to Papirus-Dark.
- Pitfall/Root cause: Several theme outputs existed but were not actually consumed (`kitty.conf` did not include the theme files, GTK only had `dank-colors.css` without `gtk.css`, Qt was forced onto Kvantum without a theme, and DMS still defaulted launcher icons to `System Default`).
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix-instantiate --parse programs/matugen.nix`, `nix-instantiate --parse programs/matugen/config.toml`, `nix-instantiate --parse scripts/weasel-shell-helpers.nix`, `nix-instantiate --parse scripts/weasel-dms-session.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix flake check`

### 2026-03-22 (DMS theme worker recovery)
- Date: 2026-03-22
- Change: Fixed the DMS theme worker crash path by disabling the Kitty Matugen template collision, making the Qt config files mutable again via Home Manager activation, adding app-id substitutions for Firefox/Thunar/virt-manager/Handy, pinning Kitty to the Monaspice mono font, and replacing the broken BatteryOverride `StyledText` usage with plain `Text`.
- Pitfall/Root cause: The merged Matugen TOML contained a duplicate `dmskittytabs` key, `Apply Qt Colors` was writing to read-only symlink targets, the launcher had too few icon substitutions, and the BatteryOverride QML file referenced a type that was not resolving in the current DMS runtime.
- Verification: `jq empty programs/dank-material-shell/settings.json`, `nix-instantiate --parse programs/matugen.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix flake check`

### 2026-03-22 (DMS icon theme live-profile trap)
- Date: 2026-03-22
- Change: Fixed the missing app/workspace/tray icons by installing `papirus-icon-theme` into the active user profile and restarting DMS after the repo config already pointed at `Papirus-Dark`.
- Pitfall/Root cause: The repo config was correct, but the active user profile still lacked `Papirus-Dark`, so DMS fell back to text letters for many icons. A local `nix profile add` repaired the live session only; the durable fix is the `home.packages` entry in `profiles/home/base.nix`.
- Verification: `readlink -f /home/evilweasel/.nix-profile/share/icons/Papirus-Dark`, `pgrep -af 'quickshell|/bin/dms'`, `dms restart`

### 2026-03-22 (Hyprland cleanup and DMS lock handoff)
- Date: 2026-03-22
- Change: Removed the remaining Hyprland/Waybar/Wlogout leftovers, switched the Niri lock bind to the DMS lock IPC call, and pruned the related Matugen outputs, helper scripts, and cached assets.
- Pitfall/Root cause: The old Hypr stack was mostly dead code but still pulled in `hypridle`, `hyprlock`, `hyprpicker`, Waybar/Wlogout assets, and a Hyprland cache entry. DMS already owns the lock and power actions, so keeping those extras only added stale dependencies and confusion.
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix-instantiate --parse programs/matugen.nix`, `nix-instantiate --parse programs/matugen/config.toml`, `nix-instantiate --parse programs/niri/base/binds.kdl`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-03-22 (Helium and T3 Code refresh)
- Date: 2026-03-22
- Change: Updated `packages/helium/flake.nix` to Helium `0.10.6.1` and `packages/t3code/flake.nix` to T3 Code `0.0.13`, including the new upstream AppImage hashes.
- Pitfall/Root cause: Both package flakes were still pinned to old releases. The correct asset hashes had to be fetched from the current GitHub release assets and converted to SRI format before updating the fetch attributes.
- Verification: `nix-instantiate --parse packages/helium/flake.nix`, `nix-instantiate --parse packages/t3code/flake.nix`, `nix eval --no-write-lock-file ./packages/helium#packages.x86_64-linux.default.drvPath`, `nix eval --no-write-lock-file ./packages/t3code#packages.x86_64-linux.default.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-03-22 (cursor and icon theme inventory)
- Date: 2026-03-22
- Change: Added multiple cursor themes and additional icon themes to the shared Home Manager package set so DMS can enumerate them on both hosts.
- Pitfall/Root cause: DMS only offers themes that exist in the user-visible XDG data path, so cursor and icon themes need to be exposed under `~/.local/share/icons`; merely adding packages to the profile is not enough. Multiple icon-theme packages also cannot all live in `home.packages` because Home Manager merges their `share/icons` trees and collides on duplicate upstream files like `breeze/actions/12/object-fill.svg`. The greeter also needs explicit `configFiles` for `settings.json` and `session.json` or it will forget the wallpaper/session state.
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix flake check`

### 2026-03-22 (theme activation performance)
- Date: 2026-03-22
- Change: Replaced recursive XDG icon-theme materialization with simple activation-time symlinks into the package store paths.
- Pitfall/Root cause: `xdg.dataFile` with `recursive = true` on large icon trees forces Home Manager to traverse and link thousands of files during every activation. That made `nh os switch` look hung even though it was just busy linking theme assets.
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix flake check`

### 2026-03-22 (kitty matugen ownership split)
- Change: Removed the repo-owned Matugen Kitty template files and their Home Manager links so DMS owns the Kitty theme outputs exclusively.
- Pitfall/Root cause: DMS already exposes Kitty as a Matugen target; keeping repo-owned `kitty-theme` and `kitty-tabs` templates duplicated the `dmskittytabs` TOML table and crashed the theme worker during every theme refresh.
- Verification: `matugen image /home/evilweasel/Pictures/wallpapers/zaney-wallpaper.jpg --config /home/evilweasel/.config/matugen/config.toml --dry-run --type scheme-vibrant --mode dark`, `nix-instantiate --parse programs/matugen/config.toml`, `nix-instantiate --parse programs/matugen.nix`

### 2026-03-22 (kitty transparency)
- Change: Added `background_opacity 0.85` to the repo-owned Kitty config so terminal transparency is handled locally in Kitty instead of through Matugen/DMS templates.
- Pitfall/Root cause: Kitty opacity is an independent terminal setting; it does not belong in the Matugen theme pipeline and should not reintroduce template ownership conflicts.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-03-24 (Zed Codex ACP on NixOS)
- Date: 2026-03-24
- Change: Enabled `programs.nix-ld` in the shared system base and added `libcap`, `xz`, `openssl`, and `zlib` so Zed's managed `codex-acp` binary can start on both hosts.
- Pitfall/Root cause: Zed launches the registry agent as a non-Nix Linux binary from `~/.local/share/zed/external_agents/codex/...`, which fails on NixOS without `nix-ld` or an equivalent wrapper.
- Verification: `nix-instantiate --parse profiles/system/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-03-27 (eza color mode fix)
- Date: 2026-03-27
- Change: Updated the dev-shell `eza` aliases in `flake.nix` to pass `--color=auto` for `ls`, `ll`, and `la`.
- Pitfall/Root cause: `eza` does not accept `tty` as a color mode, so a stale alias or inherited config path can trigger the `Option --color has no "tty" setting` error unless the aliases force a valid mode.
- Verification: `nix-instantiate --parse flake.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-03-27 (home shell alias override)
- Date: 2026-03-27
- Change: Added explicit `ls`/`ll`/`la` aliases to `programs/terminal-stack.nix` for Bash, Zsh, and Nushell so the user shell no longer inherits the system `ls --color=tty` alias.
- Pitfall/Root cause: The live shell was still taking `ls` from the existing profile alias chain, and `ll` was just `ls -l`, so fixing only the dev-shell aliases did not touch the actual login shell behavior.
- Verification: `nix-instantiate --parse programs/terminal-stack.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-03-29 (stremio-linux-shell)
- Date: 2026-03-29
- Change: Added `pkgsUnstable.stremio-linux-shell` to the shared Home Manager package list in `profiles/home/base.nix` so both hosts pick up Stremio from the unstable unfree pin.
- Pitfall/Root cause: The package belongs in the shared home profile, not a host-only wrapper, because both desktop and laptop import that module and `pkgsUnstable` is already threaded through there.
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-03-30 (screenpipe desktop app packaging)
- Date: 2026-03-30
- Change: Added a source-built `screenpipe-app` flake package plus `nixy-laptop` Home Manager wiring, vendored frontend dependencies through `importNpmLock`, pinned a local `cargo-tauri 2.10.0`, patched upstream Tauri config for Nix builds, removed target-specific Cargo git dependencies that break Linux vendoring, and wrapped the installed app with runtime paths for `bun`, `ffmpeg`, `tesseract`, and `xdotool`.
- Pitfall/Root cause: Upstream assumes networked Bun/Tauri builds and non-Nix Linux paths; the Linux source build needed explicit `onnxruntime` discovery to stop `ort-sys` downloads, `_POSIX_C_SOURCE`/`_GNU_SOURCE` for `antirez-asr-sys`, `libgbm` in the linker closure, and install-time detection of the actual Tauri binary name instead of assuming `bin/screenpipe`.
- Verification: `nix build .#screenpipe-app --no-link`, `nix path-info --no-eval-cache .#screenpipe-app`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-03-31 (screenpipe linux runtime wrapper)
- Date: 2026-03-31
- Change: Extended the `screenpipe-app` wrapper to export a Linux runtime library path and added `desktop-file-utils` to the wrapped `PATH` so the packaged app can load OpenBLAS/AppIndicator at runtime and complete the deep-link desktop registration step on startup.
- Pitfall/Root cause: `openblas` was only exposed through a temporary build-time symlink, so the final ELF had no persistent OpenBLAS runtime path; after fixing that, Linux startup still failed because `tauri_plugin_deep_link` shells out to `update-desktop-database`, which is not present on a bare NixOS PATH unless `desktop-file-utils` is added explicitly.
- Verification: `nix build .#screenpipe-app --no-link`, `timeout 5s $(nix path-info --no-eval-cache .#screenpipe-app)/bin/screenpipe --help`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-03-30 (nixy-laptop internal DNS host overrides)
- Date: 2026-03-30
- Change: Added `modules/networking/internal-dns.nix` with the Tailscale-backed `networking.hosts` overrides for the internal `*.apps.blain.de` services and imported it only from `hosts/nixy-laptop/config.nix`.
- Pitfall/Root cause: The internal service aliases must stay host-scoped so the Tailscale IP mapping only affects `nixy-laptop` instead of every machine that imports the shared system base.
- Verification: `nix-instantiate --parse hosts/nixy-laptop/config.nix`, `nix-instantiate --parse modules/networking/internal-dns.nix`

### 2026-03-31 (screenpipe bun and niri capture follow-up)
- Date: 2026-03-31
- Change: Updated `packages/screenpipe/default.nix` to bundle `bun` next to the installed Tauri executable and to time out the initial `event_driven_capture` startup screenshot after 15 seconds, then enabled explicit `xdg.portal` routing for `nixy-laptop` in `profiles/system/laptop.nix` so `niri` prefers `wlr` for `ScreenCast` and `Screenshot`.
- Pitfall/Root cause: Screenpipe's Pi bootstrap only checks for `bun` beside `current_exe()` or in a few FHS paths, so a wrapper `PATH` entry is not enough on NixOS; separately, the Linux vision pipeline can stay stuck in `frame_status = not_started` when the first Wayland screenshot hangs, and the default `niri` portal preference was still sending screenshots to the failing GNOME backend.
- Verification: `nix build .#screenpipe-app --no-link`, `nix path-info --no-eval-cache .#screenpipe-app`, `timeout 8s $(nix path-info --no-eval-cache .#screenpipe-app)/bin/screenpipe --help`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-03-31 (ew-cloud bootstrap secrets prep)
- Date: 2026-03-31
- Change: Added the `ew-cloud` bootstrap age recipient to `.sops.yaml`, set `hosts/ew-cloud/variables.nix` to the confirmed Hostinger disk by-id path, and created `secrets/hosts/ew-cloud/secrets.yaml` encrypted with both the admin and host bootstrap recipients.
- Pitfall/Root cause: `sops-nix` needs a decryptable secret at first boot, so a per-host bootstrap age key has to exist before installation; using the raw `/dev/sda` path would also be needlessly brittle when Hostinger already exposes a stable `/dev/disk/by-id` symlink.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.diskoScript.drvPath`

### 2026-03-31 (local sops tooling in shared home profile)
- Date: 2026-03-31
- Change: Added `pkgs.sops` and `pkgs.age` to `profiles/home/common.nix` so the local operator environment has permanent secret-management tooling without relying on `nix shell`.
- Pitfall/Root cause: The repo now expects regular local editing of SOPS-managed files, and using ad-hoc shells for every secrets change adds friction and makes first-time setup easier to get wrong.
- Verification: `nix-instantiate --parse profiles/home/common.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`

### 2026-03-31 (ew-cloud tailscale auth key recorded)
- Date: 2026-03-31
- Change: Updated `secrets/hosts/ew-cloud/secrets.yaml` with the real Tailscale auth key through SOPS while keeping the OpenClaw token encrypted in the same host secret file.
- Pitfall/Root cause: The Tailscale auth key must never appear in plaintext in the repo or shell history, so the correct path is editing the already-encrypted SOPS file in place rather than patching it through normal text tooling.
- Verification: `sed -n '1,80p' secrets/hosts/ew-cloud/secrets.yaml`

### 2026-03-31 (openclaw gateway auth scaffold rollback)
- Date: 2026-03-31
- Change: Removed the speculative `gateway.auth.tokenFile` wiring from `modules/nixos/roles/openclaw.nix` and kept the role at a minimal install-only scaffold with `gateway.mode = "local"`.
- Pitfall/Root cause: The upstream `nix-openclaw` Home Manager module does not expose `gateway.auth.tokenFile`; forcing an assumed option shape breaks full-system evaluation and therefore blocks installation.
- Verification: `nix-instantiate --parse modules/nixos/roles/openclaw.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.diskoScript.drvPath`

### 2026-03-31 (openclaw plugin package collision)
- Date: 2026-03-31
- Change: Set `programs.openclaw.exposePluginPackages = false` in `modules/nixos/roles/openclaw.nix` so the OpenClaw HM profile does not add standalone plugin CLIs on top of the already bundled `openclaw` package payload.
- Pitfall/Root cause: The batteries-included `openclaw` package already ships some tool binaries such as `goplaces`; exposing plugin packages again through `home.packages` creates `pkgs.buildEnv` path collisions and blocks system builds.
- Verification: `nix-instantiate --parse modules/nixos/roles/openclaw.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.diskoScript.drvPath`

### 2026-03-31 (sops-nix nested yaml key selector)
- Date: 2026-03-31
- Change: Updated `hosts/ew-cloud/config.nix` to address nested YAML secrets with slash-separated selectors (`tailscale/auth_key`, `openclaw/gateway_token`) instead of dotted selectors.
- Pitfall/Root cause: `sops-nix` does not resolve nested YAML keys via dot notation in `sops.secrets.<name>.key`; using dotted keys lets installation finish but breaks `setupSecrets` during activation, which can leave first-boot services like Tailscale unconfigured.
- Verification: `nix-instantiate --parse hosts/ew-cloud/config.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`

### 2026-03-31 (temporary public ssh fallback for ew-cloud)
- Date: 2026-03-31
- Change: Temporarily opened TCP/22 on `ew-cloud` and allowed pubkey SSH for both `root` and `evilweasel` so the VPS can be debugged over normal SSH if Tailscale fails again.
- Pitfall/Root cause: Hostinger’s SSH terminal uses the public SSH path, so once the server baseline closes public SSH before Tailscale is online there is no remote recovery path without rescue mode or a temporary fallback.
- Verification: `nix-instantiate --parse hosts/ew-cloud/config.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`

### 2026-04-01 (ew-cloud boot mode mismatch)
- Date: 2026-04-01
- Change: Moved bootloader choice out of the shared server baseline and switched `ew-cloud` to BIOS-safe GRUB with a dedicated `bios_grub` partition, separate `/boot`, and a public SSH pubkey fallback. Also forced `ew-cloud` back to scripted DHCP instead of `systemd-networkd`.
- Pitfall/Root cause: The Hostinger VPS currently boots Ubuntu in BIOS mode on GPT (`/sys/firmware/efi` absent, `bios_grub` partition present, `grub-pc` installed). Installing EFI-only `systemd-boot` there likely left the machine unbootable, which explains why neither public SSH nor Tailscale ever came up after the first reboot.
- Verification: `ssh ew-cloud 'test -d /sys/firmware/efi && echo efi-present || echo efi-absent; sudo parted -s /dev/sda print; dpkg -l \"grub*\" | sed -n \"1,40p\"'`, `nix-instantiate --parse profiles/system/server.nix`, `nix-instantiate --parse hosts/ew-cloud/config.nix`, `nix-instantiate --parse hosts/ew-cloud/disko.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.diskoScript.drvPath`

### 2026-04-01 (ew-cloud public network is static, not DHCP)
- Date: 2026-04-01
- Change: Replaced the temporary DHCP fallback on `ew-cloud` with an explicit static `systemd-networkd` uplink definition matched by MAC address and populated from the live Ubuntu cloud-init config.
- Pitfall/Root cause: The Hostinger Ubuntu image configures the public NIC through cloud-init/netplan with static IPv4/IPv6 addresses, routes, and DNS. Assuming DHCP on the NixOS side risks bringing up neither public SSH nor Tailscale even if the system itself boots correctly.
- Verification: `ssh ew-cloud 'networkctl status eth0 --no-pager; sudo cat /etc/netplan/50-cloud-init.yaml'`, `nix-instantiate --parse hosts/ew-cloud/config.nix`, `nix-instantiate --parse hosts/ew-cloud/variables.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`

### 2026-04-01 (ew-cloud BIOS GRUB only)
- Date: 2026-04-01
- Change: Removed EFI support from `ew-cloud` and kept only BIOS GRUB with a `bios_grub` partition plus separate `/boot`.
- Pitfall/Root cause: The VPS boots Ubuntu in BIOS mode, so installing EFI GRUB on a non-ESP `/boot` partition fails during deployment even though the BIOS bootloader itself installs successfully.
- Verification: `ssh ew-cloud 'test -d /sys/firmware/efi && echo efi-present || echo efi-absent; sudo parted -s /dev/sda print'`, `nix-instantiate --parse hosts/ew-cloud/config.nix`, `nix-instantiate --parse hosts/ew-cloud/disko.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`

### 2026-04-01 (remove ew-cloud public ssh fallback)
- Date: 2026-04-01
- Change: Removed the temporary public SSH firewall exception and root key fallback from `ew-cloud` so only the Tailnet interface remains allowed for inbound SSH.
- Pitfall/Root cause: Once Tailscale is online, leaving public port 22 open is unnecessary exposure. The server baseline already trusts only `tailscale0`, so the remaining task is to reapply that baseline to the host.
- Verification: `nix-instantiate --parse hosts/ew-cloud/config.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`

### 2026-04-17 (deskflow on desktop/laptop only)
- Date: 2026-04-17
- Change: Added `pkgs.deskflow` to the shared desktop and laptop Home Manager profiles so `nixy-desktop` and `nixy-laptop` pick it up without affecting server hosts.
- Pitfall/Root cause: Host-specific desktop packages belong in the `profiles/home/desktop.nix` and `profiles/home/laptop.nix` layers, not in `common.nix`, otherwise servers inherit client-only software.
- Verification: `nix-instantiate --parse profiles/home/desktop.nix` and `nix-instantiate --parse profiles/home/laptop.nix`

### 2026-04-17 (Sony WF-1000XM5 Bluetooth audio)
- Date: 2026-04-17
- Change: Added a client-only Bluetooth audio module that enables Bluetooth on the desktop host, keeps it enabled on the laptop host, turns on BlueZ experimental mode, and pushes WirePlumber Bluetooth roles/codecs toward LE Audio plus native HFP/HSP handling.
- Pitfall/Root cause: `nixy-desktop` had Bluetooth disabled entirely, and the default A2DP/HFP setup will always trade mic support against playback quality unless the headset can use LE Audio/BAP paths.
- Verification: `nix-instantiate --parse profiles/system/bluetooth-audio.nix`, `nix-instantiate --parse profiles/system/desktop.nix`, and `nix-instantiate --parse profiles/system/laptop.nix`

### 2026-04-17 (Bluetooth autoswitch refinement)
- Date: 2026-04-17
- Change: Enabled WirePlumber’s `bluetooth.autoswitch-to-headset-profile` policy so microphone-using apps can trigger headset-profile switching automatically on the client hosts.
- Pitfall/Root cause: Manual profile switching in `pavucontrol` is often not the real missing piece; if the headset was paired before BlueZ experimental mode was enabled, it usually needs to be removed and paired again before the new policy/endpoint set is visible.
- Verification: `nix-instantiate --parse profiles/system/bluetooth-audio.nix`

### 2026-04-17 (Bluetooth stack uplift for WF-1000XM5)
- Date: 2026-04-17
- Change: Switched the client hosts to `pkgsUnstable` BlueZ, PipeWire, and WirePlumber in the Bluetooth audio module so the WF-1000XM5 can negotiate newer Bluetooth audio paths on NixOS.
- Pitfall/Root cause: The stable 25.11 stack exposed only classic A2DP/HFP profiles in practice; the missing LE Audio path was not fixable from `pavucontrol` alone.
- Verification: `nix-instantiate --parse profiles/system/bluetooth-audio.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-04-17 (Bluetooth controller settings for LE pairing)
- Date: 2026-04-17
- Change: Added BlueZ main.conf settings for `ControllerMode = dual`, `Privacy = device`, `KernelExperimental = true`, and `AutoEnable = true` on the client Bluetooth module to remove common LE pairing blockers.
- Pitfall/Root cause: Even with a modern userspace stack, LE Audio can stay hidden if BlueZ is not allowed to use dual-mode/experimental controller behavior or if privacy defaults prevent LE identity matching.
- Verification: `nix-instantiate --parse profiles/system/bluetooth-audio.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-04-17 (Enable bluetoothd kernel experimental features)
- Date: 2026-04-17
- Change: Overrode `systemd.services.bluetooth` to start `bluetoothd` with `-E -K` in the client Bluetooth module.
- Pitfall/Root cause: The runtime logs showed `profiles/audio/bap.c:bap_adapter_probe() BAP requires ISO Socket which is not enabled`, while the shipped unit still launched `bluetoothd -f /etc/bluetooth/main.conf` without `-K`; the daemon never toggled the kernel ISO-socket experimental feature, so LE Audio/BAP never came up even though the headset advertised LE Audio UUIDs.
- Verification: `systemctl cat bluetooth.service`, `bluetoothctl info 80:99:E7:D5:DA:B2`, `journalctl -b -u bluetooth --no-pager | rg -i 'bap|iso'`, `nix-instantiate --parse profiles/system/bluetooth-audio.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-04-20 (docs aligned with flake-parts)
- Date: 2026-04-20
- Change: Updated `README.md`, `docs/target-architecture.md`, and `docs/migration-plan.md` to explicitly describe the dendritic `flake-parts` composition layer; updated `AGENTS.md` to mention `flake/modules/` and `lib/`, and replaced the German transliteration rule with umlaut-preserving wording.
- Pitfall/Root cause: The repo already had the modular flake wiring, but the user-facing docs still read as if the old manual host wiring was the reference model; `AGENTS.md` also still carried an ASCII-only German instruction.
- Verification: `rg -n "flake parts|dendritic|modular|flake module|flake-part|parts" README.md docs/*.md AGENTS.md flake.nix flake/modules/*.nix`, `sed -n '1,220p' README.md`, `sed -n '1,220p' docs/target-architecture.md`, `sed -n '1,220p' docs/migration-plan.md`, `sed -n '1,220p' AGENTS.md`

### 2026-04-20 (krita on all client hosts)
- Date: 2026-04-20
- Change: Added `pkgs.krita` to the shared desktop and laptop Home Manager profiles so all non-server hosts inherit it, including the `michapc` variants through the laptop profile chain.
- Pitfall/Root cause: The cleanest place for a client-only app is the shared client profiles, not individual host files; otherwise `nixy-desktop`, `nixy-laptop`, and the derived `michapc` hosts drift apart.
- Verification: `nix-instantiate --parse profiles/home/desktop.nix`, `nix-instantiate --parse profiles/home/laptop.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-04-20 (shared client modules)
- Date: 2026-04-20
- Change: Introduced shared `profiles/home/client.nix` and `profiles/system/client.nix` modules, moved common client Home Manager state there (`deskflow`, `krita`, DMS defaults, and the shared VS Code baseline), and reduced the desktop/laptop profiles to their host-specific deltas.
- Pitfall/Root cause: New flake files must be staged before git-backed Nix evaluation can see them; the first `nix eval` run failed until the new client modules were added to the index.
- Verification: `nix-instantiate --parse profiles/home/client.nix`, `nix-instantiate --parse profiles/system/client.nix`, `nix-instantiate --parse profiles/home/desktop.nix`, `nix-instantiate --parse profiles/home/laptop.nix`, `nix-instantiate --parse profiles/system/desktop.nix`, `nix-instantiate --parse profiles/system/laptop.nix`, `nix-instantiate --parse hosts/nixy-desktop/home.nix`, `nix-instantiate --parse hosts/nixy-laptop/home.nix`, `nix-instantiate --parse hosts/nixy-desktop/config.nix`, `nix-instantiate --parse hosts/nixy-laptop/config.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-04-21 (nixfmt-rfc-style rollout + niri outputs)
- Date: 2026-04-21
- Change: Switched the repo formatter workflow to `nixfmt-rfc-style` via `nixfmt-tree`, updated editor formatter config for VS Code, Neovim, Helix, and Zed, then applied a repo-wide `nix fmt`; later committed the updated `programs/niri/dms/outputs.kdl` layout separately.
- Pitfall/Root cause: `nix fmt` needed access to the local Nix cache outside the sandbox, and parallel `nix eval` runs contended on the same eval cache SQLite DB, so the host checks had to be retried until they completed cleanly.
- Verification: `nix fmt`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-04-22 (kitty hyperlink handler)
- Date: 2026-04-22
- Change: Added `programs/kitty/open-actions.conf` and wired it into Home Manager so Kitty opens `http/https` links directly with Helium via a background launch action instead of relying on `xdg-open`.
- Pitfall/Root cause: The browser desktop entry fix was not enough for Kitty hyperlinks; Kitty handles `Ctrl+Shift+Click` through its own open-actions path, so the click was still going through the OS default handler and ending up as a blank Helium window.
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, and `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`.

### 2026-04-27 (OpenClaw Codex OAuth model repair)
- Date: 2026-04-27
- Change: Repaired the live `claw` VPS OpenClaw config by replacing hallucinated `openai-codex/gpt-5.5*` and older legacy allowlist entries with the installed Codex OAuth catalog entries `openai-codex/gpt-5.4`, `openai-codex/gpt-5.4-mini`, and `openai-codex/gpt-5.4-pro`; set `gpt-5.4` primary and restarted `openclaw-gateway.service`.
- Pitfall/Root cause: OpenAI API docs list `gpt-5.5`, but this OpenClaw v2026.4.22 Codex OAuth integration did not expose it; `openclaw models status --probe` returned `Unknown model: openai-codex/gpt-5.5`, while `gpt-5.4` probed successfully.
- Verification: `ssh claw 'openclaw models list --all --provider openai-codex --plain'`, `ssh claw 'openclaw models status --probe --probe-provider openai-codex --probe-timeout 30000 --probe-max-tokens 16'`, `ssh claw 'systemctl --user restart openclaw-gateway.service && systemctl --user status openclaw-gateway.service'`

### 2026-04-27 (OpenClaw stable update and GPT-5.5 enablement)
- Date: 2026-04-27
- Change: Updated the live `claw` VPS OpenClaw npm install from 2026.4.22 to 2026.4.24, set `openai-codex/gpt-5.5` as primary with `gpt-5.4` fallbacks, and disabled the optional Bonjour plugin on the VPS.
- Pitfall/Root cause: The update exposed `openai-codex/gpt-5.5`, but the first post-update gateway restarts crashed on the VPS with Bonjour/CIAO probing cancellations; Tailscale Serve already provides remote access, so disabling Bonjour avoids the local mDNS crash path.
- Verification: `ssh claw 'openclaw --version'`, `ssh claw 'openclaw models status --probe --probe-provider openai-codex --probe-timeout 45000 --probe-max-tokens 16'`, `ssh claw 'openclaw models status --plain'`, `ssh claw 'systemctl --user status openclaw-gateway.service'`
- 2026-04-27: Added GitLab CLI (`pkgs.glab`) to shared client Home Manager packages for desktop/laptop via `profiles/home/client.nix`. Verification: `nix-instantiate --parse profiles/home/client.nix`; `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`; `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-04-27 (Helium desktop URL handler packaging)
- Date: 2026-04-27
- Change: Updated `packages/helium/flake.nix` so the wrapped AppImage package installs Helium's upstream `helium.desktop` and icon, rewrites desktop `Exec` entries to the Nix store wrapper, and keeps the Wayland/Ozone flag in the actual `appimageTools.wrapType2` run script.
- Pitfall/Root cause: The previous `desktopFile`/custom `installPhase` attrs were ignored by `appimageTools.wrapType2`, so the built package contained only `bin/helium` and no `share/applications/helium.desktop`; URL handlers in `mimeapps.list` pointed at a desktop entry the package did not provide.
- Verification: `nix-instantiate --parse packages/helium/flake.nix`, `nix build --no-link --print-out-paths ./packages/helium#packages.x86_64-linux.default`, `nix fmt`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-04-30 (nixfmt package rename)
- Date: 2026-04-30
- Change: Replaced live `nixfmt-rfc-style` package references with `nixfmt` in the devshell, VS Code Home Manager config, and client base system packages; updated repo guidance to name `nixfmt`.
- Pitfall/Root cause: `nixfmt-rfc-style` now aliases `pkgs.nixfmt` and emits an evaluation deprecation warning, while the editor command remains `nixfmt`.
- Verification: `nix-instantiate --parse flake/modules/shared.nix`, `nix-instantiate --parse programs/vscode.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix eval --no-write-lock-file .#devShells.x86_64-linux.default.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`.

### 2026-04-30 (devshell kitty terminfo)
- Date: 2026-04-30
- Change: Added `kitty.terminfo` to the flake devshell and exported a devshell-local `TERMINFO_DIRS` for generated Bash and Zsh startup files.
- Pitfall/Root cause: Remote hosts can inherit `TERM=xterm-kitty` without having Kitty's terminfo database installed, which breaks terminal/readline behavior inside the shell.
- Verification: `nix-instantiate --parse flake/modules/shared.nix`, `nix fmt`, `nix eval --no-write-lock-file .#devShells.x86_64-linux.default.drvPath`, `env TERM=xterm-kitty nix develop --no-write-lock-file .#default --command bash -lc 'printf "%s\n" "$TERMINFO_DIRS"; infocmp -x xterm-kitty >/dev/null && echo terminfo-ok'`, `env TERM=xterm-kitty nix develop --no-write-lock-file .#default --command bash -lc 'env -u TERMINFO_DIRS TERM=xterm-kitty ZDOTDIR="$ZDOTDIR" zsh -lc '\''printf "%s\n" "$TERMINFO_DIRS"; infocmp -x xterm-kitty >/dev/null && echo zsh-terminfo-ok'\'''`.

### 2026-04-30 (portable devshell bootstrap)
- Date: 2026-04-30
- Change: Added `scripts/bootstrap-devshell.sh` for curl/wget bootstrapping on non-NixOS hosts and documented the one-line command in `README.md`.
- Pitfall/Root cause: SSH-only servers need a repeatable setup path that installs/sources Nix, clones or updates `~/weasel-os`, and appends the auto-enter Bash block without hand-editing each host.
- Verification: `bash -n scripts/bootstrap-devshell.sh`; `nix shell nixpkgs#shellcheck -c shellcheck scripts/bootstrap-devshell.sh`.

### 2026-05-03 (nixpkgs screen-pipe on laptop)
- Date: 2026-05-03
- Change: Swapped the `nixy-laptop` Home Manager package from the repo-built `screenpipe-app` output to `pkgs.screen-pipe`; kept the custom screenpipe packages available in the flake.
- Pitfall/Root cause: The custom package remains useful for reference/testing, but the laptop host should exercise the nixpkgs `screen-pipe` package instead of the local source build.
- Verification: `nix-instantiate --parse hosts/nixy-laptop/home.nix`, `nixfmt hosts/nixy-laptop/home.nix`. Skipped host evaluation at user request.

### 2026-05-03 (t3code update and external Helium flake)
- Date: 2026-05-03
- Change: Updated the local `packages/t3code` flake to T3 Code `0.0.20` and switched the main Helium input from `path:./packages/helium` to `github:schembriaiden/helium-browser-nix-flake`; kept the local Helium package in the repo.
- Pitfall/Root cause: T3 Code `0.0.20` is the latest stable GitHub release as of 2026-05-03; newer `0.0.21` builds are nightly/pre-release. The maintained Helium flake now packages Helium `0.11.7.1` from upstream tarballs instead of the older local AppImage wrapper.
- Verification: `nix-instantiate --parse flake.nix`, `nix-instantiate --parse packages/t3code/flake.nix`, `nixfmt flake.nix packages/t3code/flake.nix hosts/nixy-laptop/home.nix`. Skipped host evaluation at user request.

### 2026-05-03 (remove screenpipe from laptop packages)
- Date: 2026-05-03
- Change: Removed `pkgs.screen-pipe` from `nixy-laptop` Home Manager packages and left the repo-local screenpipe package outputs untouched.
- Pitfall/Root cause: The nixpkgs `screen-pipe` package was too old for the intended desktop app test, so the laptop should not install either nixpkgs `screen-pipe` or the local `screenpipe-app`.
- Verification: `nix-instantiate --parse hosts/nixy-laptop/home.nix`, `nixfmt hosts/nixy-laptop/home.nix`. Skipped host evaluation at user request.

### 2026-05-03 (remove failing Chaotic substituter)
- Date: 2026-05-03
- Change: Removed `https://nyx.chaotic.cx` from shared Nix substituters.
- Pitfall/Root cause: The Chaotic NYX endpoint returned repeated HTTP 525 responses during `queryMissing`; Nix 2.31.2 crashed the daemon instead of failing gracefully. The cache also had no matching trusted public key in this config, so it was not useful with `require-sigs = true`.
- Verification: `nix-instantiate --parse profiles/system/common.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix build --no-link --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel --option substituters 'https://cache.nixos.org?priority=10 https://nix-community.cachix.org https://yazi.cachix.org'`.

### 2026-05-03 (pin kernel below 7.0 for DisplayLink)
- Date: 2026-05-03
- Change: Changed the shared kernel override from `linuxPackages_zen` to `linuxPackages_6_18`.
- Pitfall/Root cause: After input updates, `linuxPackages_zen` advanced to Linux `7.0.3`; `evdi 1.14.12` failed to compile against it, blocking DisplayLink and the laptop system build.
- Verification: `nix-instantiate --parse modules/overrides/linux-zen-preempt-fix.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.boot.kernelPackages.kernel.version`, `nix build --no-link --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel --option substituters 'https://cache.nixos.org?priority=10 https://nix-community.cachix.org https://yazi.cachix.org'`.

### 2026-05-05 (Millennium Steam package)
- Date: 2026-05-05
- Change: Added the Millennium flake input and overlay, set the shared Steam module package to `pkgs.millennium-steam`, and ignored local WLAN debug artifacts.
- Pitfall/Root cause: Millennium's Nix package pulled in a 32-bit build path that compiled `python3-3.11.14-i686-linux`; activation built successfully but stopped at the sudo password prompt.
- Verification: `nix-instantiate --parse flake.nix`, `nix-instantiate --parse profiles/system/base.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, `nh os switch --hostname nixy-laptop /home/evilweasel/weasel-os` (built; activation blocked on sudo password).

### 2026-05-09 (desktop shell and editor layout settings)
- Date: 2026-05-09
- Change: Committed pending DMS settings, Niri/DMS output layout, and Zed panel docking updates on a PR branch.
- Pitfall/Root cause: Shared Home Manager settings are consumed by multiple host configs, so even UI-oriented JSON/KDL changes need broad host evaluation before publishing.
- Verification: `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-05-09 (bootstrap persisted overrides)
- Date: 2026-05-09
- Change: Updated `scripts/bootstrap-devshell.sh` so the generated Bash auto-enter block persists the install-time `WEASEL_OS_ROOT`, `WEASEL_OS_REPO`, and `WEASEL_OS_BRANCH` defaults.
- Pitfall/Root cause: The bootstrap clone honored overrides only during installation; the single-quoted heredoc wrote hardcoded fallback defaults into `.bashrc`, so later SSH logins could auto-enter a different checkout.
- Verification: `bash -n scripts/bootstrap-devshell.sh`; `WEASEL_ENTER_NOW=0 WEASEL_BASHRC="$(mktemp /tmp/weasel-bootstrap-bashrc.XXXXXX)" WEASEL_OS_ROOT="$PWD" WEASEL_OS_REPO="git@github.com:EvilWeasel/weasel-os.git" WEASEL_OS_BRANCH="codex-update-desktop-shell-editor-settings" bash scripts/bootstrap-devshell.sh`

### 2026-05-14 (merge all branches into main)
- Date: 2026-05-14
- Change: Merged the outstanding feature branch into `main`, preserved the pending Zed Codex ACP registry setting, and normalized `programs/zed/settings.json` to valid JSON.
- Pitfall/Root cause: `micha-debugging` and `origin/copilot/check-screenpipe-build-license` were already ancestors of `main`; the remaining local Zed edit came from an uncommitted stash and exposed pre-existing trailing commas in the JSON file.
- Verification: `jq empty programs/zed/settings.json`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-05-14 (passwordless NixOS administration)
- Date: 2026-05-14
- Change: Added `@wheel` to trusted Nix users and disabled sudo password prompts for wheel users in the shared system profile.
- Pitfall/Root cause: `nh os switch` and `nixos-rebuild-ng` can run different root commands during activation, so narrowly whitelisting individual sudoers command paths is brittle for `fr`, `fu`, and `ncg`.
- Verification: `nix-instantiate --parse profiles/system/common.nix`, `nix fmt`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.ew-cloud.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-05-15 (Sony WF-1000XM5 HFP debugging)
- Date: 2026-05-15
- Change: Stopped forcing BlueZ `KernelExperimental` in `main.conf`, removed invalid `General.AutoEnable`, and stopped forcing mSBC for Bluetooth HFP while keeping Sony headset microphone profiles enabled.
- Pitfall/Root cause: WF-1000XM5 microphone capture still fails in classic HFP with WirePlumber/PipeWire transport errors, and `bluez5.disable-dummy-call = true` made WirePlumber crash. LE Audio UUIDs were visible but BlueZ did not expose BAP/LC3 profiles to WirePlumber.
- Verification: `nix-instantiate --parse profiles/system/bluetooth-audio.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-05-15 (LocalSend client package)
- Date: 2026-05-15
- Change: Added `pkgs.localsend` to the shared Home Manager base package list so client homes get LocalSend while the server profile remains unchanged.
- Pitfall/Root cause: The narrower Home Manager client profile is not imported by every client-class host; `profiles/home/base.nix` is the shared client home layer and is not imported by `ew-cloud`.
- Verification: `nix-instantiate --parse profiles/home/base.nix`, `nix fmt`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.michapc-debug.config.system.build.toplevel.drvPath`

### 2026-05-15 (shared CLI utility baseline)
- Date: 2026-05-15
- Change: Added common classic and modern CLI diagnostic packages to `profiles/system/common.nix`, and expanded `weasel-shell-aliases` with a sectioned cheatsheet for less obvious tools.
- Pitfall/Root cause: Several useful utilities were only in client-specific profiles or absent entirely, so putting the baseline in `profiles/system/common.nix` keeps both NixOS hosts consistent.
- Verification: `nix-instantiate --parse profiles/system/common.nix`, `nix-instantiate --parse scripts/weasel-shell-helpers.nix`, `nix fmt`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-05-15 (delta git pager)
- Date: 2026-05-15
- Change: Configured Git through Home Manager to use `delta` as the pager and interactive diff filter, with line numbers, side-by-side diffs, navigation, moved-line coloring, and `zdiff3` conflict style.
- Pitfall/Root cause: Installing modern CLI tools does not configure applications to use them; Git needs explicit pager/filter settings even when `delta` is already in the system profile.
- Verification: `nix-instantiate --parse profiles/home/common.nix`, `nix fmt`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-desktop.config.system.build.toplevel.drvPath`

### 2026-05-27 (Ratty terminal on laptop)
- Date: 2026-05-27
- Change: Added `pkgsUnstable.ratty` to the laptop Home Manager package list and advanced only the `nixpkgs-unstable` input to a revision containing Ratty.
- Pitfall/Root cause: The previously pinned `nixpkgs-unstable` revision did not expose `ratty`; current `nixos-unstable` has Ratty 0.3.0.
- Verification: `nix-instantiate --parse profiles/home/laptop.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-05-27 (Waylandcraft laptop prerequisites)
- Date: 2026-05-27
- Change: Added `libxkbcommon` to the laptop system package list for `xkbcli`, and documented the manual Prism/Fabric/Waylandcraft setup under `docs/waylandcraft.md`.
- Pitfall/Root cause: Prism Launcher and `xwayland-satellite` were already installed, but Waylandcraft explicitly requires the xkbcommon tools; Prism instance creation remains better left to the launcher.
- Verification: `nix-instantiate --parse profiles/system/laptop.nix`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-05-27 (Ratty laptop configuration)
- Date: 2026-05-27
- Change: Added a repo-owned Ratty TOML config for nixy-laptop and linked it through Home Manager.
- Pitfall/Root cause: Ratty 0.3.0 keybinding config only parses a small set of Bevy key names; German physical plus/minus keys are not directly configurable like Kitty, so reliable zoom fallbacks use `Ctrl+Shift+Up/Down`.
- Verification: `nix-instantiate --parse profiles/home/laptop.nix`, `nix eval --impure --no-write-lock-file --expr 'builtins.fromTOML (builtins.readFile ./programs/ratty/ratty.toml)'`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`

### 2026-05-30 (Factorio Steam launch)
- Date: 2026-05-30
- Change: Removed `gamemoderun` from Factorio app `427520` Steam launch options in the local Steam user config, leaving `%command% > logfile.txt`.
- Pitfall/Root cause: Factorio was not reaching its own log; Steam showed `gamemoderun` exiting 139, and coredumps pointed at Steam overlay/Millennium preload frames before the game started.
- Verification: `factorio --version` via the Steam install path, `steam -applaunch 427520`, `pgrep -af 'Factorio|factorio|SteamLaunch AppId=427520'`, and fresh `gameprocess_log.txt` entries showing Factorio tracked without the immediate 139 exit.

### 2026-08-08 (Blain remote access streaming readiness)
- Date: 2026-08-08
- Change: Renamed the Sophos VPN widget to Blain Remote Access and added separate direct Moonlight/Sunshine route and TCP-control readiness while preserving the RDP-only SSH bridge.
- Pitfall/Root cause: OpenSSH local forwarding is TCP-only, while Moonlight requires latency-sensitive UDP; the streaming path therefore uses a narrowly scoped Tailscale subnet route to `10.145.5.50/32` and reports UDP as unverified until a real stream succeeds.
- Verification: `bash tests/test-sophos-vpn-bridge.sh`, `bash tests/test-sophos-vpn-import.sh`, `shellcheck`, QML parsing with `qmlformat`, isolated `sophos-vpn.nix` build, `git diff --check`, and `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`.

### 2026-08-22 (declarative personal NetBird client)
- Date: 2026-08-22
- Change: Added a hardened `services.netbird.clients.personal` instance on `nixy-laptop` with checksum-pinned official NetBird daemon/UI 0.77.1 binaries, the self-hosted management endpoint, and no routing features; performed one-time setup-key enrollment and removed the bootstrap secret afterward.
- Pitfall/Root cause: The NixOS 25.11 login helper matches `Connected` inside `Disconnected` and can skip setup-key login; a temporary `lib.mkForce` login script was required only for enrollment. Nixpkgs clients below 0.76.0 are vulnerable to GHSA-qcpp-8vwj-hhwr, so the initial 0.71.4 package was immediately stopped/removed and replaced with official 0.77.1 tar/RPM derivations. The UI RPM additionally needs `autoPatchelfHook`, `wrapGAppsHook4`, GSettings schemas, and the module-expected `netbird.desktop` filename. UI 0.77.1 ignores the NixOS module's `--daemon-addr` argument but honors `NB_DAEMON_ADDR`, so the GApplication wrapper must set the per-client socket environment explicitly.
- Verification: independent fail-closed reviews; official release SHA-256 checks; `nix-instantiate --parse` and `nix fmt`; package binary/version, desktop-wrapper and XDG runtime checks; `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`; full `nix build`; successful `nixos-rebuild switch`; `netbird-personal status --check startup`; API peer verification; and App Launcher desktop-entry inspection.

### 2026-08-23 (Wispr Flow on NixOS)
- Date: 2026-08-23
- Change: Added a checksum-pinned Wispr Flow AppImage package and a laptop-only NixOS module with active-session udev grants, `uinput`, desktop integration, and Chromium sandboxing.
- Pitfall/Root cause: Upstream's Nix derivation is knowingly incomplete (`lib.fakeHash`, impure installer input, unreplaced Windows native DB modules). Wrapping the release AppImage is reliable, but its `--no-sandbox` mode must be patched out and `CHROME_DEVEL_SANDBOX` must point to NixOS 25.11's actual `/run/wrappers/bin/__chromium-suid-sandbox` name. `uaccess` rules added through `services.udev.extraRules` land in `99-local.rules`, after systemd's seat-late ACL processing; device rules that need active-session ACLs must instead ship as an earlier `70-*` rule through `services.udev.packages`. `substituteInPlace` replaces every matching occurrence, so patches against repeated shell snippets must anchor on the containing function name.
- Verification: `nix fmt`; `nix flake check --no-write-lock-file`; package and full `nixy-laptop` builds; generated-wrapper, desktop-file, Electron/helper, Wayland/clipboard Doctor checks; static security scan; and independent fail-closed review.

### 2026-08-23 (GeForce NOW Flatpak idempotence)
- Date: 2026-08-23
- Change: Made the declarative GeForce NOW Flatpak bootstrap tolerate the known benign case where the configured remote offers an older commit than the currently installed version while preserving fail-closed handling for other update errors.
- Pitfall/Root cause: `flatpak update` exits non-zero for an older remote commit, which caused otherwise successful `nixos-rebuild switch` activations to report failure.
- Verification: Full `nixy-laptop` build and `nix flake check --no-write-lock-file`; the generated activation script handled the older remote commit and exited `0`.

### 2026-08-23 (NetBird split DNS with Tailscale coexistence)
- Date: 2026-08-23
- Change: Enabled `systemd-resolved` and pinned the personal NetBird client's typed `dns-resolver` option to `127.0.0.153:53`, providing private custom-zone resolution without public A/AAAA records.
- Pitfall/Root cause: Tailscale's `ts-input` chain drops `100.64.0.0/10` traffic on non-Tailscale interfaces; because NetBird uses `100.96.0.0/16`, its default resolver appeared bound on the peer IP but local DNS packets were dropped on loopback. The NixOS NetBird module's typed resolver option declaratively manages `CustomDNSAddress`, the wrapper environment, and `CAP_NET_BIND_SERVICE`, avoiding firewall exceptions and imperative state.
- Verification: full `nixy-laptop` build and switch; Wispr store closure remained identical; direct and system resolver queries returned the private records; normal HTTPS, dashboard authentication, Tailscale MagicDNS, and public DNS passed; Cloudflare API and an authoritative resolver confirmed the public fallback record was absent while NetBird DNS remained healthy.
### 2026-08-04 (add ouch to devshell)
- Date: 2026-08-04
- Change: Added `ouch` to the shared development shell package list.
- Pitfall/Root cause: Devshell tooling is sourced from the central `devPackages` list in `flake/modules/shared.nix`; the desktop host evaluation remains blocked by the pre-existing insecure `docker-28.5.2` package.
- Verification: `nixfmt flake/modules/shared.nix`, `nix-instantiate --parse flake/modules/shared.nix`, `nix eval --no-write-lock-file .#devShells.x86_64-linux.default.drvPath`

### 2026-08-04 (validate devshell rebuild host)

- Date: 2026-08-04
- Change: Made the devshell `fr` and `fu` functions validate `WEASEL_OS_HOST` against the flake host registry before invoking `nh`.
- Pitfall/Root cause: On WSL, `$HOSTNAME` was `Blain37`, which is not a `nixosConfigurations` attribute, so `nh` attempted to build a nonexistent configuration.
- Verification: `nix fmt`, `nix-instantiate --parse flake/modules/shared.nix`, `nix eval --impure --no-write-lock-file .#devShells.x86_64-linux.default.drvPath`, `NIXPKGS_ALLOW_INSECURE=1 nix eval --impure --no-write-lock-file .#nixosConfigurations.<host>.config.system.build.toplevel.drvPath` for all hosts.

### 2026-08-25 (activate the nixy-laptop Codex recovery console)

- Date: 2026-08-25
- Change: Added a declarative, human-invoked Codex recovery console to nixy-laptop: the locked Nix package, sanitized bundle, explicit operating-contract launcher, and an idempotent mode-0600 dedicated Iris recovery key. No Codex auth/config content, token, SSH policy, mesh policy, or cloud state is managed by this module.
- Pitfall/Root cause: The recovery key must be generated at Home Manager activation rather than from a Nix expression, or private-key material would enter the Nix store. Existing keys are only mode-corrected; symlinks and non-regular paths fail closed.
- Verification: offline sanitizer/link/launcher/fake-broker tests, target laptop evaluation and build, activation with NixOS generation rollback, and post-activation local-only console smoke test.

### 2026-08-29 (official ChatGPT/Codex Linux preview)

- Date: 2026-08-29
- Change: Packaged the immutable official OpenAI x86_64 RPM payload for nixy-laptop without executing its repository/updater scriptlets, and installed the resulting unfree derivation through the host configuration.
- Pitfall/Root cause: Locked `pkgs.chatgpt` is Darwin-only. The Linux RPM bundles both Qt 5/6 shims and glibc/musl native prebuilds; use the Qt `out` outputs together and ignore only the unused musl loader during auto-patching, otherwise the Qt setup hook or autoPatchelf fails.
- Verification: `nix fmt -- hosts/nixy-laptop/config.nix packages/chatgpt/default.nix`, `nix-instantiate --parse` for both changed Nix files, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, focused `pkgs.callPackage ./packages/chatgpt/default.nix { }` build, `nix flake check --no-write-lock-file`, and `nix build --no-link --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel`.

### 2026-08-26 (silent NetBird tray autostart)
- Date: 2026-08-26
- Change: Linked the existing pinned NetBird UI desktop entry into the Home Manager XDG autostart directory for `nixy-laptop`, configured with the existing `netbird-personal` daemon socket.
- Pitfall/Root cause: NetBird UI 0.77.1 ignores the NixOS module's daemon-address argument, so autostart must reuse the package wrapper that supplies `NB_DAEMON_ADDR`; starting a separate UI package would otherwise target the default socket or duplicate launcher ownership.
- Verification: `nixfmt`, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, `nix build --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel`, and guarded `nixos-rebuild switch --flake .#nixy-laptop` with post-activation graphical-session, NetBird/Tailscale, DNS, dashboard, and failed-unit checks.

### 2026-08-29 (NetBird-only laptop and deterministic tray startup)
- Date: 2026-08-29
- Change: Made NetBird the sole overlay on `nixy-laptop`, removed the obsolete Tailscale CGNAT compatibility unit and automatic `blain-ai` SSH/Caddy bridge, and moved the NetBird tray from unordered XDG autostart to a graphical-session user service that waits for the personal daemon socket.
- Pitfall/Root cause: NetBird UI 0.77.1 requires a URI-form daemon address and can still fall back to `/var/run/netbird.sock` when only `NB_DAEMON_ADDR` is supplied; pass `--daemon-addr=unix:///var/run/netbird-personal/sock` explicitly. DMS also keeps its launcher inventory in `launcher_cache.json`, so a system switch alone does not reveal newly restored desktop entries until that cache is regenerated.
- Verification: focused shell tests and Nix parsing; required `nix eval`; full laptop build and live switch; `tailscaled.service` absent/inactive; NetBird daemon and tray both reported `Connected` with four peers; tray logs contained no post-restart default-socket failures; regenerated DMS cache contained Vesktop, Zen Browser, and the personal NetBird launcher.

### 2026-08-29 (Vesktop Wayland capture fallback)
- Date: 2026-08-29
- Change: Repacked the pinned Vesktop 1.6.7 AppImage declaratively so a portal-selected Wayland source is handed directly to Electron when Vesktop's redundant renderer-side picker cannot open.
- Pitfall/Root cause: Niri, the GNOME screencast portal, and PipeWire successfully created and negotiated the monitor stream, but Vesktop silently converted a rejected `screenshare:picker` renderer command into an empty callback. Electron then reported `INVALID_DISPLAY_CAPTURE_CONSTRAINTS` and stopped the portal stream. The Linux desktop-capturer path can show the portal twice; Electron's single native system-picker option is currently documented for macOS only.
- Verification: instrumented Electron logging captured the exact failure; a temporary patched instance completed a live screenshare; the patcher matched exactly once; focused package and full laptop builds passed; the installed AppImage contained the patched handler and unpacked native modules; and the new NixOS generation was switched live with Vesktop, DMS, and NetBird active.

### 2026-09-04 (Wi-Fi candidate lineage and NetBird-only DNS safety)
- Date: 2026-09-04
- Change: Added explicit early `iwlwifi` loading to the current `nixy-laptop` lineage while preserving NetBird as the sole overlay and excluding the diagnostic boot service from the release candidate.
- Pitfall/Root cause: A prior candidate was built from stale commit `c13c40e` instead of active laptop lineage `b43e535`; its broad closure reintroduced Tailscale and removed/reverted unrelated laptop packages. NetBird and Tailscale then both mutated `systemd-resolved`, while NetBird's global DNS route temporarily targeted an unavailable encrypted peer and returned SERVFAIL. Never activate a host candidate before comparing its complete closure with the running LKG.
- Verification: Nix parse/eval, effective `services.tailscale.enable=false`, NetBird autostart true, full target build, closure diff against LKG, switch-only activation, public/private DNS, IPv4/IPv6, NetBird SSH and failed-unit checks.

### 2026-09-04 (Vesktop Vencord runtime fallback)
- Date: 2026-09-04
- Change: Added fixed-output Vencord runtime assets and a settings-preserving pre-launch bootstrap to the existing patched Vesktop 1.6.7 package while retaining the Niri Wayland screenshare correction.
- Pitfall/Root cause: Vesktop stores Vencord under `sessionData/vencordFiles`, not `vencordDist`, and considers the payload valid only when `package.json` plus all four runtime assets exist. A bootstrap targeting the wrong directory leaves a plain Discord UI even though plugin settings exist.
- Verification: source and built-artifact regressions, complete laptop build, narrow closure diff, switch-only activation, runtime `Vencord`/`VencordNative` globals, Vencord/Vesktop settings entries, registered and enabled `VoiceMessages` with `channel-attach` hook, one-second unsent Opus microphone capture, normal restart with debug port closed, and post-switch DNS/NetBird checks.

### 2026-09-04 (local CUA and task-bound Codex executor)
- Date: 2026-09-04
- Change: Added pinned Cua Driver 0.23.2 with a graphical-session user service, enabled AT-SPI, pinned Codex CLI 0.153.3, and added a private task-bound executor that persists through SSH loss with local result spooling.
- Pitfall/Root cause: Nixpkgs Codex 0.92/0.133 could not decode the current model catalog or use `gpt-5.6-sol`; global Codex MCPs also added startup failures and latency. The executor explicitly clears `mcp_servers`, restricts writable workdirs, accepts only private prompt files, and keeps `workspace-write` sandboxing. CUA needs both Wayland opt-in and the session AT-SPI bus.
- Verification: fixed-output hashes, package/full-host builds, narrow closure diff, switch-only activation, CUA daemon/doctor/AT-SPI/app/window/screen checks, telemetry disabled, Codex 0.153.3, and a detached systemd executor smoke returning `LOCAL_EXECUTOR_OK` with exit 0 and no MCP startup.

### 2026-09-04 (desktop audio, synthetic key cleanup, and Handy upgrade)
- Change: Updated the ChatGPT/Codex desktop package to 26.901.31953 and added libpulseaudio to its runtime wrapper; integrated the Wispr helper synthetic-key cleanup patch; pinned Handy 0.9.6 with its dedicated nixpkgs and a checksum-preserving official static-crate download override.
- Pitfall/Root cause: Chromium's PulseAudio backend is loaded dynamically, so linking checks alone miss the missing library. Wispr must track and release synthetic keys; repeated helper starts without held keys are not a sticky-modifier regression test. Cargo's API downloads returned HTTP 403 while the official static route worked. Overriding `extraRegistries` for crates-io creates duplicate-source errors; override only the fetch URL instead.
- Verification: Independent review found no blocking security or logic errors. Resolved input graph comparison confirmed only Handy and its dedicated nixpkgs changed. Full laptop builds passed. Actual installed ChatGPT acquired a 48 kHz real-microphone stream and its own audio process mapped libpulse; Tobi specifically confirmed **Codex Voice and GPT-6** work (not a separate Discord-agent acceptance). Wispr differential event-sink tests failed on upstream held Ctrl/Meta and passed cleanup plus 100 paste cycles with the patch. Handy transcribed a synthetic test sentence verbatim with the existing Parakeet V3 model, without recording the microphone or uploading audio. Generation 210 was activated without reboot under a local generation-209 rollback guard, then accepted after DNS/HTTPS, unchanged Niri/user ChatGPT processes and no-failed-unit checks.
- Test isolation: Use an empty private CODEX_HOME as well as a private Electron profile. Verify a CDP endpoint belongs to the launched process before using it, and reap owned groups on timeout. Electron may flatten `/proc/<pid>/cmdline`; confirm cleanup ownership with exact fixture markers and log descriptors, never process name alone. Handy CLI model/transcription commands still need GTK initialization; an isolated xvfb-run display works.
- Limits: Full live Wispr overlay-state/dictation acceptance remains separate. Whole-flake `nix flake check --no-build` encountered an invalid source store path while evaluating unrelated ew-cloud; laptop toplevel and Home Manager evaluation passed. Shared laptop module changes are declarative for its consumers, but only nixy-laptop was activated. Unrelated MIME configuration and worktrees were preserved.

### 2026-09-22 (ChatGPT startup crash on NixOS)

- Change: Point the bundled Parcel watcher's detect-libc LDD probe at the package's glibc, preserve native module access while repacking ASAR, and expose libglvnd to Chromium. Retain ChatGPT 26.901.31953 and its PulseAudio wrapper.
- Root cause: autoPatchelf moved the installed Electron PT_INTERP to offset 315641856, beyond detect-libc's 2048-byte read; /usr/bin/ldd is absent on NixOS. The resulting process.report.getReport() call in the Git worker terminated the app with SIGILL. libGL.so.1 lookup errors were a separate packaging issue.
- Verification: Nix parse and laptop toplevel evaluation; focused package build; test-chatgpt-startup.cjs forces the failed ELF probe, rejects any report fallback, and verifies native file events; test-chatgpt-audio-integration.sh passes. The real GUI starts with the existing profile and stays open past the formerly reproducible startup crash; projects and chats remain visible.
- Final validation: Full laptop system build succeeded; closure comparison against the running system changes only ChatGPT (+126.6 KiB). User confirmed the repaired app stays open and chat switching works. The repository-wide flake check reached ew-cloud but failed on an unrelated invalid Nix store source path in its Home Manager fontconfig evaluation.

### 2026-09-22 (NetBird-triggered kernel split-lock freezes)

- Change: Set `NB_DISABLE_EBPF_WG_PROXY=true` only for the laptop's personal NetBird daemon. Preserve NetBird 0.77.1, kernel WireGuard, DNS at 127.0.0.153:53, peer configuration and service hardening.
- Root cause: EFI pstore records identical `Oops: Split lock detected` at `skb_clone+0x182/0x220` on September 13, 21 and 22, matching upstream page_pool alignment bug `dc0df5a0c62ccea1d0e08d39a4dc9064de81d411` and NetBird issue #7331. Normal journals ended before the crash. NetBird's generic XDP program on loopback triggers the kernel bug; unrelated Niri/NVIDIA errors also occur during clean sessions.
- Verification: Nix parse/format, laptop toplevel evaluation and full build, NetBird integration checks and diff whitespace checks passed. Exact closure comparison found only the wrapper setting and generated references; no package/kernel version changes. Activated under a five-minute rollback timer, then verified actual daemon environment, UDP-proxy startup log, absent loopback XDP, unchanged overlay IP and kernel WireGuard, 4/4 connected peers, 2/2 relays, all three previously pingable peers, all four peer DNS addresses, public DNS/HTTPS and three internal service names. The phone already did not answer ICMP before the change. Rollback timer was cancelled after acceptance; no reboot was needed.
- Limits: Long-term freeze freedom still requires normal use. Whole-flake `nix flake check --no-build --no-write-lock-file` fails on the pre-existing invalid source path in ew-cloud/openclaw Home Manager fontconfig; the affected laptop build passes. Preserve the user's dirty OpenRazer/MIME files and existing worktrees. Client environment options are embedded in the wrapper, so `systemctl show -p Environment` alone does not verify activation.


### 2026-09-22 (Thunar thumbnails and image preview)

- Change: Enable `services.tumbler` in the shared client system profile so Thunar can generate file thumbnails and the standalone image preview.
- Root cause: Thunar was enabled without its D-Bus thumbnailer. The preview options were already correct, but requests to `org.freedesktop.thumbnails.Thumbnailer1` failed with `ServiceUnknown`; even the standalone pane depends on an XX_LARGE thumbnail.
- Verification: `nixfmt --check profiles/system/base.nix`, `nix-instantiate --parse profiles/system/base.nix`, `git diff --check`, and `nix eval --no-write-lock-file .#nixosConfigurations.<host>.config.system.build.toplevel.drvPath` for nixy-laptop, nixy-desktop, michapc and michapc-debug; full laptop builds and `nix flake check --no-write-lock-file` passed. Before activation, `nix store diff-closures` confirmed only Tumbler, ffmpegthumbnailer and libgepub were added. Preserve the active, uncommitted OpenRazer configuration when building the live system; do not activate a clean checkout that removes it.
- Live result: Activated on nixy-laptop without reboot; verified packaged D-Bus activation after stopping the temporary probe, active OpenRazer, no failed system/user units, JPEG/PNG thumbnail caches, and the original screenshot's selected JPEG displayed in the right preview pane. User confirmed the preview works. Existing OpenRazer/MIME files and flake.lock remain unchanged.

### 2026-09-30 (ChatGPT Linux desktop app update)

- Change: Updated the nixy-laptop's declaratively packaged official ChatGPT RPM from 26.901.31953 to 26.928.21956, retaining the NixOS-specific ASAR and runtime-library fixes.
- Pitfall/Root cause: OpenAI's official x64 RPM `latest` link is mutable; resolve it once, read the RPM metadata for its concrete version, and pin the immutable versioned URL plus its Nix SRI SHA-256 before rebuilding.
- Verification: Nix parse, laptop toplevel evaluation, focused ChatGPT package build and startup/audio regression tests, then a live laptop `nixos-rebuild switch` with process and unit checks.

### 2026-10-04 (TUIOS installation and coding-tool updates)

- Change: Install the official TUIOS 0.8.5 Linux binary on nixy-laptop; update T3 Code to 0.0.45, the Nix Codex CLI pin to 0.160.0, and ChatGPT to 26.930.31730. Also update the user's PATH-preferred global npm Codex to 0.160.0.
- Pitfall/Root cause: Codex exists in both npm and Home Manager; updating only one leaves the laptop executor on an older version. Resolve ChatGPT's official mutable latest RPM metadata, then pin its versioned URL and checksum. Preserve the NixOS watcher, ASAR, graphics, and PulseAudio fixes and all existing dirty OpenRazer/MIME changes; leave flake.lock unchanged.
- Verification: Nix parse/format and git diff --check; no-write-lock-file toplevel evaluation for nixy-laptop, michapc, and michapc-debug; focused package builds and full live laptop build; TUIOS PTY/pane command smoke; T3 Code renderer smoke with an isolated profile; ChatGPT native-watcher and audio packaging regressions. Whole-flake check --no-build --no-write-lock-file reaches ew-cloud but fails on the existing invalid fontconfig source store path. The isolated ChatGPT GUI probe did not open a renderer; the currently running desktop process is left intact and needs a normal restart to use the new package.
- Activation: `sudo -n nixos-rebuild switch --flake /home/evilweasel/weasel-os#nixy-laptop --no-write-lock-file` succeeded. The closure changes only the four requested programs and their generated references; both installed Codex commands report 0.160.0, TUIOS reports 0.8.5, and the desktop launchers resolve to the new packages. System/user failed-unit lists are empty after activation.


### 2026-10-07 (Home snapshots and disk reserve)

- Change: Add a nixy-laptop-only Home snapshot module that creates the missing Btrfs `.snapshots` subvolume through tmpfiles, catches up missed daily runs, cleans hourly, retains up to seven daily snapshots, and skips creation below 50 GiB available.
- Root cause: NixOS writes Snapper config but does not create its snapshot subvolume. The old nonpersistent timer missed offline runs; fixed retention and quota-dependent SPACE_LIMIT did not provide a working free-space policy. Use FREE_LIMIT with a 0-7 range without enabling quotas; under pressure all eligible timeline snapshots may be removed. ExecCondition returns 255 for operational errors and 1 only for low-space skips.
- Space: Removed only idle Steam shader caches (~20 GiB) and unused Docker build cache (2.562 GB). Preserved games, saves, models, VM disks, containers, volumes, existing generations, dirty OpenRazer/MIME files, and flake.lock. Approximately 118 GiB remains free after validation builds.
- Verification: Nix parse/format, git diff --check, `nix eval --no-write-lock-file .#nixosConfigurations.nixy-laptop.config.system.build.toplevel.drvPath`, full laptop builds for the publishing and live source, and `nix store diff-closures` (only the snapshot guard added). Verified native snapshot creation, restoring a changed disposable file, live Persistent=yes, hourly cleanup, and successful service results. Whole-flake `nix flake check --no-build --no-write-lock-file` still fails on the pre-existing ew-cloud/OpenClaw fontconfig invalid source path.
- Activation: Switched the preserved live checkout without reboot. A transient Flathub DNS failure on the first activation recovered on retry; repeated native activation passed and both failed-unit lists are empty. Publish from an isolated origin/main checkout to preserve the divergent live branch and unrelated changes.

### 2026-10-07 — Codex ACP and Serena on nixy-laptop
- Pin Codex ACP 2.1.1 to the existing declarative Codex binary and Serena v1.7.0 to its commit. Laptop-only Home Manager module supplies LSP binaries and merges MCP configuration without storing existing credentials in Nix.
- Keep laptop Zed settings separate from the shared settings because the desktop does not import the added packages. Avoid adding unstable nixfmt alongside the existing formatter: Home Manager reports a binary collision.
- Verified laptop eval/build and activation, ACP initialize/session creation, ten LSP initialize handshakes, and Python Serena symbol lookup/references/rename/body replacement followed by execution. Symbol replacement requires correct indentation; an MCP success alone does not prove valid code.
- Full `nix flake check --no-build --no-write-lock-file` remains blocked by the unrelated ew-cloud/openclaw Fontconfig invalid store source. Preserve the user's unrelated local changes when applying this change.

- Desktop MCP startup may use HOME as cwd (which is itself a Git repository here); avoid `--project-from-cwd` in the shared entry. Use Serena activate_project for the current repository to prevent whole-home indexing.


### 2026-10-07 (On-demand Cartesia audio and Proton Pass CLI)

- Change: Install laptop-only Proton Pass CLI 2.4.2 and declarative Codex skills for safe credential injection and spoken Cartesia answers. The audio helper pins Sonic 3.6 (2026-08-27), uses a native German voice, sends an emotion on every segment, joins long answers, validates MP3 output and writes private audio/transcript metadata outside the repository.
- Pitfalls: The pinned unstable package was CLI 2.0.2; override with Proton's versioned binary and verified SHA-256. CLI authentication is separate from the GUI; keep session keys in the existing D-Bus Secret Service. The browser login timed out without confirmation, so Proton vault access remains unverified. The successful Cartesia test used the pre-existing private local credential file without printing its contents or starting Companion. Cartesia documents emotions only for English; German controls remain experimental.
- Verification: Nix parse/format and diff checks; no-write-lock-file laptop toplevel evaluation and full builds for publishing and preserved live source; both skill validators; independent mocked Proton/API failure and long-text tests with real ffmpeg/ffprobe; authenticated voice discovery and a live 23.20-second MP3 that fully decodes. Whole-flake check --no-build --no-write-lock-file still fails on the existing ew-cloud/OpenClaw fontconfig invalid source path.
- Activation: nixos-rebuild switch succeeded without reboot; the live closure adds only Proton Pass CLI and the two skills. Verified installed CLI version, skill store symlinks, Home Manager success, private outputs, active OpenRazer, and unchanged unrelated files/flake.lock. A transient Flathub DNS failure recovered after service restart; system/user failed-unit lists are empty. Publish from isolated origin/main because the live branch contains older local Handy/Wispr packaging changes absent from main.


### 2026-10-07 (Proton-backed German voice comparison)

- Verified the completed Proton Pass CLI login and authenticated Cartesia voice discovery through a masked pass:// reference; switched the audio helper's private runtime preferences to Proton without storing a key in Git or Nix. The selected default Cartesia key is in the Personal vault; the AI vault did not contain it. Leave the existing local credential file untouched.
- Generated ten active native de-DE voice comparisons, five feminine and five masculine, with identical text/model/speed and separate calm/content/sad/angry contexts. Cartesia's public German showcase features Marlene and Sebastian; the ten voices are an editorial shortlist, not a verified provider ranking. German emotion controls remain experimental.
- Verification: Authenticated catalog reads succeeded; 40 bounded TTS calls completed without retries. Initial three-part clips were only 12-16 seconds, so a new angry segment was appended without regenerating the original audio. Final clips are 17.79-22.23 seconds, fully decoded by ffmpeg and checked by ffprobe, with private permissions. MP3s, transcript, nonsecret manifest and ZIP are under ~/.local/share/cartesia-audio/comparisons/2026-10-07-german. No package or Nix source change; skip Nix evaluation for this documentation-only entry.


### 2026-10-08 (T3 project migration, shared knowledge and Rena audio)

- Change: Register the six existing project roots through native T3 RPC without importing chats; preserve the two prior projects and six existing threads. Use the existing declarative Codex binary and CODEX_HOME, merge a managed global knowledge block through Home Manager, and adapt the shared Cartesia skill to private project-local T3 audio previews. Rena remains the user's configured default, with Proton Pass credentials.
- Scope: T3 0.0.45 is already the current official stable release. Move its exact source to source.json and disable the imperative desktop updater; the real backend inherits both T3CODE_DISABLE_AUTO_UPDATE=true and WEASEL_T3_CLIENT=1. Electron main /proc/environ is entirely empty and is unsuitable for checking these flags.
- Update handoff: User clarified that pins require periodic LLM review of their reasons, not indefinite freezing. Provide docs/t3-update-handoff.md for implementing the daily LLM routine in T3 later. No update timer or NixOS-version/input change is installed. Keep the unfinished deterministic updater privately outside Git; its fresh offline GUI test fails because T3 downloads Clerk UI code.
- Verification: Nix parse/format, git diff --check, no-write-lock-file laptop toplevel evaluation and full build in both publishing and preserved live checkouts; Cartesia skill validator, mocked audio/context boundary checks, real T3 project/provider/UI checks and native audio preview without autoplay. The revised 5:27 Rena answer uses two bounded TTS requests and fully decodes with ffmpeg. Whole-flake check --no-build --no-write-lock-file still fails at the pre-existing ew-cloud/OpenClaw fontconfig invalid store source.
- Activation: Create Home snapshots 3 before project migration and 4 before system activation; select the exact built live closure with nix-env --profile /nix/var/nix/profiles/system --set and its switch-to-configuration switch. Activation succeeds, both failed-unit lists are empty, shared-context --check and Cartesia doctor pass. Preserve unrelated source/index history and flake.lock; publish a signed focused commit from the isolated origin/main worktree.

### 2026-10-08 (finish local integrations and restore main checkout)

- Reconcile the divergent live branch with published main while retaining the active Handy 0.9.6 crate-fetch override, Wispr synthetic-key cleanup, OpenRazer and MIME settings. Preserve branch history and nested worktrees; track Serena's Nix project settings and exclude nested worktrees from Git/Serena indexing.
- The previous switch receipt's 277 file hashes still match. Current laptop toplevel evaluation and the active system report the same derivation, so these local changes are part of the running generation rather than untested future updates.
- Verification: Nix syntax/format for Handy/Wispr, shell syntax, ChatGPT audio integration regression and Git whitespace checks pass. Final no-write-lock-file toplevel evaluations for nixy-laptop, nixy-desktop, michapc and michapc-debug and full nix flake check pass. Full laptop build resolves to the exact running system path. The earlier ew-cloud Fontconfig failure did not recur; validation leaves flake.lock unchanged. No application restart or system activation is needed for this source reconciliation.
- Finish the T3 URI handler with the packaged `t3code.desktop` filename. The MIME file is an out-of-store Home Manager symlink, so its correction applies without a system switch. Record parallel-work isolation in AGENTS.md and T3 documentation; preserve the existing nested Wispr worktree.
- Native T3 project-setting writes were denied by its capability check in runtime auto; selecting Worktree at thread creation remains available. Do not claim the default setting was changed or bypass the API restriction.

### 2026-10-08 (T3 nightly, Parallel networking, imported artifact runtime)

- Change: Pin regular T3 nightly 0.0.46-nightly.20261008.2833; add a separate Search/Extract-only Codex rule and shared retry guidance; package command-scoped lcms2/OpenSSL-curl support for the imported LibreOffice renderer.
- Root cause: This T3 harness restricts networking despite the existing working Parallel OAuth configuration. Bundled LibreOffice lacks lcms2 and a CURL_OPENSSL_4-compatible library on the default loader path. Remote main also lacks existing local Handy/Wispr changes; build the complete current local source and preserve those patches.
- Verification: official AppImage digest matched Nix prefetch; exact packaged nightly launch and authenticated UI; isolated V1-to-V2 DB migration (quick_check ok, 8 projects, 7 threads, 309 messages, no model runs); Codex/Claude provider discovery; Search/Extract receipts; execpolicy positive and billing-negative checks; packaged DOCX-to-PDF-to-PNG render; Nix parse, affected-host drvPath evals and complete local laptop build; closure diff limited to T3 and the two helpers. Whole-flake check remains blocked by the known unrelated ew-cloud Fontconfig source.
- Activation: built candidate only; this harness prevents sudo via NoNewPrivs, including escalated commands. Do not report switch or production DB migration until confirmed from the normal host terminal.


### 2026-10-08 (daily update candidate 20261008T201955Z-eaff4a88)

- Change: Prepare codex 0.160.0 -> 0.162.0 with exact official source metadata.
- Verification: Candidate package and laptop builds plus required isolated functional gates passed; preserve their receipts.
- Activation: Pending independent submit verification; authoritative result is in /var/lib/weasel-updates/runs/20261008T201955Z-eaff4a88/journal.json.


### 2026-10-08 (daily update candidate 20261008T203502Z-308348dd)

- Change: Prepare chatgpt 26.930.31730 -> 26.1002.52244 with exact official source metadata.
- Verification: Candidate package and laptop builds plus required isolated functional gates passed; preserve their receipts.
- Activation: Pending independent submit verification; authoritative result is in /var/lib/weasel-updates/runs/20261008T203502Z-308348dd/journal.json.


### 2026-10-08 (own VPS access and Proton SSH identities)

- Change: Repair the existing Hephaestus/NetBird and Hephaestus-to-Iris SSH routes; add a native Proton key in the dedicated Own Infrastructure SSH vault and its scoped persistent user agent. Declare only the new SSH fragment and service in laptop Home Manager. Back up the unchanged encrypted legacy key in Personal without its passphrase.
- Root cause: The personal NetBird daemon was idle, the old accepted key was locked, and Iris intentionally accepts SSH through Hephaestus. Preserve host keys, other identities, cloud firewall, Iris root-login ban, and default agent; source-restrict the new server keys. The Proton DBus keyring backend and exact vault scope are required.
- Verification: New-key-only real SSH as Hephaestus hermes/root and Iris aidan plus sudo all passed after the service handover; forbidden forwarding denied, permitted Iris jump passed. Agent enabled/active with one expected identity and socket0600. Encrypted backup round-trip byte equality, original inode/mode preserved; exact Home Manager unit build, Nix format/parse and laptop drvPath evaluation passed.
- Remote build limits: Hephaestus has an intentional Nix budget admission wrapper with zero budget and no exposed KVM; Iris has no Nix/KVM and little RAM. Neither was provisioned as a builder, rebooted or switched. Document evaluation/build/VM-test architecture separately from actual host activation.


### 2026-10-08 (daily review, deterministic update gates and protected bootstrap)

- Change: Install a normal-user review/candidate helper, 37-pin registry with explicit daily coverage of all70 actual resolved lock nodes, bounded T3-nightly/Codex/ChatGPT discovery, real isolated GUI/native/ACP/migration gates, and a fixed root inbox activator plus exact-source bootstrap. Native T3 scheduler uses existing Codex auth/harness; dispatch success is not provider or activation completion.
- Protection: Signed Git-object bytes, immutable source archives, strict generated-closure pairs, main/index/remote/system CAS, retained source inodes, AC/battery/space gates, real read-only Home checkpoints, durable journals before transitions, bounded own-artifact retention and system-only rollback. No Home restore or caller-provided root commands. Source changes, missing/unfinished journals and later candidate edits/ignored files/open descriptors are preserved and block or skip cleanup.
- Verification: 114 tests passed (discovery19, gates37, activation28, bootstrap14, preparation16); Nix format/parse, laptop toplevel evaluation and full nix flake check --no-write-lock-file passed. Complete combined laptop build is /nix/store/al7jc9dzwfaym2a8izjxmkngvmm0dbwj-nixos-system-nixy-laptop-25.11.20260501.26ef669. Built immutable helper imports/status/full registry and exact-node review recording passed; flake.lock SHA256 remains734d9ff0fe2bdbfa8e8f0a1cf6273f38c2a4d8944f9bb02134734ad49e95cfc1.
- Real app evidence: Codex0.162.0 CLI/app-server and bound ACP2.1.1, ChatGPT26.1002.52244 rendered UI/native watcher/PulseAudio, and distinct T3 stable0.0.45-to-nightly DB migration54-to60 passed without personal profiles or model requests. T3 needs its public Clerk frontend and a private non-loopback interface for navigator.onLine; all other outbound destinations remain blocked. Xvfb/socket paths must remain short. DBus system/user units require distinct structural roles, not a shared name whitelist.
- Initial candidates above were integrated as separately signed pin commits for the combined first bootstrap, not submitted as daily activation requests; their private tested receipts exist, their illustrative root run journals do not. Root service/first Home checkpoint/system switch remain unobserved until the one normal-terminal bootstrap. NoNewPrivs1 blocks sudo; manual scheduler dispatch was denied in runtimeauto and was not bypassed. Review receipts distinguish unresolved upstream research from passed candidate gates; supported26.05 migration and remaining adapters stay explicit separate work.

- Bootstrap reporting correction: The root installer does not observe or control T3 scheduler enablement. Return managed-separately-by-T3 rather than inventing scheduler_enabled=false; native API state is recorded independently. Bootstrap14 tests passed again. The preceding full-build path is retained as an intermediate result; the final immutable receipt selects the rebuilt reporting correction.


### 2026-10-08 (actual bootstrap invocation and Nix multicall names)

- Root cause: The final real built UserRunner preflight caught a failure that mocked transaction tests missed: resolving env/nix-store to their final coreutils/nix binary changes argv0 and loses the requested applet. Pin the immutable containing directory, preserve the requested basename, and independently require the final target inside the Store. Reject mutable FHS parents; the service already supplies an immutable runtime PATH.
- Verification: Real isolated env output and actual nix-store --version pass in the new regression; activation29 and bootstrap14 tests pass. Normal-user signed-source/remote/manifest/exact-system preflight is repeated using the final built code before producing the replacement receipt. No sudo, privileged executor or system activation occurred. The previous receipt is superseded by a new exact signed-source receipt.

### 2026-10-08 (native T3 launcher for approved sudo)

- Passwordless wheel sudo already exists. The AppImage/FHS bubblewrap launcher sets NoNewPrivs on T3 and its Codex backend, including approved out-of-sandbox commands. Package the same upstream payload natively with autoPatchelf and GTK/tray libraries; keep app.asar byte-identical and retain T3/Codex permission controls.
- Verification: Nix parse/format and git diff checks; no-write-lock-file laptop drvPath evaluation and full build; full flake check without builds; native PTY, keyring, FFI, accessibility and SQLite smoke tests; isolated GUI window and Environment HTTP 200. Agent commands inherit ELECTRON_RUN_AS_NODE, and T3 overrides --user-data-dir, so GUI tests must unset the former and isolate XDG paths as well as T3CODE_HOME.
- Activation remains pending: an existing NoNewPrivs process cannot clear the flag for children. Restart the native candidate once from the normal host terminal, then verify an approved sudo -n id -u. The Main system candidate also contains earlier pending tool/updater updates; do not silently activate those as part of this packaging task. See docs/t3-native-sudo.md.


### 2026-10-09 (native T3 gates and canonical URI association)

- Native T3 owns app.asar inside its own output; probe that archive before considering a legacy extracted AppImage closure. Ambiguous archives and embedded version mismatches fail closed.
- Preserve the native app's actual com.t3tools.T3Code.desktop MIME association and package the same hidden URI alias. A pre-existing user-local generated desktop file may still shadow the packaged alias; do not claim that alias proves the live launch path.
- Verification: 41 gate regressions; exact native m8jzhfcpqlnmfis7rfndw5fx0vx0hq4d package build; actual private renderer/backend and stable-to-nightly database migration pass. Four affected host evaluations passed. No user profiles or model requests.


### 2026-10-09 (broad daily batches with independent activation)

- User scope is all configured channels and applicable custom pins together, then cached full builds and targeted repair/bisection; daily 12:00 Europe/Berlin. Retain explicit paired 25.11-to-26.05 release-migration mode and unchanged system/home stateVersion. Missing per-app GUI adapters are build-only coverage, not a blanket package hold.
- Schema2 reuses signed direct candidates, frozen independent all-host/flake/laptop checks, source-inode retention, power/space/Home snapshot guards, exact activation and durable recovery. Protect effective Root units, auth/state settings and own-infrastructure SSH/vault metadata, not just source filenames. Root probes stay ordinary-user processes; candidate code never replaces installed verifiers.
- Real checks found hidden Git flags, private-umask checkout/learning mode drift, reused failed probe directories, NixOS generated Unit defaults and HM ExecStart list coercion. Preserve physical modes and exact signed blobs, allocate new retry fixtures, compare generated effective units and normalize only immutable CLI roots. Existing baseline lock contains an identical duplicate systems_4 node; only the unambiguous old duplicate is readable and new candidates must canonicalize it.
- Niri requires validate --config FILE, not --config FILE validate. Actual built Niri validates all16 generated/frozen includes; a real CLI regression protects that contract. Native T3 final GUI/backend/migration passes. Seven verifier suites pass202 tests; all4 actual host/sensitive evaluations pass. Full final-source flake/laptop builds and bootstrap are recorded separately, with no activation claim from dispatch status.

- Final integration preserves the actual current runtime MIME bytes: T3 sets the canonical default handler without an Added Associations entry. Match the task source to that edit before fast-forward; do not overwrite or stage Main. A fresh approved host command again inherits NoNewPrivs1 and sudo refuses it, so privilege is not proven by an earlier successful check; supply the final immutable built bootstrap for a normal terminal if this persists.

## 2026-10-09 — Niri computer-use implementation (activation/acceptance in progress)

- Added a single-writer Rust Niri actor, direct guarded AT-SPI adapter, additive Codex/T3 MCP merge and optional budgeted Decisions evaluator; declarations keep credentials outside Git. Real Codex native/Electron/Zen/cross-app flows were verified; direct T3 native edit/save works.
- German shortcuts require a refreshed canonical keymap; Electron wtype lost physical keycodes/Unicode. Verified isolated VS Code uses keyCode dispatch and clipboard input. Rich clipboard preservation remains an explicit pre-input capability refusal pending adapter work.
- T3 backend exit cleanup hung on its desktop-browser IPC worker. It became healthy before the prepared kill; no recovery GO/signal was executed. Preserve failure/recovery proofs and distinguish daemon/build from real integration.
- Validation so far: Rust locked/offline build+regression; baseline laptop/desktop full builds and flake check; final changed source still requires final host builds, guarded activation and broad live acceptance. Whole-tree nix fmt updated lock/unrelated formatting; reverted only those generated changes and use no-write-lock-file/scoped formatter going forward.

### 2026-10-09 (clipboard preservation and visible accessibility scope)

- Added bounded data-control preservation in RAM, original Chromium MIME/provenance restoration and an independent source-holder user scope. Three real T3/Code edits preserved all eight original MIME payloads; holder survived actor restart and exited on a fresh copy. Wayland has no atomic ownership CAS; cancellation, takeover or ownership loss skips restoration.
- Review repaired late/nonterminal clipboard replies and moved the ownership check immediately before paste dispatch. Regression coverage: 13 clipboard tests, 3 core tests, 7 helper protocol cases and 2 lease cases; final live recheck remains part of acceptance.
- Direct AT-SPI now reports a versioned selected-window SHOWING-ancestor scope and pruning diagnostics. Unknown states, immediate visibility contradictions and exhausted budgets are read-only; omitted deeper hidden descendants are not attested. Nine traversal regressions pass; actual Meld/Zen compatibility must be verified after backend replacement.
- Keep config inspection to explicit nonsecret MCP fields: unrelated MCP arguments can contain credentials. Do not print complete config sections or subprocess argument vectors.

- Real visible-scope probes reduced Meld from 831 to 65 retained nodes (~98ms); direct text edit plus freshly described Save stored exact bytes, other pane unchanged. Zen exposed 41 visible nodes (~83ms) instead of exhausting 1000, but its first setter acknowledged a no-op; fresh UI caught it and the visual workflow recovered with exact submitted values.
- Gecko bulk GetActions overwrote names with keybinding strings, and GetText beyond CharacterCount returned empty. Read individual bounded named actions and exact bounded text ranges; 14 adapter regressions and own-localhost read-only probe pass. SetTextContents acknowledgement is not effect proof; preserve that first failure and verify browser typed/named-action paths live before acceptance.

- Final Gecko Store live proof: named activate focused the real Zen field; typed Unicode/multiline input and freshly named press submitted exact trusted FormData. AT-SPI adds a FEFF representation marker after the supplementary character while submitted bytes do not; do not alter user text from that representation alone. Meld native semantic edit/save was exact after accounting for its automatic final LF; retain the initial extra-LF artifact failure.

### 2026-10-09 (compositor shortcuts, local MCP approval and API idle transport)

- A real T3 native-suite Super+Shift+F acknowledged input but typed F into Meld. The pinned Smithay virtual-keyboard path bypasses Niri's compositor shortcut filter. Add a separate guarded owned uinput route for Super aliases and explicit key_scope=compositor; preserve app Wayland routing. Proxy revision/session/epoch checks refuse older actors before the whole batch. Offline core17 and independent review12 passed; final Store activation and live global-key/stop acceptance are separate outstanding checks at this commit.
- Add accurate MCP effect annotations and approve only the authorized weasel_desktop server. Both actual ordinary Codex owners (declarative0.162 and PATH0.161) discovered and called the real backend without changing global approval/sandbox settings; final-package workflows remain to be checked after activation.
- One idle HTTPS image request disconnected with unknown billing; preserve its reservation and do not replay it. Close completed connections after30s idle before a new POST, retaining pending-worker precedence. Fourteen offline transport/ledger tests passed. Twenty new image-choice calls all returned the expected choice; warm n9 per endpoint medians were236.84ms Decisions and1505.29ms Responses, not a whole-task or Mac-parity claim.
- First guarded switch succeeded but NixOS restarted the paused own updater path. No updater service ran. Exact immutable post-review repaired only the missing getent pin and retained both original errors; reviewed r2 uses a fresh frozen/live-identical pause and unchanged idle-service identity to stop only that path after switching. Never reuse completed pause records or relax installed activation gates.

### 2026-10-09 (GTK clipboard, action arguments and GUI lifetime)

- Real Meld Copy offered GTK_TEXT_BUFFER_CONTENTS and serialized GTK rich text. GTK3.24.51 marks the former SAME_APP and excludes it from persistence: omit without reading/reoffering pointer bytes, while preserving the exact serialized rich MIME in bounded RAM. Unknown/sensitive/oversized offers still refuse. GtkSource's implicit final LF explains native Copy/file differences; exact-file transfers need an explicitly verified conditional GUI normalization, not hidden adapter text replacement.
- Type ignored restore_clipboard:false because that field belongs to Paste. Reject unknown/misplaced fields in complete actor batch parsing and every proxy Action before forwarding, including against older actors. Tests cover a valid earlier edit followed by a misplaced policy field. No fallback or global approval change.
- Three isolated Calc test profiles disappeared at ordinary CLI task completion alongside running exec-session finalization; exact OS signal sender remains unproven. Saved artifacts and original inputs were intact. Launch apps that must survive an agent through distinct transient user services and verify actual profile/PID/window binding; an existing single-instance app may forward elsewhere.
- Validation: core18 and clipboard14 locked/offline Rust tests; scoped nixfmt, git diff --check, no-write-lock-file flake check and full laptop/desktop builds passed. New GTK live preservation, fresh ordinary clients, final stop/reconnect tests and guarded activation remain separate acceptance steps.

### 2026-10-09 (scoped cancellation and source-package update compatibility, in progress)

- A normal T3 task reported interruption while its physical desktop Wait continued. Preserve that live failure: local Stop acknowledgement is not actor release. Patch both pinned Codex MCP call paths to forward request-scoped cancellation; build the matching CLI and code-mode host with the separately frozen Rust 1.95 toolchain and paired V8 assets. Source build and live Stop acceptance are still pending at this entry.
- Add a connection-local typed cancellation registry with permanently spent request IDs and prioritized controls. The actor's explicit release recovery retries only existing actuator release/receipts, preserves the takeover cause and epoch, and invalidates observations on success; it never replays input. The final Core package built with 44/44 locked tests; that does not prove a fresh ordinary client's Stop path.
- The daily updater previously assumed the removed npm Codex file. Represent the five-file source/patch/toolchain bundle, preserve it during unrelated batches, review official latest daily, and verify candidate-relative source ownership plus actual built CLI/ACP links and the unique ACP export. The integrated updater suites pass 224/224 without skips; their private fixtures are not desktop-task coverage.
- Review caught a missing owned profile-outlink prune entry and a new test that assumed the author's sibling fixture directory. Keep original failures; own outlink cleanup preserves external targets/foreign notes, and portable tests use disposable default temp directories. Whole-tree no-write-lock formatting still changed four unrelated baseline files; preserve that generated patch and restore only those own edits, then use scoped formatting.

### 2026-10-09 (completed Source build; live cancellation remains open)

- The exact pinned Source Codex and matching code-mode host built successfully. All seven cancellation tests passed; the actual host executed a tool-free stdio Unicode/arithmetic probe with exact output and exit 0. Final laptop/desktop builds, no-write-lock host evaluations and flake check passed. The actual selected Home Manager profile and Source-bound ACP were evaluated; isolated app-server/ACP initialize passed without copying auth or making model requests.
- Source binaries being built or passing isolated protocol tests does not mean the running T3 process has changed or physical Stop has passed. Preserve the failed live Stop case; activate through the protected updater-coordinated path, then qualify fresh ordinary clients and real cancellation/takeover/reconnect only after the user returns desktop control. The desktop stays stopped while the user reads messages.

### 2026-10-09 (protected R4 activation and T3 provider reuse)

- The exact signed Source/Core generation activated through frozen R4 guards, a new readonly Home snapshot 11 and an independently bound readonly postreview. Current system/profile agree; the owned daily schedule and updater path were restored only after postreview passed. A path unit has no MainPID field; validate its loaded/inactive/dead state separately from the service's complete idle identity.
- Production Core44 is running without simulation flags, with physical takeover cause preserved, no active task or queued actions, and release confirmed. Mutable Codex MCP configuration points to the new Core store binary. Actual ordinary T3 child calls to desktop_status/desktop_windows passed without desktop input or capture.
- A new delegated T3 thread reused the already running stock Codex app-server despite the per-user profile pointing to Source Codex. Restart T3 after activation, then verify the actual app-server executable before qualifying the new Stop path. Tool reconnection alone does not reload Codex. Live cancellation/takeover/reconnect and the user-facing demo remain pending.
- Verification: protected manual --guards-only/--execute, frozen postreview --guards-only, exact journal/snapshot/source binding, systemd process/store identity, scoped MCP TOML fields, direct normal T3 tools, and scheduler reread. No new build was needed for this docs-only record.
- After the user-controlled T3 restart, actual app-server and code-mode-host processes both resolve to Source Codex. Preflight captured all three outputs and verified the local download hash without input or resume. Fullscreen Zen exposed contradictory visibility ancestry, so its direct semantic snapshot correctly remained read-only; prepare the fresh-image/guarded-keyboard route instead of treating that limited tree as an absent control. Actual live Stop acceptance still awaits the demo.


### 2026-10-09 (cooperative desktop input and task indicator)

- The user replaced the all-physical-input takeover policy: ordinary input invalidates observation generations and stops only a colliding batch, preserving completed/possible effects for fresh recovery. Physical Esc remains explicit abort; owned synthetic Esc does not. Authoritative EVIOCGKEY snapshots retain aggregate held-control counts only, refusing temporary held/unknown state without a permanent latch. Preserve existing legacy/explicit markers across upgrade.
- Added an isolated C/GTK layer-shell blue monitor indicator with a Python task lease. It spans thinking gaps, never takes input/focus, and binds the selected output plus actor session/epoch. A read-only review found fragmented responses could exceed a per-recv timeout; added a total monotonic deadline and regression check.
- T3 desktop work defaults to one normal Codex-authenticated GPT-6-luna child with medium reasoning; the parent stays on its chosen model. Separate Decisions predicate/choice evidence is optional and budgeted, and does not plan free actions.
- Verification before activation: 57 locked/offline Rust tests and 14 headless indicator lifecycle/deadline tests passed; scoped Nix format/parse and diff checks passed. Root runs combined host eval/build and flake checks before signed integration. These component results do not prove physical interleaving, Esc, overlay click-through or T3 Stop; live acceptance remains separate.
- Combined verification: nix eval --no-write-lock-file for laptop and desktop drvPath, nix flake check --no-write-lock-file, and both host toplevel builds passed. Exact final laptop closure: /nix/store/rv9bk1qj3n9awcsiavm2fj2f44bmwaxm-nixos-system-nixy-laptop-25.11.20260501.26ef669; flake.lock unchanged. Activation and live GUI tests are still pending at this source commit.

### 2026-10-09 (preserve live MT Code URI registration)

- MT Code wrote two T3 scheme-handler lines through the existing out-of-store MIME symlink during R5 integration. The user expressly approved preserving both bytes in a separate signed commit before activation. Copy only that exact approved delta into the task worktree; never reset or rewrite live Main to silence the clean-source gate. The earlier frozen receipt was invalidated without attempting activation.
- The existing desktop entry is mtcode.desktop pointing to the installed MT Code 0.1.24. No other default handler or user association was changed. Re-evaluate the affected host configurations and bind the final activation receipt to the resulting signed Main.

### 2026-10-09 (MT Code parallel voice trial)

- Add MT Code 0.1.24 as a separate pinned native AppImage package and laptop launcher, retaining the official T3 Nightly package. MT uses its own `.mt`/`.config/mt` data and local server port; do not inherit `T3CODE_HOME` or `T3CODE_PORT`. Keep app updates in Nix.
- MT's OAuth transport registers `t3code://` at startup and can write through the existing Home Manager MIME symlink into the repository. Guard that registration in the packaged ASAR so future starts preserve the configured handler. Retain the separately approved handler commit from the concurrent Computer-Use task, and serialize activation after that task finishes.
- Verification: scoped nixfmt and parse, `git diff --check`, no-write-lock laptop evaluation, flake check and full laptop build; actual separate MT window rendered its setup dialog with the local server connected. No voice/model request or remote-client compatibility trial was performed.

### 2026-10-09 (read-only observation during cooperative input)

- Two normal Luna demo attempts stopped before UI input: one confused an inactive historical takeover reason with a live latch; the next ended after two capture conflicts. Preserve both failed runs. Status now separates the active reason from historical last_reason, and already-resumed resume is idempotent only after actor release/queue guards.
- Successful capture remains readable during ordinary physical input and reports input/action readiness plus original/current generations. Keep the capture-start generation; stale images still reject input with zero dispatch. Drop the batch input guard only after owned input release, before post-action capture. True Esc/cancel, focus and capability failures retain their fences. The skill gives temporary contention a bounded recovery window instead of abandoning the workflow after two frames.
- Verification: 62 locked/offline Rust tests, scoped rustfmt and git diff --check passed; initial full laptop build produced s2bnxwg8lbmwk6ajslcx7b0xzwcc8x88. No-write-lock laptop/desktop drvPath evaluations, full desktop build and flake check passed; flake.lock unchanged. Signed integration and guarded activation follow. Component tests and a built closure do not prove a successful live Luna workflow; fresh-client desktop acceptance remains pending, with no new GUI input before 10 October 01:00 Europe/Berlin.

### 2026-10-10 (fresh identity before desktop focus)

- An independent shim reproduced acceptance of an old observation for Focus, including a changed PID under the same fixture Niri ID. Focus now requires readable capture, a <=60s observation, unchanged input generation/released controls and matching unique current Niri ID, PID, app ID, workspace, layout and floating state; recheck cancellation/deadline/epoch before dispatch. Focus remains standalone and requires fresh observation afterward. No live Niri ID reuse was demonstrated.
- Three regression tests cover identity/age/control state and the real action chain against an isolated private command shim. The subprocess asserts one test actually ran. Keep the final compositor inventory-to-dispatch boundary and live qualification separate from pure test proof.
- Verification: 65 offline package tests in the full laptop Nix build, scoped rustfmt, git diff --check, unchanged flake.lock, no-write-lock drvPath evaluation for laptop/desktop, both host builds and nix flake check passed. Laptop closure ipq1b983pf3rr6isij3jb5a2pbpkvasf; activation and fresh ordinary-client GUI acceptance follow.


### 2026-10-10 (bound resume, preserved stop causes and MCP presentation)

- An independent socket/registry reproduction showed that Resume was not request-cancellable. Bind active resume to the observed current backend session/epoch and use a distinct bound wire method, preserve stronger real Escape causes, keep failed persistence/rollback logically stopped, and classify both resume names as cancellable non-priority work. Startup capture-not-attempted differs from failure; quiet/readiness remains advisory.
- Keep MCP projection at the stdio edge: emit IDs, errors and partial effects before inventories, preserve every guard/window/image, and retain exact full replies with detailed=true. Only omit display-mode catalogs whose entire contents have known valid fields; an independent nonselected-mode regression caught silent loss of future/malformed entries. Preserve original failed probes and tests.
- A real FileRoller click landed in adjacent own Thunar despite unchanged pixels. Small fresh crops and post-click focus checks are required; never infer model-prepared sizes. Later Code saves were correct but violated the explicit crop procedure, so artifacts alone cannot close acceptance.
- Meld failed before opening because T3's LD_LIBRARY_PATH loaded two GLib/AT-SPI sets. A pure import succeeds after removing only that variable from the Meld child; retain global T3/session environment and prove actual edit/save separately. No display-isolated startup probe proves GUI success.
- Verification before final integration: scoped rustfmt/diff checks; Root full laptop build with87 package tests; independent combined locked/offline107 tests and two original failure regressions after repair. Final-source host/flake builds and live startup/Stop/flows remain separate gates recorded below.
- Final combined source verification: scoped rustfmt, diff checks, no-write-lock laptop/desktop drvPath evaluations, flake check and both host builds passed; the actual Nix Core build ran107 tests successfully. Laptop closure z0mkwayzfkbgq6qbb2azwqh0g9lfll9h; flake.lock unchanged. Meld then completed Task11 through its real GUI with exact50-byte UTF-8 output after the process-local library-path repair. Final Stop, interleaving, fresh-client and mixed-suite acceptance remain open.

### 2026-10-10 (managed Codex precedence in laptop shells)

- Fresh and inherited Bash shells selected the legacy npm Codex0.161 because the laptop sessionPath prepended .npm-global. Keep that installation and its other commands, but put the declarative Codex0.162 bin first for new logins and use an idempotent Bash-local prefix for terminals inheriting an already-sourced Home Manager environment. Preserve existing shell aliases, completion, global Codex config and authentication.
- Private actual Bash-hook probes preserved the warmup alias, selected the existing Source Codex and avoided duplicate leading prefixes. Scoped Nix formatting/parse, host evaluation, flake check and laptop build follow before signed integration; ordinary GUI/Stop qualification remains separate.
- Actual PATH-fix validation passed: scoped nixfmt/parse and diff check, no-write-lock laptop drvPath evaluation, full flake check and laptop toplevel build. Candidate qd3myikim1jpz5i5033mi8hkwmj728iz; unchanged Core and flake.lock. Activation and a real new ordinary Codex workflow remain pending while the physical MX Keys reports a held control.

### 2026-10-10 (typed desktop action schema)

- Two real Meld calls failed before dispatch because the generic action schema allowed missing Focus.window_id and an invalid Key.window_id. Publish eleven discriminated variants aligned with existing serde fields/defaults/nullability; retain runtime guards and reject unknown variant fields. Independent contract and pinned Codex conversion probes preserve the complete union below its5000-byte schema budget; this is no live latency claim.
- Document the actual direct AT-SPI query as Unicode-lowercase literal substring filtering after snapshot/handle construction. It narrows returned nodes, not traversal, scope or permission.
- Verification: private locked/offline111 tests and54 independent JSON-schema cases passed; final repository host/flake builds and fresh ordinary-client GUI qualification follow.
- Final exact-source verification passed: scoped rustfmt/diff, no-write-lock laptop/desktop drvPath evaluations, full flake check and both host builds. Actual Nix Core ran111 tests successfully. Laptop candidate b9vjlllffvg2nmb3g6cc99v9gmbb0sa2; flake.lock unchanged. Signed-Main binding, guarded activation and fresh-client GUI/Stop qualification remain separate.

### 2026-10-10 (pointer crop bound before whole-batch admission)

- A real Luna Clipboard run produced exact recovered bytes but dispatched one pointer action from a 2752×1152 overview. Preserve that failed crop qualification and both GUI newline repairs. Enforce a 1200-pixel maximum on each displayed-view axis for Move/Click/Scroll/Drag at the earliest all-action preflight; mixed batches reject before earlier text/chords, queue, actor or device creation. Full reference dimensions do not reject a valid crop. Focus, keyboard and semantic guards stay unchanged.
- Publish the same actionable rule in the MCP description and laptop skill, and request the task indicator's first TTY result after 1000ms so its ready event does not inherit a ten-second tool wait. No live speed claim follows.
- Verification: pinned Rust 1.91.1 cargo test --locked --offline passed 113/113, including oversized views on both axes, valid crops of large references and mixed Type/Super-before-Click zero-effect refusal; scoped rustfmt and git diff --check passed. Host/flake builds, signed integration, protected activation and fresh-client GUI retest follow in private receipts.
- Original hermetic Nix build retained: the Super-before-Click regression initially saw the missing-device error before the crop error (112/113). Check the batch-wide pointer view before action-specific capability reads, retaining all later validation; rerun the same test and full required source validation.

### 2026-10-10 (readable semantic labels and bounded reference decode cache)

- Ordinary or held input no longer discards direct AT-SPI labels. Separate tree availability from action readiness; incomplete or initially/finally non-actionable reads issue no new handles. A final conflict removes only that read's staged handles. Escape, deadline, cancellation and all mutation guards remain. Native MCP cancellation of this read is still limited by its existing Other request classification.
- Cache one exact reference decode within 16 MiB pixels and 4 MiB PNG; re-read bytes and validate observation/session/epoch/generation on hits. Keep fresh current capture/decode for Click/Scroll/Drag, invalidate on stop/recovery, and perform I/O outside the mutex. These are retained payload bounds, not RSS or a measured live speedup.
- A real noninteractive login shell still selected npm Codex despite the interactive fix: Home Manager's inherited session guard and Bash's interactive-only return skipped selection. Share the idempotent owned prefix in profileExtra and initExtra. App discovery also checks the installed user profile before declaring a guessed system launcher missing.
- Verification: pinned rustfmt, scoped nixfmt/parse, diff check, no-write-lock laptop/desktop drvPath evaluations, full flake check and both host builds passed; actual hermetic Core build passed 130/130 tests. Laptop candidate m01aixx7pj89y8paxjw46hg9mygmv0c7; flake.lock and Core Cargo.lock unchanged. Signed integration, guarded activation and fresh-client GUI/Stop acceptance remain separate. Preserve ordinary Codex's failed launcher and Clipboard runs; exact artifacts and visible pane focus are not yet qualified.

### 2026-10-10: Verify semantic element focus separately from window focus

- Added standalone opaque-handle `semantic_focus` through AT-SPI Component.GrabFocus. Revalidate the same object, window, owner, modal context, generation and stop state; acceptance alone is insufficient without fresh FOCUSED readback. Dependent keys require new observation and semantics; no pointer or keyboard fallback is sent by the focus operation.
- The actual R12 recovery preserved a physical-Escape stop after an earlier wrong-element Code click; file-manager partial effects are not complete Task 9/10 passes. Duplicate Meld titles still refuse direct mapping. Neither component tests nor a generation build prove live text focus or clipboard transfer.
- Verified: scoped Rust formatting and diff check, both no-lock host evaluations, `nix flake check --no-write-lock-file`, both host builds, actual Nix Core tests 132/132 and separate locked/offline AT-SPI tests 16/16. Flake lock unchanged; live semantic-focus acceptance remains pending user return of control.

### 2026-10-10: Diagnose incomplete accessibility reads and recover text focus

- Publish additive bounded AT-SPI traversal counters for the existing incomplete-tree causes; old snapshots retain an unknown/null diagnostic. Counters neither change visibility pruning nor authorize actions. A full depth-40/node-1000 read exposed Code's focused editor, so a shallow incomplete tree was not proof that Electron accessibility was missing.
- The generic desktop skill now requests one bounded full retry where appropriate and requires actual text-buffer focus before typing, paste or Ctrl+A. Keep ordinary-input recovery within the same goal and preserve completed effects; physical Escape remains an explicit stop.
- Verified: pinned scoped rustfmt, parse and diff checks, both no-write-lock host evaluations, full flake check and both host builds. Actual hermetic Core tests passed 133/133; separate locked/offline AT-SPI tests passed 19/19. Flake.lock and Cargo.lock unchanged. The preceding R13 live Code→Thunar run produced exact 234-byte Unicode output after focus/input/save-dialog recovery; its indicator timed out during the final thinking gap. This is an open lifecycle failure, not full acceptance or a live test of this new diagnostic metadata.

### 2026-10-10 (cooperative goal recovery and lightweight indicator lifecycle)

- Ordinary user input may stop a conflicting batch, never the goal. Preserve human edits and partial effects, reobserve/reprove text focus, then continue only the unfinished step with the same task frame; physical Esc and explicit Stop remain aborts. R1 prematurely ended after user text; R2 preserved that buffer and saved a separate exact GUI baseline but Root missed GO, so neither proves active cancellation.
- Replace the frame helper's synchronous full desktop_status probe with a versioned small read-only lifecycle reply and one nonblocking probe in its selector. End/owner events remain reachable; only a timeout permits one bounded retry, no heartbeat renews while authority is unknown. Other contracts fail closed; guards/input/model behavior stay unchanged.
- Verification: pinned rustfmt and diff checks, 25 isolated indicator tests, actual hermetic Nix Core135/135 tests, no-write-lock laptop/desktop drvPath evaluations, full flake check and both host builds passed; locks unchanged. Signed integration, protected activation and live frame/ordinary-input/active-Stop qualification follow; component tests are not GUI acceptance.

### 2026-10-10 (sample exact AT-SPI text selection without inferring content identity)

- Add opt-in direct text-selection metadata for one exact focused/showing/enabled, unprotected text object. Use the provider Unicode character count and bounded repeated count/range/state reads; require one stable nonempty full range, and explicitly report text identity as unverified. Preserve raw text/LF, default response shape and all input/cancel/focus/visibility guards. This sampled readback cannot prove atomicity or saved bytes.
- Verified frozen source patches independently, pinned scoped rustfmt and diff check, no-write-lock laptop drvPath evaluation, full flake check and full affected laptop build; actual hermetic Core137 tests and private AT-SPI27 tests passed. Locks unchanged. Laptop candidate mnjrd9apmz6b0769gdzwlzhh3wly3l6d; source integration and activation/live Code+Meld selection qualification remain separate.
- Original R15 Task31 after-Yield T3 cancellation failed despite a timely task_cancel ACK; a separate exact-task Safety fallback released the actor before Canary input. Ordinary CLI app-flow r1 paused before Copy/Paste and never began Task32. Preserve these failures; installation/build/Exit0 and unrelated old matrix passes are not current GUI acceptance.

### 2026-10-10: Bounded local recovery and automated Proton Drive backups

- Pruned unbounded old Home snapshots while preserving three recent points; storage fell from 94% to 77%. Keep running/booted Nix generations plus four newest, protect updater transactions, and collect only unreferenced store objects with a 40 GiB weekly limit. Preserve project caches and GC roots.
- Added official Proton Drive CLI 0.9.0 plus a loopback authenticated Restic transport, daily personal/system-state backup, weekly verification/retention, explicit verified archive eviction, and recovery runbook/interactive strategy. Preserve the vendor Bun executable bytes: autoPatchelf breaks its embedded payload. Root-state backup reads a temporary readonly Btrfs snapshot directly, avoiding a huge archive copy. Proton trash lookup requires unique decrypted name plus exact UID; journal and retry interrupted deletions safely.
- Verified scoped syntax, affected-host no-lock evaluations, full flake evaluation and laptop/desktop builds; real Restic roundtrip/access-control/retention tests; actual encrypted cloud restore with bytes/symlink and root ownership/mode checks; actual archive upload/download/hash/eviction with generated test data. Full initial production backup is a separate ongoing job, not yet a restore-qualified complete backup. Pass CLI browser login restored independently from Drive.

- 2026-10-10: Source17 prepares task-scoped Codex callback/admission cancellation, bounded Codex build profile, opt-in compact MCP window inventory and explicit exact-ID window close/reuse guidance. Private Core Close suite 149/149 and replay bindings reviewed; new close remains live-unqualified. Fast-forwarded own dirty branch onto storage Main a431b5d with all eight source hashes preserved. Current checks: cargo fmt --check, Nix parse, git diff --check; final both-host evaluations/builds remain pending. Original large build guard died after tool interruption; attached a persistent identity-bound user monitor to the same Nix request, not a duplicate build. Preserve unknown original exit, current GUI pause/physical Esc, and protected single activation. New Jarvis product repo bootstrapped/signature verified and privately pushed using existing HTTPS auth; platform packaging/runtime unchanged.

- 2026-10-10: Final merged Source17 code passed scoped formatting/parse/diff checks, both affected-host no-lock drvPath evaluations and full `nix flake check --no-write-lock-file --max-jobs 1 --cores 2` (137.93s). Source hashes remain frozen; the full host build and live cancellation/reconnect qualification are still pending. The `programs.codex.package` 0.92 receipt is unrelated: bind the selected `home.packages` scoped derivation explicitly. Security review found no language-override input in 100 nearby Root items, but a separate Context7 credential-shaped literal in a video-worker process-argv result; keep resource probes to pid/ppid/comm/rss without args/env, and never copy raw credentials into research. User lifted the incident hold; GUI/mic pause remains.

- 2026-10-10: Source17 signed 8b6a1bde passed fresh laptop/desktop builds (198.041s/28.017s), exact-source flake check, pinned rustfmt and 149 locked/offline Core tests. Protected Procedure8 activated mavpi with readonly Home snapshot21 and passed immutable postreview; owned daily scheduler/PATH restored. Physical Escape bytes survived the daemon restart; existing T3/Codex provider was preserved, so fresh-client active cancellation and GUI close/reuse remain unqualified during the user’s background-only pause. Source-only Windows ZIP was received, hashed and selectively reviewed separately; native import gates remain closed. Preserve unknown original build exit and corrected formatter/read-check failures rather than infer acceptance.


### 2026-10-10 (preserve runtime T3 MIME association)

- Preserve the exact runtime MIME delta: MT remains the default t3code handler; Added Associations retain both mtcode.desktop and com.t3tools.T3Code.desktop. DesktopLinuxUrlHandler.register separately writes the user desktop entry and invokes xdg-mime default on Linux; the MT OAuth guard does not cover that startup path. Code and timing support a runtime registration write, without a historical syscall-writer audit. Preserve the active symlink, inode and bytes; this preparation changes only the isolated worktree and requires explicit later integration.
- Verification: offline no-write-lock evaluations with IFD disabled and max-jobs=0. Desktop drvPath equals the stored z64 deriver. Laptop evaluation stops at 4c8zs98-source.drv despite its output already existing; preserve that failure and do not enable IFD or claim fresh laptop drvPath equality. Stored mavpi/z64 outputs and derivers remain verified; Root-Code8b6 to Main080590b differs only in this log, and this preparation changes no Nix files. No new build or activation.

### 2026-10-10 (Btrfs reserve emergency and project snapshot exclusions)

- User-authorized data-only emergency balance recovered device-unallocated space from 1 MiB to 28 GiB: `-dusage=0` relocated zero chunks; `-dusage=5,limit=4` then three `-dusage=5,limit=8` passes recovered 4/12/20/28 GiB. No file deletion or metadata/profile conversion. Final original guard `ok:true`, no active balance, all device-error counters zero. Update owner interrupted only its exact own Cloud build; its previous Laptop PASS/cache remained preserved. Neither interruption nor preparation is a Cloud PASS.
- Prepared bounded, locked reserve maintenance (15 GiB admission trigger/25 GiB target), separate actual metadata use/reservations, persistent notifications and snapshot lock coordination. Inventoried 24 untracked manifest-recognized project build directories (~12.37 GiB nominal); migrations remain deferred until coordinated update release. Preserve source/Git, active caches, originals on migration failure, Factorio acceptance artifacts and Computer-Use evidence. Root Git checks must run as directory owner and must not treat ownership errors as untracked files.
- Added only two approved visible local launcher IDs; original hidden runtime T3/MT desktop handlers and MIME stayed untouched. Validator needed the installed Store path and `env -u LD_LIBRARY_PATH`. First pre-attempt original-identity capture was not persisted; final completion receipt records that qualification. GUI/input remained paused. Pinned Home Manager implements `xdg.desktopEntries` through profile packages; manage the two local launcher files explicitly with `xdg.dataFile` text plus force, not a source-less force-only definition.
- Verification: seven safety tests (`python3 -B tests/test-btrfs-reserve.py`), Python AST, changed Nix parsing/nixfmt, `git diff --check`, no-write-lock laptop/desktop drvPath evaluations, `nix flake check --no-build --no-write-lock-file`; actual serial `nix build --no-write-lock-file --max-jobs 1 --cores 8` for both hosts. Final r2 actual exits 0: Laptop 47.79s ds466/z64as, Desktop 20.10s z64/mksdll. Built helper CLI/import and exact launcher bytes/syntax passed. Private `/tmp/weasel-storage-validation-20261010-r2/receipt.json` binds actual source diff SHA. Reserve stayed 28 GiB, no build interrupt; serial reservation explicitly released. Main integration, new service activation and actual filesystem migration are still deferred.
### 2026-10-11 (preserve T3 restart default-handler bytes)

- Preserve the observed one-line runtime change: Default Applications now selects com.t3tools.T3Code.desktop for t3code; Added Associations still contains both MT and T3. The desktop-entry/MIME timestamps match the reported T3 restart; no syscall-writer audit is claimed. Linux DesktopLinuxUrlHandler.register invokes xdg-mime default separately from the MT OAuth guard. The live out-of-store target must retain its exact bytes, inode, mtime and symlink chain through index-only staging and a normal fast-forward.
- Verification: git diff --check; both-host offline no-write-lock drvPath evaluations with IFD disabled and max-jobs=0. Desktop returns the existing mksdll1 deriver; laptop preserves the existing 4c8zs98-source.drv IFD-disabled failure, with no IFD override or fresh laptop PASS. Only MIME bytes and this log change; no Nix source, lock, build, activation or app restart.

### 2026-10-11 (preserve MT Code URI registration)
- Change: Preserve the observed MT Code t3code URI association in a focused signed metadata commit before the daily update baseline is sealed.
- Pitfall/Root cause: The out-of-store MIME target is tracked in Main; later app registration can dirty it again. The writer was not proven. Preserve live bytes rather than restore another app association.
- Verification: Exact one-line MIME diff, unchanged Nix blobs, git diff --check, direct-child signature and clean Main/origin equality; no app launch, build or activation.

- 2026-10-10: Vorbereiteter, nicht installierter Bootstrap für die eng gebundene 25.11→26.05-Auth-Ausnahme und unabhängige SSH-/NVML-Prüfungen; Aktivierung ausstehend. Ein gleicher drvPath beweist weder dieselbe Paketausgabe noch denselben Executor. Der kleine SSH-Build muss vor IFD-freier Provenienz-Evaluation liegen. Private Invarianten- und Artefakt-Fixtures wurden ausgeführt; Host-Komposition, vollständiger Bootstrap-Build und geschützte Installation brauchen eigene frische Belege. Prüfung: private Varianten von tests/test-update-migration.py und tests/test-update-batch.py sowie gebundene Initrd-Parser-Fixtures; keine GUI-, Mikrofon-, Dienst- oder Factorio-Aktion.

- 2026-10-10: Kalte SSH-Provenienz benötigt vor der IFD-freien Informationsprüfung die explizite Auswertung des vollständigen systemd-Quellpfads. `verify_built_ssh` erzwingt dafür nur dessen `toString` mit `builtins.deepSeq` vor den drei bestehenden SSH-Build-Ausgaben; keine zusätzliche Ausgabe, IFD-Ausweichprüfung oder Gate-Ausnahme. Statische Python-Syntax, privater Git-Tree, Quellenmanifest und NAR geprüft; frische kalte Gegenprobe und Bootstrap-Build bleiben ausstehend.

- 2026-10-11: Nix 2.31 verweigert kontextgebundene IFD-Lesezugriffe auch bei bereits realisierter Ausgabe, wenn IFD deaktiviert ist (Diagnose34fd6826478d00b5bbaede1a54d20985d901154569bc19fef6502fe9a357dc67). Kontextsensitive Provenienz darf nicht durch kontextlose Literalpfade ersetzt werden. Beide geschützten Produktionskontexte verwenden nun die gemeinsame feste Evaluationsliste: offline, max-jobs0, leere Remote-Builder, substitute=false, IFD=true. Diese Flags verhindern Build-Realisierung und Substitution; --offline ist keine Netzwerkisolation und kein pauschaler Fetcher-Netzwerkschutz. Fehlende Cache-Artefakte brechen ohne Fallback ab; Mini-Build, vollständige Quellenprädikate und Aktivierungsgates bleiben erhalten. Private Python-Orchestrierungsproben sind getrennt von noch ausstehenden realen Kontext-/Host-Proben, Bootstrap-Build und Installation zu berichten.

- 2026-10-11: Nutzervorgabe für künftige Update-Lanes: Laptop-Abnahme priorisieren; unabhängige VPS-only-OpenClaw-Reparaturen sollen sie nicht verzögern, Desktop/VPS separat koordinieren. Lange Compiler-Jobs bevorzugt auf vorhandenem Hephaestus als Nix-Remote-Builder/Cache nach SSH-, Ressourcen-, Architektur- und Vertrauensprüfung; Einrichtung bislang nicht behauptet. Snapshot, Aktivierung und Hardwareprüfung bleiben auf dem Zielhost. Die heutigen verpflichtenden Helper-Gates werden dadurch nicht geändert.

## 2026-10-11 — Plain OpenSSH effective-policy keyword contract

The exact official plain OpenSSH 10.2p1 daemon parsed successfully (4291 bytes, SHA256 `3e233d14038b56e848effc147e88956a9bcd5b7362f7f16f8a2bc649f0d6862a`) but the helper incorrectly required `gssapiauthentication`: locked nixpkgs defaults `withKerberos = false`, and upstream only dumps that field under `#ifdef GSSAPI`. Remove only this mandatory-emission requirement; retain full output equality, singleton checks and exact locked-root package/unit/config provenance. Targeted comparator tests cover absent-both success and GSSAPI presence/value/duplicate rejection. The earlier R22 control really observed cold rejection and selected-output realization, then failed this comparison; its overall result remains FAILED. Realized controls must be checked as warm on retry, without claiming another cold trial.

- 2026-10-11: The SSH migration adapter rejected actual customized Main at two
  conjuncts: it required true XAuth despite the preserved typed false option and
  omitted existing client extraConfig/PipeWire PAM loginLimits from independent
  official composition. Read-only generated-field parity found exactly those
  client/PAM differences on all four Main hosts. Preserve typed values, exact
  protected sources/metadata, canonical PAM package and limits artifact hash;
  realize limits.conf as the optional fourth bounded mini-build output. A false
  XAuth option is bound to the same canonical daemon's private /dev/null parse
  and its compiled default remains literal. Changed Python comparator and
  orchestration fixtures: 23 PASS; fresh 59-case Nix matrix and real full policy
  remain separate required observations. No Main, historical candidate, system
  activation, GUI or credentials changed by this private preparation.

- 2026-10-11: The canonical sshd /dev/null parse omits its unconfigured SFTP
  subsystem; using the configured-output comparator there incorrectly required
  it. Added a separate strict defaults validator requiring the same 44 keys
  except subsystem, retaining every observed field/digest and scalar/shape
  checks. Configured output still requires all 44. Binding expressions and all
  prior Nix provenance predicates are byte/AST unchanged; actual canonical
  defaults digest 699c3141 validates offline. Python-only default/comparator
  regression and orchestration subset: 29 PASS. No Nix run or activation here.


### 2026-10-11 — Bind OpenSSH cross-release defaults without widening authored policy

- Actual immutable OpenSSH 10.2p1/10.5p1 parse captures exposed MixedCase keyword rendering, empty RevokedKeys-array omission, canonical libexec paths, five changed algorithm/penalty defaults, and numeric generated PAM-order comments. Normalize ASCII keyword names only; retain values, duplicate rejection and complete configured-field comparison. Ordinary batches gain only canonical rendering/path identity support; semantic five-field adaptation is limited to the separately approved exact 25.11→26.05 migration, exact daemon versions/defaults, and both configured values matching their own fresh canonical defaults. Configured host-key policy and arbitrary/authored mutations remain strict.
- The PAM fingerprint projects only anchored numeric generated comments, preserving ordered rules/arguments and exact independently composed source/package provenance. Exact emitted Nix projection passed pure builtin-only checks: comment-only deltas equal; argument and rule-order mutations differ. Python targeted migration suites passed 47/47; exact saved historical configured/default output replay passed, while ordinary semantic transition rejected. These source/pure/diagnostic proofs do not replace the pending fresh protected full-policy gate, build, separate Bootstrap approval or activation.

### 2026-10-11: normal NixOS sudo wrapper and final protected Bootstrap binding

Root independently observed NoNewPrivs 0 and /run/wrappers/bin/sudo -n id -u returning 0. The normal wrapper is setuid root mode 4511; /run/current-system/sw/bin/sudo is plain mode 555. Keep per-call privilege checks, exact source/receipt bindings and separate approval for privileged updater installation. The final private source seal adds factual documentation and keeps Main and the historical candidate unchanged; metadata tree/NAR/patch checks do not prove a system build, installed helper, activation, authentication, boot or hardware behavior.


### 2026-10-11 — Validate official SSH-vsock units as exact canonical drop-ins

- Actual26.05 SSH mini-build metadata exposed two old Python assumptions: the three-name allowlist omitted official sshd-vsock.socket/sshd-vsock@.service, and selected-tree validation expected every canonical generated unit file as a base symlink. Official systemd.nix instead selects these two as <name>.d/overrides.conf. Added only these two exact names/representations. Drop-in directories must be real directories containing only overrides.conf, which must symlink to the exact independently composed canonical unitRoot/name; file hash and all previous source/package/unit/provenance checks remain strict. Unknown units, extra conf files, base shadows, wrong targets, copied regular overrides and directory aliases are rejected.
- Replayed exact actual012 public build metadata and both immutable mini/full selected-file graphs; no Nix host evaluation/build, daemon or service run. Existing binding expression/emitter is byte-identical. Complete migration fixture-suite result is recorded privately; fresh full protected policy still required.


### 2026-10-11 — Preserve authored SSH client configuration across canonical package updates

- Exact actual protected-field hashes matched complete144-byte old/new SSH client extraConfig slices. Only the official libvirt Include package path differed; authored Host*/AddKeysToAgent text was identical. Added a lazy fingerprint-only adapter for complete official libvirt Include and GnuPG Match stanzas, byte-bound to both locked upstream modules. Used package/output identities and GnuPG runtimeShell/Bash identity are independently checked; exactly one generated stanza is required. Feature flags, remainder, ordering and arguments remain protected; duplicate/mutated stanzas, marker impersonation, package/coercion spoofs and authored changes reject. Unused off-host packages are not forced.
- Existing raw authentication fingerprint and entire forced SSH-binding prefix remain unchanged. Targeted pure/synthetic Nix fixtures cover laptop GPG-off, desktop GPG-on, package paths, flags, counts and adversarial mutations. Exact saved-artifact replay is diagnostic; fresh all-host policy/build/activation remain mandatory and pending.

### 2026-10-11 — Context-safe exact generated SSH client stanza count
- R28's fresh protected policy eval rejected a regex pattern carrying a real Nix output context. `lib.splitString` escapes its delimiter into a regex; exact canonical package stanzas retain output context. Replaced only `clientCount` with the byte-length removed by `builtins.replaceStrings`, divided by the nonempty complete stanza length. No context discard, regex, provenance relaxation or wider normalization.
- Verified the exact old regex failure and new count/projection using `builtins.appendContext` for libvirt, GnuPG and runtimeShell registered derivation outputs. Contextual fixtures use the explicit read-only local store; baseline fixtures keep their private store. All 14 targeted pure-Nix client tests pass; duplicate/argument changes still reject. The forced SSH binding prefix is unchanged. This is private prepared source, not installed or a fresh four-host policy/build PASS.

### 2026-10-11 — Exact official WPA account and fwupd refresh migration defaults
- Actual paired Desktop diagnostics found only a new `wpa_supplicant` account/AuthData entry and eight appended Polkit lines. Every existing account field and the old 51-line policy prefix were unchanged. The eight line hashes exactly reproduce locked 26.05 `services/hardware/fwupd.nix`'s two-action rule for `fwupd-refresh`; WPA has no Polkit rule.
- Prepared a finite migration-only adapter using independent locked module composition, canonical account/group names, empty credentials/SSH/group grants, exact shadow package plus `/bin/nologin` shellPath, package/output and selected/vendor unit provenance. WPA old `userControlled.{enable,group}` differs in type from new bool: only false plus the exact old dormant wheel default is admitted; active user control or disabled new hardening fails. Both releases already create a static system `fwupd-refresh` account; no DynamicUser assumption is made. All remaining accounts, groups, AuthData and authored policy stay exact.
- Final targeted nine pure-Nix fixtures pass; complete migration suite 148/148 passes. Fixtures replace only independent module composition with explicit synthetic canonical configs; they are not real host policy evidence. Full fresh four-host gates, bootstrap build and separate installation approval remain pending Root. Original TRANSITION/SSH constants, complete SSH binding fragment and ordinary-batch factory are unchanged. No Main/historical SOURCE, daemon, runtime or activation changes.

### 2026-10-11 — Version-specific WPA automatic-interface option
- Actual R30 full-policy command019 failed before normalization because locked 25.11 has no `networking.wireless.autoDetectInterfaces` option; locked 26.05 introduces a boolean with official default `true`. The old adapter and synthetic fixtures incorrectly treated it as a shared field. The failed run remains FAILED; its first 18 successful SSH commands do not establish a full policy pass.
- Removed this option from shared fingerprints and both independent-module seeds. Old-side code never reads it. New-side code always binds its exact boolean value to the independently composed, unseeded locked module default, including when wireless is disabled; nondefault, missing and malformed values fail. Existing wireless flags, canonical account/group/credential/package/unit guards, exact fwupd tail rule, all other accounts and ordinary-batch policy remain unchanged.
- Final targeted pure fixtures and complete migration suite are recorded in the R32 proposal receipt. They prove this narrow adapter and the absent-old/default-new regression; actual Desktop old/new normalized-expression preflight, fresh full four-host policy, bootstrap build and separate installation approval remain Root-owned and pending. No Main, historical SOURCE, host evaluation, daemon or activation changes.

### 2026-10-11 — Independent locale composition for canonical service units
- Actual old Desktop preflight still failed R32's release predicate. Root's exact unit diagnostic proved only one generated line differed: `LOCALE_ARCHIVE`; actual supported locales included German, while the independent composer had only the default C/en_US list. Every other fwupd unit field, vendor base byte, account/group and package proof matched. The failed real composition remains FAILED.
- Added the typed `i18n.supportedLocales` list to the compared release flags and independent official module seed. The actual locale archive must match the independently generated clean package in derivation/output/coercion identity, and the entire global service environment must equal that composition. No raw caller environment, archive, unit text or drop-in is seeded. Complete service text/path/metadata, vendor, account and all preceding SSH proofs remain exact; ordinary batches and original constants are unchanged.
- Targeted pure fixtures and the full migration suite are recorded in the R33 proposal receipt; they cover default/German lists, list/member shape, archive/output/coercion spoofing and environment changes. Real old/new Desktop preflight and fresh formal policy/build/install evidence remain Root-owned and pending. No Main, historical SOURCE, host evaluation, daemon or activation changes.

### 2026-10-11 — Coupled official NetworkManager/WPA migration defaults
- Actual new Desktop diagnostics isolated the remaining R33 failure: the locked 26.05 NetworkManager module enables WPA, DBus control and `autoDetectInterfaces=false` when it fully manages wireless using the WPA backend. Locked 25.11 lacks that block and leaves WPA disabled. The earlier isolated-WPA-default assumption was incomplete; R33 real preflight remains FAILED.
- The finite migration composer now receives typed NetworkManager enable/unmanaged/backend selectors with a clean package. Only enabled NM with empty unmanaged list, WPA backend and empty WPA networks admits independently generated old/new wireless enable/DBus/autodetect differences. It never seeds those observed derived flags. Selectors, interfaces and locales remain compared; outside that mode raw wireless flags remain exact. Both official NM module hashes join the already finite source contract. Full WPA account/credential/group/package/unit, locale/environment and fwupd provider/vendor/unit/rule guards remain intact. Original R24 constants, SSH fragment and ordinary batches are unchanged.
- Final targeted fixtures and full migration suite are recorded in the R34 receipt. Initial selector tests incorrectly required every valid selector change to throw; such changes must instead remain unequal in the cross-side fingerprint. The initial failure log is retained; the corrected comparison assertions preserve strictness and production bytes were unchanged. Actual both-side Desktop preflight, any controlled cache warming, fresh all-host policy and exact bootstrap build remain Root-owned. No Main, historical SOURCE, host evaluation, daemon or activation changes.

- 2026-10-11: Private bootstrap R35 fixes a cold release-migration prerequisite: deepSeq of the complete system-unit path evaluated provenance but did not build the aggregate output needed by the fwupd vendor read. Only the typed migration now realizes and requires that exact output in the existing max-jobs=1/cores=2 build; ordinary batches and all policy predicates remain unchanged. Actual prior R34 trace passed SSH/NM/WPA/locale and reached a cold fwupd vendor read; its failure remains historical. Mocked orchestration and Python migration fixtures do not prove host builds or activation; fresh four-host gates remain required. Targeted FreshEffectiveSSHTests: 29 PASS/0 skipped; full migration suite: 161 PASS/0 skipped. Only verify_built_ssh AST changed against R34; no host evaluation, daemon, system build, signing or activation performed by this proposal.


### 2026-10-11 — Root-Quellzugriff und vollständige Kandidatenbelege
- Der erste breite 26.05-Kandidat bestand die geschützte Vorbereitung; die Root-Prüfung stoppte vor Integration, weil Dienst-Umask 0077 das neue Quellnamespace trotz mkdir-Modus privat machte. Das frische Root-Namespace wird ausdrücklich 0711 gesetzt; Quellbäume bleiben 0555/0444, Journale und Worker privat.
- Vollständige Closure- und Quellenbelege überschritten mit Einrückung den unveränderten 1-MiB-Leser. Alle Kandidatenphasen einschließlich echtem Prune-Abschluss/Unterbrechung verwenden verlustfreies kompaktes JSON; die Submit-Ausgabe wird vor Inbox-Publikation begrenzt.
- Verifikation: Vorbereitungssuite 39 Tests, Aktivierungssuite 40 Tests plus separater Root-Test mit fremder UID; fünf negative Kontrollen. Bootstrap zusätzlich mit vier Host-Auswertungen, vollständigem Flake-Check und tatsächlichem Laptop-Systembuild geprüft. Der alte signierte Paketkandidat und der terminale failed-before-integration-Beleg bleiben erhalten; neue Main-Basis verlangt frische Vorbereitung.


### 2026-10-11 — Niri-Gate unter durchsuchbaren Root-Parents
- Der zweite 26.05-Kandidat bestand die geschützte Vorbereitung sowie Root-Migrations-/Flake-/T3-/Codex-/ChatGPT-Prüfungen. Danach scheiterte Niris Parent-FD-Öffnung an `O_RDONLY` auf `/var/lib/weasel-updates` (`0711`), vor Fixture-Erstellung, Snapshot, Integration oder Aktivierung.
- Nur Parent-FDs wechseln auf fail-closed Linux `O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`; endgültiges Source-/Datei-Lesen, Grenzen, Identität und private `0700`-Namespaces bleiben unverändert. Ein eigener `0711`-Test übersähe den Fehler, weil der Owner lesen darf; die normale Regression nutzt deshalb `0111`.
- Verifikation: normale Niri-Suite 16 PASS/1 Root-Skip; unveränderliche Root-Testquelle mit tatsächlichem UID/GID-65534-Kind 1 PASS, private700/Parent-Symlink/Dateigrenze abgewiesen. Alter signierter Kandidat und terminaler Root-Fehler bleiben erhalten; eine neue Bootstrap-Main-Basis verlangt erneute Vorbereitung.

### 2026-10-11 — release-migration candidate 20261011T063541Z-06f9eccc

- Updated existing configured sources: flake.lock, flake.nix, hosts/nixy-laptop/config.nix, modules/apple-silicon-support/packages/asahi-fwextract/default.nix, modules/nixos/roles/openclaw.nix, packages/chatgpt/default.nix, packages/codex-acp.nix, packages/cua-driver-bin.nix, packages/helium/flake.nix, packages/mtcode/default.nix, packages/netbird-bin.nix, packages/netbird-ui-bin.nix, packages/t3code/source.json, packages/tuios-bin.nix, packages/vesktop-bin.nix, packages/wispr-flow/default.nix, profiles/home/laptop.nix, profiles/system/base.nix, profiles/system/laptop.nix.
- Required before submission: all host evaluations, flake checks, the complete laptop build, changed critical app probes, closure inventory and session preservation. Build, activation and background completion require their own receipts.

### 2026-10-13 — scoped Escape follow-up review corrections

- Explicit takeover now outranks physical Escape. Action admission binds to the one live indicator task ID, permits multiple batches in that same scope, and tombstones action-only IDs. Resume warning publication and ID reservation serialize with cancellation. Indicator begin/end/status now bind session, epoch and owner token; cleanup is attempted even when begin ACK is lost. Corrected test fixtures to mutate `Arc<State>` before sharing.
- Validation: offline locked Cargo core suite passed 169/169 with the cached gcc wrapper and scoped review target; indicator suite passed 27/27; Rust syntax parse, `git diff --check`, and required Laptop drvPath evaluation passed (`/nix/store/ibm3yrl1y9yj0zx0si3jcixn4d8ih2b2-nixos-system-nixy-laptop-26.05.20261012.7993f34.drv`). No runtime deployment or GUI acceptance.

### 2026-10-13 — scope physical Escape to active Computer Use

- Physical Escape outside queued/preparing/running work or the exact task-indicator scope now only advances input generation; in-scope Escape cancels by epoch, clears that old scope, releases through the actor cleanup path, and preserves its restart-persistent cause. The revision-2 indicator scope stays live across model gaps and has a fixed nonrenewing 20-minute bound.
- Re-arming `physical_escape` takes a fresh task ID and current session/epoch binding. First resume returns a visible two-second warning; only a second matching call after the delay commits and binds the same indicator scope. Escape, newer cancellation, stale binding or warning expiry leaves the stop latched; no task/queue replay. Explicit takeover and legacy markers retain their prior resume policy.
- Validation: indicator socket/lifecycle suite 26/26; Rust source parsed with `rustfmt --emit stdout`; Laptop drvPath evaluation passed. Rust Cargo tests could not link because `cc` is absent; broad devShell acquisition was stopped after it began fetching unrelated packages. No live daemon, service, marker, GUI or system activation touched.

### 2026-10-11 — Storage auf abgeschlossener 26.05-Basis

- Ursprünglichen signierten Storage-Checkpoint 53d01c24 erhalten und Main02b5d10 bewusst in den eigenen Branch zusammengeführt; Konflikt im append-only Log unter Erhalt beider Seiten aufgelöst. Wartungstrigger auf 40 GiB und Ziel auf 50 GiB erhöht. Alle alten Run-/Bootstrap-/Manual-Journale blockieren offene Recovery unabhängig vom letzten öffentlichen Status; neun Sicherheitsprüfungen bestanden, tatsächlich vorhandene Root-Journale ohne offene Sperre gelesen.
- Frische Auswertung beider Hosts und vollständiger Flake-Check PASS; tatsächlicher Laptop26-Build Exit0/14.27s, Closure93qir7. Desktop enthielt unerwarteten NVIDIA-Kernelmodul-Build und wurde mit genau einem eigenen pidfd-SIGINT beendet: Exit1/67.66s, kein PASS. Kein Storage-Modul ist im Desktop importiert; dessen drvPath r83zjd stimmt exakt mit signierter Main02b5 überein. Keine weitere Desktop-Kompilierung erforderlich.
- Noch keine neue Storage-Aktivierung oder Projektmigration. Laufzeitänderungen im Main-Checkout und GUI-/Mikrofonpause erhalten. Build-Beleg: /tmp/weasel-storage-validation-20261011-26-r1/receipt.json. Weitere Quellenanpassung braucht frischen Laptop-Nachweis.

### 2026-10-11 — Laufzeitdateien für Storage erhalten

- Nach abgeschlossenem Release-Update waren sieben Main-Dateien durch App-Laufzeitregistrierung/DMS/Matugen verändert; T3 zeigte keine weitere aktive Quellarbeit. Beobachtete MIME-Zuordnung und sämtliche DMS-/CSS-Bytes privat mit Hash/Inode/mtime bewahrt und in den eigenen Branch übernommen, ohne Live-Dateien zu schreiben oder Apps neu zu starten. Konkreter Prozessautor bleibt unbewiesen.
- Finale 26-r2-Prüfung: vollständiger Flake-Check Exit0/39.49s, Laptop Exit0/16.63s (93qir7), isolierter Niri-Parser ohne GUI Exit0/3.32s. Desktop drvPath weiterhin exakt unverändert r83zjd. Neun Storage-Sicherheitsprüfungen bestanden. Beleg /tmp/weasel-storage-validation-20261011-26-r2/receipt.json; Reservierung freigegeben. Noch keine Projektmigration.
- Automatische Freigabeprüfung wies den kombinierten Main-Fast-forward wegen fehlender eindeutiger aktueller Freigabe ab. Keine Umgehung; ausschließlich eigener Aufgabenbranch-Checkpoint, Main/Live-System unverändert.
### 2026-10-11 — Erhaltene App-Laufzeitdateien vor Storage-Integration

- Sieben aktuelle MIME-/DMS-/Matugen-Dateien unverändert als fokussierten signierten Metadatenstand erhalten; index-only Staging bewahrt Live-Bytes/Inodes/mtime. Keine fremde aktive Quellarbeit in T3 beobachtet. Daten waren bereits im Storage-Branch identisch und durch Laptop-Build sowie isolierten Niri-Parser geprüft. Kein App-Neustart, keine GUI-/Mikrofonaktion.

### 2026-10-11 — Gleichzeitigen Start der Storage-Oneshots sauber überspringen

- Erste Native-Aktivierung setzte current/profile auf den getesteten Storage-Laptop, meldete jedoch Exit4, weil Messung/Wartung gleichzeitig anliefen und der belegte eigene Lock als Fehler behandelt wurde. Kein Btrfs-Eingriff, kein Rollback nötig. Busy operation.lock ist jetzt ein sauberer Exit0/Deferred ohne Mess-/Schreib-/Balance-Aktion; Regression gezielt ergänzt. Der native Fehler bleibt im privaten Aktivierungsbeleg erhalten.
- Systemdienst-PATH muss explizit gesetzt werden. Direkte store-path-Aktivierung benötigt beim vorhandenen nixos-rebuild --no-reexec, um keinen unbeabsichtigten Tool-Rebuild via nicht vorhandenem NIX_PATH zu versuchen. Snapshot27 wurde einmal erstellt und bei Korrekturen wiederverwendet. Kein Root-Git-safe.directory-Workaround.

- 2026-10-11: Storage26 tatsächlich aktiviert (0qrb, eigener Root-r5 Exit0); alle24 idle Cache-Verzeichnisse pfaderhaltend migriert, Rsync-Integrität zweimal pro Pfad und echte nichtrekursive Snapshot-Ausschlüsse bewiesen. Eigene Testsnapshots28/29 gezielt entfernt; Recovery26/27 erhalten. Alle7 Runtime-Dateien bytes/inodegleich (Farb-mtimes aktualisiert). Fokuschecks wiederverwenden; keine weitere Prüfung für diesen Docs-only-Abschluss.

- 2026-10-11: Home reorganized under source categories; T3 Jarvis/Factorio bindings updated, canonical updater checkout kept physical. Old remote-backed repos removed, unpublished history preserved in verified bundles, VPN profiles kept byte/inode/mtime identical. Root backup staging snapshot pinned GC-deleted store and orphan-VM blocks; change to on-demand creation/finally cleanup and remove idle timer. Six lifecycle/privacy regressions PASS; Laptop drv evaluation and targeted full build PASS (wb9zx); Desktop unaffected. Existing retention preserves active/booted systems. Detailed personal cleanup receipts remain private outside Git.

### 2026-10-11 — batch candidate 20261011T100131Z-81ef72e4

- Updated existing configured sources: flake.lock, modules/displaylink.nix, packages/t3code/source.json.
- Required before submission: all host evaluations, flake checks, the complete laptop build, changed critical app probes, closure inventory and session preservation. Build, activation and background completion require their own receipts.

### 2026-10-11 — Daily-Batch aktiviert; Root-PATH und Laufzeitkosten belegen

- Batch `20261011T100131Z-81ef72e4`, signierter Code-Commit `df56d2f21fa1141f28bc2ffb33184b282a686e42`: 26.05-Kanal und SOPS aktualisiert, reguläres T3-Nightly 2967 und DisplayLink 6.4.0-22 übernommen. Registryprüfung 63 Einträge/58 Nicht-root-Lock-Nodes; gezielte OpenClaw-API- und Millennium/nixos-hardware-Provenance-Holds bewahren übrige Updates. Codex-Source-Bundle bleibt unverändert; 0.162.1-Patches apply --fuzz=0 ist kein Source-Build-/Cancel-Akzeptanzbeleg. MT 0.1.26 benötigt erlaubten Source-Adapter. Offene Gründe erneut 2026-10-12 prüfen.
- Vorbereitung und unabhängiger Root bestanden Flake-Check, vier geschützte Host-Evaluationen, vollständigen Laptop-Build, Closure/Niri und frische isolierte T3-Proben; Codex/ACP/ChatGPT nur bei exakt unverändertem immutable Output wiederverwendet. Aktiviert `09bjyxzfwn4bpjcrpcz5snqdkb45qj1r`, Home-Snapshot31 readonly. Kein GUI-/Mikrofon-/App-Neustart; laufender alter DisplayLink-Daemon bleibt absichtlich erhalten, Hardware-/Firmwareverhalten ungetestet.
- Erste Root-Aktivierung rollte zurück: Wrapper-PATH enthält glibc-bin, aber nicht den separaten glibc-getent-Output. Exakte installierte Runner-Auflösung reproduziert vor DNS eine ActivationError; kein Flathub-Fehler belegt. Beleg privat `/tmp/weasel-daily-81ef72e4-root-postswitch-cause.json`. Dauerhafte Helper-Paketkorrektur benötigt getrennte Freigabe; im normalen Batch weder Helper/Gates ändern noch Prüfungen auslassen.
- Vorhandene geschützte Same-Candidate-Recovery mit frisch geprüften Transaktions-, Quellen-/Inode-/Index-, Signatur-, Remote-, Closure-/Deriver- und Snapshot-Bindungen fortgesetzt. Immutable Script unabhängig geprüft; installed activate_exact, kein tolerierter nativer Fehlercode, kein neuer Build. Echte Native-Aktivierung Exit0, Dienste/DNS und normaler Push geprüft, Root-Journal complete 11:56:22Z; ursprüngliche Fehlerspur erhalten. Private Root-Belege unter `/var/lib/weasel-updates/runs/20261011T100131Z-81ef72e4/`.
- Laufzeit nicht mit Compilezeit verwechseln: warmer vollständiger Laptop-Build 72.995s; installierte Policy führt vor dem Flake-Check 53 separate Eval-Aufrufe aus. Keine Reduktion der privilegierten Gates im Batch. GitHub-REST-Stundenbudget für Helper und Root reservieren; öffentliche Git-Refs/raw/Parallel für breite Recherche verwenden. Private Performanceprüfung `/tmp/weasel-daily-81ef72e4-performance-audit.json`. Dieser Abschluss ändert ausschließlich Dokumentation und wiederholt keine Nix-Prüfungen.

## 2026-10-12 · Computer Use indicator with fractional scaling

- DP-5 at scale 1.5 reports Niri logical width 1706 and GDK width 1707. Exact cross-library geometry matching prevented the task indicator from starting.
- Accept only a unique monitor at the exact requested position with at most one logical pixel extent difference. Keep subsequent GDK geometry checks exact against its initial rectangle; the helper still detects every Niri layout change.
- Verified focused Nix package build including observed-geometry regression, 25 indicator lifecycle tests, laptop drvPath evaluation, and live worker ready event plus visible DP-5 indicator. Unrelated mimeapps edits preserved; no system activation performed.

## 2026-10-12 · Hephaestus MT laptop route

- The phone MT import still pointed nixy-laptop at the stopped official T3 environment on port 3774. Its independent active MT environment uses loopback port 3773.
- Added an enabled laptop user SSH tunnel to the remote loopback adapter, reusing the existing pinned Hephaestus root identity. The Hermes login key explicitly denies forwarding; preserve that restriction.
- Imported nixy route now uses the current MT environment and a private scoped session. Other peers are preserved. Verified authenticated private HTTPS and successful non-session-starting voice RPC on both MT backends; S24 microphone/playback and live delegation remain device acceptance work.
- Verification: Nix syntax and laptop drvPath evaluation; evaluated unit command matches enabled runtime unit. No whole-system activation or main MT/T3 restart.


## 2026-10-12 — prepare isolated getent bootstrap and retain MT MIME association

- Separate bootstrap proposal adds only glibc.getent to the updater runtime dependencies; installed glibc.bin does not provide getent, so the fixed Root PATH previously rejected the DNS health probe and rolled back. No verifier, policy, SSH, credentials or gates changed.
- Preserve the live MT-generated t3code protocol association byte-for-byte. Main has two separately signed source changes since the last active closure; ordinary daily candidate creation correctly requires their separately checked baseline installation.
- Proposal validation: nix-instantiate --parse modules/daily-updates.nix; full nix flake check --no-write-lock-file (exit 0); Laptop full-system build (exit 0, 10.63s, akap7f0agqrhsw5vgjddq346y86x6jgn); Desktop drvPath eval (exit 0). Independent review confirmed all nine runfiles byte-identical and all three packaged wrappers resolve getent from its immutable separate output. No Main integration or activation at preparation.


## 2026-10-12 — preserve failed bootstrap and prepare bounded resume

- Approved getent packaging/bootstrap Main751 failed native switch exit4 because a manually enabled MT tunnel wants-symlink was foreign to Home Manager. Root rollback completed, original readonly Snapshot33 and failure journal retained; no update success inferred.
- Preserved the two manual unit originals and adopted byte-checked immutable HM links. All unit section values and running PID453278/Invocation remain identical; no daemon reload or restart during adoption.
- Separate helper proposal adds root-only exact journal-SHA resume for the same immutable signed receipt/closure after complete system rollback. Default recovery/retry fences remain; original journal and Snapshot are archived, all existing gates and a fresh Snapshot run, complete requires native exit0+health+unchanged source. Early resumed failures remain recovery-required.
- Validation: nine existing update/Codex regression groups passed (435 cases, one existing Root-only fixture skipped); focused bootstrap29 cases passed, full Laptop system build exit0, full flakecheck exit0. Independent review found and verified early-failure fence retention and dangling archive rejection. This entry records preparation; deployment and actual recovery remain unperformed.

### 2026-10-12 — batch candidate 20261012T115729Z-571105d5

- Updated existing configured sources: flake.lock, flake.nix, packages/t3code/source.json.
- Required before submission: all host evaluations, flake checks, the complete laptop build, changed critical app probes, closure inventory and session preservation. Build, activation and background completion require their own receipts.

- 2026-10-13: Added closed Jarvis launch/project/file actions inside the shared desktop actor with fresh private profiles, canonical file containment and explicit OS-spawn/uncertain receipts; post-focus/launch capture checks DP-6/eDP-1 before capture. The ordinary MCP action schema has a <5000-byte budget: shorten redundant prose, preserve tagged fields/enums. Focused offline Cargo launch tests passed 9/9 and action_schema_contract_tests 4/4; Root source review approved 000c438. Laptop `nix eval --no-write-lock-file ...config.system.build.toplevel.drvPath` with IFD disabled stopped at the existing Wispr helper source; no host build or runtime acceptance claimed. R65-R70 receipts and errors retained in the launch handoff.


## 2026-10-13 — preserve runtime settings and qualify merged baseline

- Preserve current MIME association and Zed UI-generated settings byte-for-byte in the isolated baseline snapshot. No provider, model, credentials, access or app process was selected or changed by the updater.
- Main includes the merged, already-tested Jarvis desktop-launch increment while current/profile still reproduce the preceding daily batch. Qualify the exact new baseline through the existing protected bootstrap before ordinary candidate preparation; no helper or gate changes.
- Reuse the documented actor159-test and full Laptop build evidence only when the exact current derivation matches. Fresh Laptop evaluation, substitution-aware dry plan and cached realization bind this snapshot; activation needs its own immutable receipt and readonly Home snapshot.

### 2026-10-13 — batch candidate 20261013T103836Z-0a4b72a1

- Updated existing configured sources: flake.lock, flake/modules/shared.nix, lib/mk-host.nix, packages/t3code/source.json, profiles/system/base.nix.
- Required before submission: all host evaluations, flake checks, the complete laptop build, changed critical app probes, closure inventory and session preservation. Build, activation and background completion require their own receipts.

### 2026-10-14 — morrowbyte / Blacksmith CI

- Moved GitHub ownership to morrowbyte and added gated, cached host evaluation plus manually requested full builds. Blacksmith App installation is distinct from its additional account verification and runner acceptance.
- Preserve the canonical checkout's unrelated MIME edits; implement CI in an isolated worktree. Workflow never updates flake.lock or activates a host. Validation is recorded in the task result; a disabled job is not build acceptance.
- Validation: actionlint (both new workflows), git diff --check, nix flake check --no-write-lock-file --no-build and both host drvPath evaluations passed. Substitution-aware dry plans found 13 laptop derivations and 134 desktop derivations; desktop realization deferred to CI to avoid local heavy compilation.

### 2026-10-14 — corrected organization to morrowbite

- Owner created morrowbite under sequelweasel; accepted EvilWeasel membership, transferred all three repositories and updated remotes/release repository. Account creation through the integrated browser is suspended until the fork browser issue is fixed. Docs-only destination correction; git diff --check. Previous verification remains valid because workflow/config code is unchanged.

### 2026-10-14 — Namespace runner migration

- Replaced inactive Blacksmith workflow with Namespace profile ci-builder and native Nix cache volume. User performs onboarding manually; Computer Use remains stopped. actionlint and git diff --check passed. Host configuration and flake.lock unchanged; host evaluations are recorded in task evidence.

## 2026-10-14 — Namespace and declarative fork updates
- Added pinned T3 fork updater recipe, separate launcher/profile and hourly Home Manager timer on both hosts. Updates verify release SHA-256, retain GC roots and switch versions atomically without restarting sessions; rollback remains available.
- Namespace app is on morrowbyte, so repos were transferred back there; EvilWeasel remains main identity. Evaluate host before `flake check --no-build` so import-from-derivation sources are realized on a cold runner.
- Verification: updater five failure/rollback tests + shellcheck; Nix parse, both host drvPath evaluations, laptop system build, locked flake check. Desktop full compilation deliberately remains CI/manual due uncached dependencies.

- Cold CI follow-up: `flake check --no-build` disallows generated sources beyond the chosen host too; CI now uses ordinary locked flake check. Preserve the existing private transcription/account launcher when present.
- Owner policy now persists in the shared Codex guidance: Namespace-only builds/full suites, priority tier, concrete measurable goals and one writer per path. Installed verified updater unit files directly for immediate laptop use; first service run succeeded and timer is active. Full NixOS activation was not needed. Existing private launcher remains intact.

## 2026-10-14 — Distinct fork icon and private profile merge
- Added a native SVG branch icon for the fork and installed it for the existing launcher, preserving its private account/transcription command.
- Privately merged T3/MT connection catalogs using their existing unlocked keyring keys; route identity includes environment plus connection ID/type, not environment alone. Retained all routes/credentials, validated encrypted roundtrip/reference integrity and preserved both source profiles with backups. Merged settings/keybindings with existing fork customizations taking precedence; instance identity/server exposure was not cloned.
- Verification: SVG rendered/viewed, private import receipt and source hashes, Nix parse/evaluation for both hosts; full source builds remain on Namespace.

- Home Manager adopts the existing `t3code-weasel.desktop` ID, so future activation does not add a fourth launcher. The private CLI wrapper remains the credential/settings owner.

- Follow-up evaluation caught an invalid `xdg.dataFile.force` override: `xdg.desktopEntries` installs package entries, so no dataFile source exists. Removed the override; the shared desktop ID itself prevents duplicate launcher entries. The initial evaluation failure is retained.

## 2026-10-14 — Overnight CI storage diagnosis
- Both full Namespace host builds evaluated successfully but exhausted the 50-GB Nix cache during realization (desktop substitution alone: 41.8 GiB unpacked, before temporary/output space). Cache-backed evaluation remains enabled; full builds use the runner filesystem instead. Added a bounded storage-only dispatch to measure capacity before retrying compilation, with no purchased storage or laptop fallback.
- Verification: actionlint and both unchanged host drvPath evaluations; remote probe pending. Original failed run 38484408376 retained.

## 2026-10-15 — Proton Mail Bridge for the laptop
- Added `protonmail-bridge-gui` beside the existing Proton Mail desktop package in the laptop profile. No credentials are part of the flake.
- Verification: nixfmt, Nix parse, diff check and locked laptop toplevel drvPath evaluation succeeded. The exact selected Bridge 3.24.2 closure was already present locally; GUI launch and account login are separate acceptance steps. Full system activation is not implied.

## 2026-10-15 — Headless Proton Mail Bridge
- Added a laptop Home Manager user service using the pinned Bridge executable in noninteractive mode, so remote terminal users need no laptop GUI and Bridge does not depend on the login terminal. The unit uses the existing D-Bus keyring and loopback mail endpoints; no credentials enter Nix or Git.
- Verification: Nix parse, nixfmt, locked laptop toplevel drvPath evaluation. Bootstrap installs only the same evaluated task-owned unit before a future full system activation. Mail authentication and delivery are separate runtime evidence.

## 2026-10-15 — Blacksmith CI migration
- Owner superseded Namespace-only policy for the current Morrowbyte repositories after account verification/billing setup. Nix CI now uses Blacksmith, bounded upstream Actions-backed Nix caching, and a larger-disk runner only for full builds; no host activation or laptop compilation.
- Verification: actionlint and diff check; shared Blacksmith acceptance run 38740107584 passed Linux x64/ARM64, Windows x64 and macOS. Per-repository Nix execution remains separately reported.

- 2026-10-17: Physical Escape cancels the current Computer Use task and queue without persisting a global takeover. Idle Escape only invalidates observations; old task IDs stay closed; explicit Ctrl+Alt+Escape takeover remains persistent. Legacy Escape markers migrate to the ordinary startup gate. Updated the installed skill and explicit local command links; the focused Blacksmith workflow tests and exports the same actor/indicator closure for deployment without a laptop rebuild. Verification uses focused Escape/resume/marker regressions and locked host drvPath evaluations; preserve unrelated dirty MIME edits.

### 2026-10-17 (Morrowbyte URI associations)
- Change: Preserve the existing Morrowbyte default and Slack URI association; remove MT Code from additional t3code associations, keeping the explicit Morrowbyte handler.
- Pitfall/Root cause: A pre-existing uncommitted MIME association change blocked protected daily-update baseline admission. User explicitly approved its commit and removal of MT Code from this file.
- Verification: Python ConfigParser assertions for default/additional handlers and absence of mtcode.desktop; git diff --check. No system build or activation. Nix evaluation deferred because the newly required Btrfs metadata preflight was above its 85% threshold.

## 2026-10-17 — Canonical repository transfer

Migrated the protected updater origin checks, dev-shell default and active setup links to `morrowbyte/weasel-os`. The previous GitHub alias resolves to the same repository ID 933863588; this preserves the trust target and all signing/snapshot/closure gates. Source changes require the normal immutable bootstrap before installed helpers change. Verification: existing preparation/activation/bootstrap tests and affected-host evaluation.

## 2026-10-18 — Computer Use indicator lifetime

- A prototype's 20-minute ceiling was blocking normal long-running tasks. The indicator now follows its actual owner by default; bounded trials can still request a positive explicit runtime.
- Preserve physical Escape, exact-owner cleanup, backend/session and output binding. These are independent of a fixed session duration.
- Verified five focused indicator lifecycle cases: default lifetime/explicit end, explicit expiry, runtime above the former ceiling, Escape epoch and invalid owner. No physical desktop input or system activation in these checks.

## 2026-10-18 — Remove trial monitor names from launch capability
- Removed the DP-6/eDP-1 product whitelist from launch and post-launch observation. Runtime enabled-output geometry, exact focused workspace/window, task scope, cancellation and input collision checks remain. An individual physical trial can still bind its own monitor explicitly.
- This does not generalize the existing Zed/Chrome launch prototype or provide independent input seats; those remain separate implementation work.
- Verification: five focused launch contract tests passed (including runtime DP-5/HDMI-A-2/USB-C-1 targets and unchanged cancellation/stale-target rejection); affected laptop drv evaluation passed. No live action, actor restart or system activation.

## 2026-10-18 — Object-targeted background accessibility
- Added explicit background semantics to separate direct AT-SPI click/edit from shared keyboard/pointer generation checks. Real target/window/output/context and Escape/cancellation remain checked; overlap with the actual focused target still rejects stale input. No focus/key/pointer/clipboard fallback is introduced.
- Ten focused semantic regressions passed, including unrelated held input, target replacement/geometry, same-target collision, existing foreground generation semantics and physical Escape. Live app acceptance remains unrun; activation stays with the sole physical coordinator.

## 2026-10-18 — Keep desktop guidance aligned with end-to-end ownership
- Removed unconditional extra Luna-child dispatch from the desktop skill; reuse the user's selected accountable feature owner and retain one physical writer. Prefer existing app APIs/plugins/MCP/ACP/A2A before shared-input fallback.
- Documented live-schema-gated background AT-SPI use and runtime monitor selection. Older installed actors must not be sent invented fields. This is documentation only; no extra build or physical action was run.

## 2026-10-18 — Bind long-running task scopes to their real owners
- Core still had a fixed 20-minute expiry after the helper had been changed to follow task lifetime. Exact helper and owner PID/start-time binding now replaces that deadline for paired scopes; process death/reuse, end, Escape and epoch invalidation remain terminal. Legacy pre-helper begin can upgrade with its original token, but cannot replace an existing process binding.
- The helper requires an explicit owner-lifetime acknowledgement before READY; old Core/new helper pairs refuse instead of claiming support. No runtime restart or activation during the concurrent Jarvis physical trial.
- Four focused Python helper checks passed; laptop drv evaluation passed. Core compilation/regressions and package qualification run through the existing Blacksmith Computer Use workflow.

## 2026-10-18 — Route the actual T3 development Electron identity
- Real native dictation reached the correct T3 development window but keyboard auto typing lost trailing characters. Its actual app ID `com.t3tools.T3Code.Development` was missing from the existing known-Electron clipboard route.
- Added that exact ID to the shared selector. Explicit keyboard/clipboard choices, other apps, Unicode length thresholds, clipboard payload/restore and Escape guards stay intact. One focused routing regression runs in the normal Blacksmith Core suite; no native replay or activation during the coordinator's cleanup.
