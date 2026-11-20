{
  testers,
  lib,
  pkgs,
  writeShellScript,
  coreutils,
  jq,
}:
let
  # Each title change fences the Niri event stream after the corresponding
  # draft has actually arrived through foot's terminal input.
  draft = writeShellScript "foreground-draft" ''
    set -eu
    for phase in private actor; do
      IFS= read -r line
      printf '%s\n' "$line" > "$HOME/draft-$phase.txt"
      printf '\033]2;draft-%s-complete\007' "$phase"
    done
    exec ${coreutils}/bin/sleep infinity
  '';
in
testers.runNixOSTest {
  name = "agent-first-desktop";

  nodes.machine = { config, ... }: {
    imports = [ ../../modules/agent-first.nix ];
    users.users.operator.isNormalUser = true;
    services.greetd = {
      enable = true;
      settings.default_session.command = "${pkgs.greetd}/bin/agreety --cmd ${pkgs.niri}/bin/niri-session";
      settings.initial_session = {
        user = "operator";
        command = toString (
          import ../../hosts/weasel-agent/vm-session.nix {
            inherit pkgs;
            niri = config.programs.niri.package;
          }
        );
      };
    };
    environment.systemPackages = [ jq ];
    virtualisation = {
      memorySize = 4096;
      cores = 2;
      resolution = {
        x = 1280;
        y = 800;
      };
      qemu.options = [ "-vga none -device virtio-gpu-pci" ];
    };
  };

  testScript = lib.replaceStrings [ "@draft@" ] [ "${draft}" ] (builtins.readFile ./desktop.py);
}
