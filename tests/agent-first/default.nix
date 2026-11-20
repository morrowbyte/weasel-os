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
    for phase in private actor legacy pixels; do
      IFS= read -r line
      printf '%s\n' "$line" > "$HOME/draft-$phase.txt"
      printf '\033]2;draft-%s-complete\007' "$phase"
    done
    exec ${coreutils}/bin/sleep infinity
  '';
in
testers.runNixOSTest {
  name = "agent-first-desktop";

  nodes.machine = { config, pkgs, ... }: {
    imports = [
      ../../modules/agent-first.nix
      ../../hosts/weasel-agent/vm-graphical.nix
    ];
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
    environment.etc."niri/config.kdl".text = lib.mkAfter ''
      window-rule {
          match app-id="^weasel-protected$"
          block-out-from "screen-capture"
      }
    '';
    environment.systemPackages = [
      jq
      pkgs.imagemagick
      pkgs.xterm
      (pkgs.callPackage ../../packages/agent-clients { })
    ];
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

  testScript =
    lib.replaceStrings [ "@draft@" ] [ "${draft}" ] (builtins.readFile ./desktop.py)
    + "\n"
    + lib.replaceStrings [ "@xterm@" ] [ "${pkgs.xterm}/bin/xterm" ] (builtins.readFile ./legacy-app.py)
    + "\n"
    + builtins.readFile ./pixels.py
    + "\n"
    + builtins.readFile ./presentation.py
    + "\n"
    + lib.replaceStrings [ "magick" ] [ "${pkgs.imagemagick}/bin/magick" ] (builtins.readFile ./viewer-pointer.py)
    + "\n"
    + builtins.readFile ./managed-launch.py
    + "\n"
    + builtins.readFile ./clients.py
    + ''

      with subtest("Crew's own local provider reads native state and initializes the two system MCP servers"):
          machine.copy_from_host("${./crew.py}", "/tmp/crew-provider-probe.py")
          machine.succeed(operator("python3 /tmp/crew-provider-probe.py"), timeout=155)

      with subtest("A named Mousepad profile retains the saved document across a full guest reboot"):
          profile_command = "python3 /tmp/private-profile.py --name profile-test-boat --phase "
          machine.copy_from_host("${./persistent-profile.py}", "/tmp/private-profile.py")
          machine.succeed(operator(profile_command + "save"), timeout=90)
          machine.shutdown()
          machine.start()
          machine.wait_for_unit("greetd.service")
          machine.wait_until_succeeds(operator("niri msg --json outputs | jq -e 'length == 1'"))
          machine.send_key("spc")
          machine.send_key("meta_l-ret")
          machine.wait_until_succeeds(operator("niri msg --json windows | jq -e '.[] | select(.is_focused and .app_id == \"foot\")'"))
          machine.copy_from_host("${./persistent-profile.py}", "/tmp/private-profile.py")
          machine.succeed(operator(profile_command + "reopen"), timeout=90)
    '';
}
