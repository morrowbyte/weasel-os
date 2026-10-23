{
  config,
  host,
  pkgs,
  pkgsUnstable,
  inputs,
  lib,
  username,
  ...
}:
{
  features.canbus.enable = true;
  features.displaylink.enable = true;
  services.xserver.videoDrivers = lib.mkIf (host == "nixy-laptop") [ "nvidia" ];

  xdg.portal = {
    enable = true;
    xdgOpenUsePortal = true;
    extraPortals = [
      pkgs.xdg-desktop-portal-gnome
      pkgs.xdg-desktop-portal-gtk
    ]
    ++ lib.optional (host == "nixy-laptop") (
      import ../../packages/xdg-desktop-portal-luminous.nix { inherit pkgs pkgsUnstable; }
    );
    config.niri = {
      default = [
        "gnome"
        "gtk"
      ];
      "org.freedesktop.impl.portal.Access" = [ "gtk" ];
      "org.freedesktop.impl.portal.Notification" = [ "gtk" ];
      # GNOME's RemoteDesktop backend requires Mutter; Luminous uses Niri's
      # virtual input protocols and asks permission before enabling autotype.
      "org.freedesktop.impl.portal.RemoteDesktop" = lib.mkIf (host == "nixy-laptop") [ "luminous" ];
      "org.freedesktop.impl.portal.ScreenCast" = [ "gnome" ];
      "org.freedesktop.impl.portal.Screenshot" = [ "gnome" ];
      "org.freedesktop.impl.portal.Secret" = [ "gnome-keyring" ];
    };
  };

  services.gnome.at-spi2-core.enable = true;

  local.hardware-clock.enable = true;

  networking = {
    networkmanager.wifi.backend = lib.mkForce "iwd";
    wireless.iwd.enable = lib.mkForce true;
  };

  programs = {
    gnupg.agent.enableSSHSupport = false;
    steam.gamescopeSession.enable = false;
    ssh = {
      startAgent = true;
      extraConfig = ''
        Host *
          AddKeysToAgent yes
      '';
    };
  };

  environment.systemPackages =
    (with pkgs; [
      bubblewrap
      claude-code
      openssl
      openvpn
      proton-pass
      # Azure Data Studio retired in February 2026. The existing VS Code
      # installation and MSSQL extension remain; personal ADS data is retained.
      teams-for-linux
      slack
      protonmail-desktop
      protonmail-bridge-gui
      python3
      python3Packages.pip
      snapper
      btrfs-progs
      libxkbcommon
      nil
      xwayland-satellite
      cava
      cliphist
      helix
      lsfg-vk
      lsfg-vk-ui
      superTuxKart
      prismlauncher
      ftb-app
      firefox
      ventoy-full-qt
      antigravity-fhs
      obs-studio
      kontroll
      protontricks
      mangojuice
      spice
      spice-gtk
      spice-protocol
      virtio-win
      win-spice
    ])
    ++ [
      pkgsUnstable.nodejs
      pkgsUnstable.dgop
      pkgsUnstable.dsearch
      inputs.helium.packages.${pkgs.stdenv.hostPlatform.system}.default
      (
        let
          zen = inputs.zen-browser.packages.${pkgs.stdenv.hostPlatform.system};
        in
        if
          pkgs.stdenv.hostPlatform.system == "x86_64-linux" && zen.zen-browser-unwrapped.version == "1.23.3b"
        then
          # The upstream flake checksum differs from the official release asset digest.
          # https://github.com/zen-browser/desktop/releases/tag/1.23.3b
          zen.default.override {
            zen-browser-unwrapped = zen.zen-browser-unwrapped.override {
              url = "https://github.com/zen-browser/desktop/releases/download/1.23.3b/zen.linux-x86_64.tar.xz";
              hash = "sha256-uyBF0+IWVNQP6EElAJLurf0sH8gx7vtQkiJ3PIvcoUk=";
            };
          }
        else
          zen.default
      )
      inputs.self.packages.${pkgs.stdenv.hostPlatform.system}.t3code
      (import ../../packages/handy {
        handy = inputs.handy;
        handyNixpkgs = inputs.handy-nixpkgs;
        system = pkgs.stdenv.hostPlatform.system;
      })
    ];

  services = {
    snapper = {
      snapshotInterval = "daily";
      cleanupInterval = "daily";

      configs.home = {
        SUBVOLUME = "/home";
        TIMELINE_CREATE = true;
        TIMELINE_CLEANUP = true;
        TIMELINE_LIMIT_DAILY = 7;
        TIMELINE_LIMIT_WEEKLY = 0;
        TIMELINE_LIMIT_MONTHLY = 0;
        TIMELINE_LIMIT_YEARLY = 0;
        SPACE_LIMIT = "0.3";
      };
    };
    tlp.enable = false;
    power-profiles-daemon.enable = true;
    upower.enable = true;
    gnome.gcr-ssh-agent.enable = false;
    blueman.enable = true;
  };

  hardware = {
    # RazerGenie is installed for all client machines.  Its backend was only
    # enabled on the desktop, leaving the laptop GUI unable to talk to the
    # Naga V2 HyperSpeed receiver.
    openrazer = {
      enable = true;
      users = [ username ];
    };

    sane = {
      enable = true;
      extraBackends = [ pkgs.sane-airscan ];
      disabledDefaultBackends = [ "escl" ];
    };
    bluetooth = {
      enable = true;
      powerOnBoot = true;
    };
  };

  services.udev = {
    packages = [ pkgs.sane-airscan ];
    extraRules = ''
      ATTRS{name}=="Sony Interactive Entertainment DualSense Wireless Controller Touchpad", ENV{LIBINPUT_IGNORE_DEVICE}="1"
      ATTRS{name}=="DualSense Wireless Controller Touchpad", ENV{LIBINPUT_IGNORE_DEVICE}="1"
    '';
  };

  virtualisation = {
    libvirtd.qemu.package = pkgs.qemu_kvm;
    spiceUSBRedirection.enable = true;
  };

  boot.kernel.sysctl = {
    "net.bridge.bridge-nf-call-iptables" = 0;
    "net.bridge.bridge-nf-call-ip6tables" = 0;
    "net.bridge.bridge-nf-call-arptables" = 0;
  };
}
