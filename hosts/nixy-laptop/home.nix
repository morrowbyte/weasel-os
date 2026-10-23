{
  inputs,
  lib,
  pkgs,
  pkgsUnstable,
  ...
}:
let
  hermesDesktop = inputs.hermes-agent.packages.${pkgs.stdenv.hostPlatform.system}.desktop;
  codexLatest = pkgsUnstable.callPackage ../../packages/codex-source { };
  codexPathInit = ''
    case ":$PATH:" in
      ":${lib.getBin codexLatest}/bin:"*) ;;
      *) export PATH="${lib.getBin codexLatest}/bin:$PATH" ;;
    esac
  '';
  cuaDriver = pkgs.callPackage ../../packages/cua-driver-bin.nix { };
  laptopExecutor = pkgs.callPackage ../../scripts/weasel-laptop-executor.nix {
    codexPackage = codexLatest;
  };
  netbirdUi = pkgs.callPackage ../../packages/netbird-ui-bin.nix {
    daemonSocket = "unix:///var/run/netbird-personal/sock";
  };
  netbirdUiLauncher = pkgs.writeShellScript "netbird-ui-launcher" ''
    set -eu

    for _ in {1..120}; do
      if [[ -S /var/run/netbird-personal/sock ]]; then
        # 0.77.x can fall back to the default socket despite NB_DAEMON_ADDR.
        exec ${lib.getExe netbirdUi} \
          --daemon-addr=unix:///var/run/netbird-personal/sock
      fi
      ${pkgs.coreutils}/bin/sleep 1
    done

    echo "NetBird daemon socket did not appear within 120 seconds" >&2
    exit 1
  '';
in
{
  weasel.t3Fork.enable = true;
  imports = [
    ../../programs/t3code-fork.nix
    ../../profiles/home/common.nix
    ../../profiles/home/base.nix
    ../../profiles/home/client.nix
    ../../profiles/home/laptop.nix
    ../../programs/hephaestus-recovery-console.nix
    ../../programs/coding-tools.nix
    ../../programs/t3code-client.nix
    ../../programs/computer-use.nix
    ../../programs/ui-decisions.nix
    ../../programs/vps-ssh.nix
    ../../programs/proton-infra-ssh.nix
    ../../programs/proton-mail-bridge.nix
    ../../programs/cloud-backup.nix
    ../../programs/storage-health.nix
  ];

  home.packages = [
    inputs.claude-desktop.packages.${pkgs.stdenv.hostPlatform.system}.default
    hermesDesktop
    cuaDriver
    laptopExecutor
    (import ../../packages/proton-pass-cli.nix { inherit pkgs pkgsUnstable; })
  ];

  # Select verified Codex while keeping the user's other npm commands.
  home.sessionPath = lib.mkBefore [ "${lib.getBin codexLatest}/bin" ];
  # An existing desktop can pass an old, already-sourced HM environment.
  # Login shells also need selection before .bashrc's interactive-only guard.
  programs.bash.profileExtra = lib.mkAfter codexPathInit;
  programs.bash.initExtra = lib.mkAfter codexPathInit;

  home.sessionVariables.PROTON_PASS_LINUX_KEYRING = "dbus";
  weasel.uiDecisions = {
    enable = true;
    manageMcp = true;
    imageRoots = [ "/run/user/1000/weasel-computer-use/images" ];
  };
  home.file.".codex/skills/cartesia-audio".source = ../../skills/cartesia-audio;
  home.file.".codex/skills/proton-pass-cli".source = ../../skills/proton-pass-cli;

  # Disable the package's unordered XDG autostart. It can race the hardened
  # daemon and then remain disconnected until manually restarted.
  xdg.configFile."autostart/netbird.desktop" = {
    force = true;
    text = ''
      [Desktop Entry]
      Type=Application
      Name=NetBird (managed by systemd)
      Hidden=true
      NoDisplay=true
    '';
  };

  # Preserve Home Manager's stale backup outside the autostart directory so
  # systemd-xdg-autostart-generator cannot launch a second, obsolete UI.
  home.activation.archiveLegacyNetbirdAutostart = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    legacy="$HOME/.config/autostart/netbird.desktop.hm-backup"
    archive="$HOME/.local/state/weasel-os/netbird.desktop.hm-backup"
    if [[ -e "$legacy" ]]; then
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p "$HOME/.local/state/weasel-os"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/mv --backup=numbered -T "$legacy" "$archive"
    fi
  '';

  # The UI is a tray process and must start only after the custom daemon socket
  # exists. A clean user-initiated quit remains respected (Restart=on-failure).
  systemd.user.services.netbird-ui = {
    Unit = {
      Description = "NetBird tray for the personal client";
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
    };
    Service = {
      Environment = [ "WEBKIT_DISABLE_DMABUF_RENDERER=1" ];
      ExecStart = netbirdUiLauncher;
      Restart = "on-failure";
      RestartSec = "5s";
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };

  systemd.user.services.cua-driver = {
    Unit = {
      Description = "Cua background computer-use driver";
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
    };
    Service = {
      Type = "simple";
      Environment = [
        "CUA_DRIVER_RS_ENABLE_WAYLAND=1"
        "XDG_SESSION_TYPE=wayland"
        "WAYLAND_DISPLAY=wayland-1"
        "DISPLAY=:0"
      ];
      ExecStartPre = "${cuaDriver}/bin/cua-driver telemetry disable";
      ExecStart = "${cuaDriver}/bin/cua-driver serve --permission-mode standard";
      Restart = "on-failure";
      RestartSec = "3s";
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };

  xdg.desktopEntries.hermes-desktop = {
    name = "Hermes";
    comment = "Hermes Agent desktop application";
    exec = "${hermesDesktop}/bin/hermes-desktop";
    icon = "${hermesDesktop}/share/hermes-desktop/dist/hermes.png";
    terminal = false;
    categories = [
      "Development"
      "Utility"
    ];
    startupNotify = true;
  };

  weasel.hephaestusRecoveryConsole = {
    enable = true;
    package = codexLatest;
  };
}
