{ pkgs, ... }:
let
  niri = pkgs.callPackage ../packages/computer-use/window-capture/package.nix { };
  driver = pkgs.callPackage ../packages/cua-driver-bin.nix { };
  actor = pkgs.callPackage ../packages/computer-use {
    inherit niri;
    agentFirstLaunch = true;
    cua-driver = driver;
  };
  indicator = pkgs.callPackage ../packages/computer-use-indicator { inherit niri; };
  workspace = pkgs.callPackage ../packages/agent-workspace { };
  desktop = pkgs.callPackage ../packages/agent-desktop { inherit actor indicator; };
  sessionService = {
    partOf = [ "graphical-session.target" ];
    wantedBy = [ "graphical-session.target" ];
    after = [ "graphical-session-pre.target" ];
  };
  stop = pkgs.writeShellScript "weasel-stop-input" ''
    ${workspace}/bin/weasel-workspace cancel-all &
    private_job=$!
    ${actor}/bin/weasel-computer-use call desktop_cancel '{}'
    wait "$private_job"
  '';
in
{
  programs.niri.enable = true;
  programs.niri.package = niri;
  programs.niri.useNautilus = false;
  hardware.graphics.enable = true;
  hardware.uinput.enable = true;

  # logind grants these ACLs to the active local seat. The rule has to run
  # before 73-seat-late.rules for the uaccess builtin to apply.
  services.udev.packages = [
    (pkgs.writeTextFile {
      name = "weasel-seat-access";
      destination = "/etc/udev/rules.d/70-weasel-agent-seat.rules";
      text = ''
        SUBSYSTEM=="input", KERNEL=="event*", TAG+="uaccess"
        SUBSYSTEM=="misc", KERNEL=="uinput", TAG+="uaccess"
      '';
    })
  ];
  services.gnome.at-spi2-core.enable = true;
  environment.systemPackages = [
    actor
    indicator
    desktop
    workspace
    (pkgs.callPackage ../packages/agent-launcher { inherit workspace; })
    pkgs.git
    pkgs.jq
    pkgs.ripgrep
    pkgs.foot
    pkgs.chromium
    pkgs.zed-editor
    pkgs.fuzzel
    pkgs.mousepad
    pkgs.wl-clipboard
    pkgs.python3
    pkgs.xwayland-satellite
  ];
  fonts.packages = [ pkgs.dejavu_fonts ];
  environment.etc."niri/config.kdl".text = builtins.replaceStrings [ "@cancel@" ] [ "${stop}" ] (
    builtins.readFile ./agent-first.kdl
  );
  # Local Codex-backed clients inherit these defaults even with a fresh home.
  environment.etc."codex/config.toml".source = (pkgs.formats.toml { }).generate "weasel-codex.toml" {
    mcp_servers.weasel_os = {
      command = "${desktop}/bin/weasel";
      args = [ "mcp" ];
    };
    mcp_servers.weasel_desktop = {
      command = "${actor}/bin/weasel-computer-use";
      args = [ "mcp" ];
      supports_parallel_tool_calls = true;
    };
  };
  environment.etc."codex/skills/weasel-desktop/SKILL.md".source = ../skills/weasel-desktop/SKILL.md;
  environment.etc."codex/skills/weasel-desktop/SKILL.md".mode = "0444";
  systemd.user.services = {
    weasel-computer-use = sessionService // {
      description = "Weasel desktop actor for the active Niri session";
      path = [
        indicator
        "/run/current-system/sw"
      ];
      after = sessionService.after ++ [ "cua-driver.service" ];
      serviceConfig = {
        ExecStart = "${actor}/bin/weasel-computer-use serve";
        Restart = "on-failure";
        UMask = "0077";
      };
    };
    cua-driver = sessionService // {
      description = "Application accessibility driver for Weasel";
      environment.CUA_DRIVER_RS_ENABLE_WAYLAND = "1";
      serviceConfig = {
        ExecStartPre = "${driver}/bin/cua-driver telemetry disable";
        ExecStart = "${driver}/bin/cua-driver serve --permission-mode standard";
        Restart = "on-failure";
        RestartSec = 3;
        UMask = "0077";
      };
    };
  };
}
