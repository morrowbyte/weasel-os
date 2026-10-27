{
  config,
  lib,
  pkgs,
  ...
}:
{
  imports = [
    ../../profiles/system/common.nix
    ../../profiles/system/base.nix
    ../../profiles/system/client.nix
    ../../profiles/system/laptop.nix
    ../../modules/home-snapshots.nix
    ../../modules/storage-recovery.nix
    ../../modules/btrfs-reserve.nix
    ../../modules/daily-updates.nix
    ../../modules/wispr-flow.nix
    # ../../modules/networking/internal-dns.nix
    ./hardware.nix
    ./users.nix
  ];

  # Preserve the loaded NVIDIA 580.142 ABI during this live release migration.
  # Re-review the production-driver hold on 2026-10-11 and coordinate its boot.
  hardware.nvidia.package = lib.mkForce (
    config.boot.kernelPackages.nvidiaPackages.mkDriver {
      version = "580.142";
      sha256_64bit = "sha256-IJFfzz/+icNVDPk7YKBKKFRTFQ2S4kaOGRGkNiBEdWM=";
      sha256_aarch64 = "sha256-jntr88SpTYR648P1rizQjB/8KleBoa14Ay12vx8XETM=";
      openSha256 = "sha256-v968LbRqy8jB9+yHy9ceP2TDdgyqfDQ6P41NsCoM2AY=";
      settingsSha256 = "sha256-BnrIlj5AvXTfqg/qcBt2OS9bTDDZd3uhf5jqOtTMTQM=";
      persistencedSha256 = "sha256-il403KPFAnDbB+dITnBGljhpsUPjZwmLjGt8iPKuBqw=";
    }
  );

  # Preserve the running D-Bus implementation during the 26.05 live migration.
  # Switching to dbus-broker requires a reboot and cannot be tested by a switch.
  services.dbus.implementation = "dbus";

  # Release reverse-tunnel listeners when a VPN drop leaves a dead SSH client.
  services.openssh.settings = {
    ClientAliveInterval = 30;
    ClientAliveCountMax = 3;
  };

  # Keep the known boot path while migrating the release without a reboot.
  boot.initrd.systemd.enable = false;

  # Keep the running daemon and its containers through the protected live switch.
  # The new Docker package becomes effective after a coordinated daemon restart.
  systemd.services.docker.restartIfChanged = false;

  # Keep the active DisplayLink session through the protected live switch.
  # Apply the rebuilt manager only during a coordinated display restart.
  systemd.services.dlm.restartIfChanged = false;

  # The AX211 can enumerate on PCI without iwlwifi being auto-loaded on some boots.
  # Request the in-kernel driver explicitly before NetworkManager starts.
  boot.kernelModules = [ "iwlwifi" ];

  # NetBird's hardened per-client daemon integrates DNS through systemd-resolved.
  # This avoids an unprivileged daemon mutating openresolv state and lets the
  # module's scoped polkit rule authorize only NetBird's resolved operations.
  services.resolved.enable = true;

  # NetBird is the laptop's sole overlay. Other hosts may still enable the
  # shared profile's Tailscale service independently.
  services.tailscale.enable = lib.mkForce false;

  weasel.wispr-flow.enable = true;

  # T3's pinned headless Chrome is downloaded after package fixup. Existing
  # T3 sessions already use this stable nix-ld library path, so activation
  # repairs future preview launches without restarting the desktop app.
  programs.nix-ld.libraries = with pkgs; [
    alsa-lib
    at-spi2-core
    dbus
    expat
    glib
    libgbm
    libx11
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxcb
    libxkbcommon
    nspr
    nss
    systemd
  ];

  services.netbird = {
    package = pkgs.callPackage ../../packages/netbird-bin.nix { };
    ui.package = pkgs.callPackage ../../packages/netbird-ui-bin.nix {
      daemonSocket = "unix:///var/run/netbird-personal/sock";
    };
    useRoutingFeatures = "none";
    ui.enable = true;
    clients.personal = {
      name = "personal";
      interface = "nb-personal";
      port = 51821;
      dns-resolver = {
        address = "127.0.0.153";
        port = 53;
      };
      hardened = true;
      autoStart = true;
      ui.enable = true;
      environment = {
        NB_MANAGEMENT_URL = "https://netbird.evilweasel.cloud";
        NB_ADMIN_URL = "https://netbird.evilweasel.cloud";
        # Avoid the generic-XDP page_pool split-lock crash on this kernel.
        # Keep kernel WireGuard and use NetBird's UDP relay proxy instead.
        NB_DISABLE_EBPF_WG_PROXY = "true";
      };
      openFirewall = true;
      openInternalFirewall = true;
    };
  };

  users.users.evilweasel.extraGroups = [ "netbird-personal" ];

  # Only the RPM payload is installed. Its scriptlets would otherwise add an
  # imperative OpenAI DNF repository and updater outside the flake.
  environment.systemPackages = [
    (pkgs.callPackage ../../packages/chatgpt/default.nix { })
    (pkgs.callPackage ../../packages/tuios-bin.nix { })
    (import ../../packages/mtcode/default.nix { inherit pkgs; })
  ];
}
