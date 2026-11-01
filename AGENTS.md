# Repository Guidelines

## Project Structure & Module Organization
This repo is a Nix flake composed through `flake-parts` for two NixOS hosts: `nixy-desktop` and `nixy-laptop`.
- `hosts/<host>/`: host entrypoints (`config.nix`, `home.nix`, `hardware.nix`, `users.nix`, `variables.nix`).
- `flake/modules/`: `flake-parts` modules and output wiring.
- `lib/`: host composition helpers used by the flake layer.
- `modules/`: reusable NixOS modules (drivers, certs, hardware support, Apple Silicon overlays).
- `programs/`: Home Manager program configs (Hyprland, Waybar, Neovim, VS Code, etc.).
- `packages/`: custom derivations/flake packages.
- `scripts/`: packaged helper scripts written as Nix derivations.
- `pictures/`, `certs/`: assets and bundled cert material.

## Build, Test, and Development Commands
Run commands from repo root.
- `nix fmt`: format all Nix files (uses `nixfmt-tree`, also run by `.githooks/pre-commit`).
- `nix flake check`: evaluate flake outputs and basic checks.
- `nix build .#certs`: build the custom package exposed by this flake.
- `nix build .#nixosConfigurations.nixy-desktop.config.system.build.toplevel`: validate desktop system build.
- `sudo nixos-rebuild switch --flake .#nixy-desktop` (or `.#nixy-laptop`): apply a host config.

## Local Alias Workflow
Preferred day-to-day rebuild commands are defined in `hosts/nixy-laptop/home.nix` and `hosts/nixy-desktop/home.nix`:
- `fr`: `nh os switch --hostname ${host} /home/${username}/weasel-os` (rebuild current flake state).
- `fu`: `nh os switch --hostname ${host} --update /home/${username}/weasel-os` (update inputs + rebuild).
- `ncg`: run system/user garbage collection, then switch boot configuration.
Use these aliases when available; use the full `nixos-rebuild`/`nh` commands in non-interactive or fresh environments.

## Parallel Agent Work
- Keep the main checkout on `main` as the integration baseline. For concurrent code changes, use a separate branch and worktree per task; in T3 use its native worktree handoff or launch a thread with a worktree workspace.
- Check the thread's current worktree binding before creating another. Read-only reviews may share a checkout; agents must not edit, stage or commit in another task's worktree.
- Before integrating, fetch and compare current `origin/main`, preserve other worktrees, resolve conflicts in the task branch, and validate the resulting host configuration. Push without force and return the main checkout to a clean, synchronized state.
- Coordinate system activation separately from source changes. Builds may run in parallel; only one update or system activation transaction may be in progress. An active or unresolved update blocks another activation.
- Never reset, stash or delete another task's work to make integration succeed. The daily updater must use an isolated candidate and stop if its baseline changes.

## Coding Style & Naming Conventions
- Nix code is formatted with `nixfmt`; do not hand-format around it.
- Use the standard RFC-style layout that `nixfmt` produces.
- Prefer lowercase kebab-case file names (examples: `nvidia-drivers.nix`, `local-hardware-clock.nix`).
- Keep host-specific logic in `hosts/<host>/`; move reusable logic to `modules/` or `programs/`.

## Testing Guidelines
There is no separate unit-test framework in this repo.
- Treat `nix flake check` + target `nix build` as the required validation baseline.
- For host changes, build the affected host output before opening a PR.
- For Home Manager/program edits, verify evaluation through the relevant host build.

## Agent Verification Policy
- Prefer syntax-only verification when possible: `nix-instantiate --parse <changed-file>`.
- For any task that changes repo-tracked code or NixOS/Home Manager configuration, run `nix eval --no-write-lock-file .#nixosConfigurations.<affected-host>.config.system.build.toplevel.drvPath` for each affected host after the edits.
- For docs-only or markdown-only changes, skip the `nix eval` step unless the change also touches code or config that needs rebuild validation.
- Do not use verification commands that modify `flake.lock` unless the task explicitly requires lock updates.

## Commit & Pull Request Guidelines
Git history uses short, imperative, scope-first messages (for example: `added llama-cpp.nix module`).
- Keep commits focused to one area (host/module/program).
- PRs should include: what changed, why, affected host(s), and exact validation commands run.
- Include screenshots only for visible UI changes (Waybar, Rofi, wallpapers, wlogout, etc.).
- After a verified code/config change request, create a focused signed commit and push it to `origin/main` before handing the task back.
- If `git commit` or `git push` fails because the required GPG/SSH key or permissions are unavailable, stop and report the blocker; never create an unsigned commit or work around signing/auth requirements.

## Language Convention
- When communicating in German or writing German text, use normal German with real umlauts and `ß`. Only fall back to ASCII transliterations when the interface strictly requires it.

## Agent Learnings
- Maintain `agent-learnings.md` as an append-only log for future agents.
- After every task that changes files, append a short entry with date, what changed, pitfalls/root cause, and verification command(s) used.
- Keep entries concise and factual.

## Focused iteration and upgrade priority

- Keep ordinary package/system upgrades separate from development of the updater itself. Finish the requested Laptop result before unrelated Desktop/VPS work unless the user requests otherwise.
- Reuse successful evidence for an unchanged source/configuration. Do not repeat broad matrices or full builds after docs-only changes, minor unrelated failures, or an already-qualified package result. Preserve required installed security gates; do not bypass them to save time.
- For a narrow fix, run the relevant regression and affected-host evaluation/build. Build the evaluated drvPath directly when possible to avoid repeating the same evaluation. An unchanged host drvPath needs no new host build.
- Inspect uncached dependencies before a build. Unexpected heavy compilation must be stopped at the owned safe boundary and coordinated; prefer the existing Hephaestus route for long builds after verifying its availability. Do not spend hours on a blind local build.
- A noncritical post-switch application/service failure should be repaired on the already-tested system when safe. Do not turn that into a new broad upgrade, discard the tested candidate, or repeat unrelated checks. Report actual activation and remaining failures separately.
- For data migrations, perform one representative pilot and independent integrity/exclusion proof, then serial focused per-directory checks. Skip active projects; preserve files, sources, saves and evidence.

## Home layout

- Put new source projects under `~/source/<category>/<project>`: `personal`, `gamedev`, `system`, `tools`, or the existing work/learning categories. Keep disposable task builds under `~/.cache/<tool>/<task>` and tool backups under their state directory. Do not create build runs, backup piles or new project directories at Home top level.
- The canonical `~/weasel-os` checkout stays a real directory because installed update/source validators reject symlink ancestors; `~/source/system/weasel-os` links to it. Old compatibility links for other moved projects preserve existing scripts and thread references. New work uses the organized source paths.
- Old locally unnecessary clean repositories may be removed after remote publication is verified; preserve unpublished changes/branches and local-only settings first. Downloadable installers and obsolete duplicate builds are disposable; VPN profiles, credentials, saves and unique personal files are preserved.

## Build location (owner decision 2026-11-01)

For weasel-os, run quick syntax checks and focused lightweight tests on the laptop.
Run longer builds, compilation and full suites on the existing project Boat.dev VM.
Do not use Blacksmith or ask another daily build-location question for this project.
Reuse the existing Boat instance, source checkout and caches; keep its budget and
bounded lifetime, and report a concrete missing build capability instead of silently
moving a long build onto the laptop. This project-specific owner decision supersedes
older daily-location and Blacksmith-only guidance. Live desktop validation remains
on nixy-laptop with one Computer Use owner and explicit Stop boundaries.
