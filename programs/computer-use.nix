{
  config,
  lib,
  pkgs,
  ...
}:
let
  cuaDriver = pkgs.callPackage ../packages/cua-driver-bin.nix { };
  computerUse = pkgs.callPackage ../packages/computer-use { cua-driver = cuaDriver; };
  indicator = pkgs.callPackage ../packages/computer-use-indicator { };
in
{
  home.packages = [
    computerUse
    indicator
  ];
  home.file.".codex/skills/niri-computer-use".source = ../skills/niri-computer-use;
  # Keep explicit local commands aligned with the managed actor and its skill.
  home.file.".local/bin/weasel-computer-use".source = "${computerUse}/bin/weasel-computer-use";
  home.file.".local/bin/weasel-computer-use-indicator".source =
    "${indicator}/bin/weasel-computer-use-indicator";
  # This key works independently of a busy or disconnected client.
  xdg.configFile."niri/config.kdl".text = lib.mkAfter ''
    binds {
      Alt+Escape allow-inhibiting=false {
        spawn "${computerUse}/bin/weasel-computer-use" "call" "desktop_cancel" "{}";
      }
      Ctrl+Alt+Escape allow-inhibiting=false {
        spawn "${computerUse}/bin/weasel-computer-use" "call" "desktop_takeover" "{}";
      }
    }
  '';
  home.activation.configureDesktopMcp = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    $DRY_RUN_CMD ${pkgs.python3}/bin/python ${../scripts/configure-computer-use.py} \
      --codex-home ${lib.escapeShellArg "${config.home.homeDirectory}/.codex"} \
      --command ${lib.escapeShellArg "${computerUse}/bin/weasel-computer-use"}
  '';
  systemd.user.services.weasel-computer-use = {
    Unit = {
      Description = "Persistent Niri computer-use actor";
      After = [
        "graphical-session.target"
        "cua-driver.service"
      ];
      PartOf = [ "graphical-session.target" ];
    };
    Service = {
      ExecStart = "${computerUse}/bin/weasel-computer-use serve";
      Restart = "on-failure";
      RestartSec = "1s";
      UMask = "0077";
      Environment = [ "XDG_SESSION_TYPE=wayland" ];
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };
}
