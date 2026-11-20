{
  config,
  lib,
  pkgs,
  modulesPath,
  ...
}:
{
  imports = [
    ../../modules/agent-first.nix
    (modulesPath + "/profiles/qemu-guest.nix")
  ];
  networking.hostName = "weasel-agent";
  nixpkgs.config.allowUnfreePredicate = pkg: lib.getName pkg == "chatgpt";
  environment.systemPackages = [ (pkgs.callPackage ../../packages/agent-clients { }) ];
  boot.loader.grub.device = "/dev/vda";
  fileSystems."/" = {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };
  users.users.operator.isNormalUser = true;
  services.greetd = {
    enable = true;
    settings.default_session.command = "${pkgs.greetd}/bin/agreety --cmd ${config.programs.niri.package}/bin/niri-session";
  };
  virtualisation.vmVariant = {
    imports = [ ./vm-graphical.nix ];
    virtualisation = {
      memorySize = 4096;
      cores = 2;
      diskSize = 4096;
      resolution = {
        x = 1280;
        y = 800;
      };
      qemu.virtioKeyboard = true;
      qemu.options = [ "-vga none -device virtio-gpu-pci" ];
    };
    services.greetd.settings.default_session = {
      user = "operator";
      command = lib.mkForce (
        toString (
          import ./vm-session.nix {
            inherit pkgs;
            niri = config.programs.niri.package;
          }
        )
      );
    };
  };
  system.stateVersion = "26.05";
}
