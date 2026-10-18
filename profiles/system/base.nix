{
  config,
  pkgs,
  pkgsUnstable,
  host,
  lib,
  username,
  inputs,
  ...
}:
let
  dankGreeterModule = builtins.fetchurl {
    url = "https://raw.githubusercontent.com/AvengeMedia/dank-greeter/3a38384124dc770a660007235049294d20636159/distro/nix/greeter.nix";
    sha256 = "9055f860ab31aae88b9a71af6528f78dea4ca71364538e11a315c0aff78634a6";
  };
  inherit (import ../../hosts/${host}/variables.nix) keyboardLayout;
  alarmClockApplet = pkgs.alarm-clock-applet.overrideAttrs (old: {
    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.makeWrapper ];
    postFixup = (old.postFixup or "") + ''
      wrapProgram "$out/bin/alarm-clock-applet" \
        --prefix GST_PLUGIN_SYSTEM_PATH_1_0 : ${
          lib.makeSearchPath "lib/gstreamer-1.0" [
            pkgs.gst_all_1.gst-plugins-good
          ]
        }
    '';
  });
  clientSystemPackages = with pkgs; [
    protonvpn-gui
    warp-terminal
    code-cursor
    bun
    libreoffice-qt
    hunspell
    hunspellDicts.en_US
    hunspellDicts.de_DE
    dotnetCorePackages.dotnet_9.sdk
    dotnetCorePackages.dotnet_9.runtime
    dotnetCorePackages.dotnet_9.aspnetcore
    virtiofsd
    remmina
    thunderbird
    ouch
    razergenie
    wineWowPackages.staging
    winetricks
    sl
    dxvk_2
    vkd3d-proton
    keymapp
    wally-cli
    alarmClockApplet
    alacritty
    google-chrome
    zellij
    yazi
    fd
    fzf
    ripgrep
    zoxide
    imagemagick
    poppler
    jq
    p7zip-rar
    ffmpeg
    bottles
    lutris
    heroic
    protonup-ng
    mangohud
    qbittorrent
    meld
    obsidian
    vesktop
    vim
    bubblewrap
    wget
    killall
    docker-compose
    eza
    git
    cmatrix
    lolcat
    htop
    libvirt
    lxqt.lxqt-policykit
    lm_sensors
    unzip
    unrar
    libnotify
    v4l-utils
    ydotool
    duf
    ncdu
    wl-clipboard
    pciutils
    socat
    cowsay
    lshw
    bat
    pkg-config
    meson
    ninja
    brightnessctl
    virt-viewer
    swappy
    appimage-run
    networkmanagerapplet
    yad
    inxi
    playerctl
    nh
    nixfmt
    swww
    matugen
    grim
    slurp
    file-roller
    swaynotificationcenter
    imv
    mpv
    gimp
    pavucontrol
    tree
    spotify
    neovide
    tuigreet
  ];
in
{
  imports = [
    (
      args@{ pkgs, ... }:
      import dankGreeterModule (
        args
        // {
          greeterPkgs = {
            dms-greeter = pkgsUnstable.dms-greeter;
          };
        }
      )
    )
    ../../modules/canbus.nix
    ../../modules/displaylink.nix
    ../../modules/overrides/linux-zen-preempt-fix.nix
    ../../modules/amd-drivers.nix
    ../../modules/nvidia-drivers.nix
    ../../modules/nvidia-prime-drivers.nix
    ../../modules/intel-drivers.nix
    ../../modules/vm-guest-services.nix
    ../../modules/local-hardware-clock.nix
  ];
  boot = {
    kernelModules = [ "v4l2loopback" ];
    extraModulePackages = [ config.boot.kernelPackages.v4l2loopback ];
    extraModprobeConfig = ''
      options v4l2loopback devices=1 video_nr=1 card_label="OBS Cam" exclusive_caps=1
    '';
    kernel.sysctl = {
      "vm.max_map_count" = 2147483642;
    };
    loader.systemd-boot.enable = true;
    loader.efi.canTouchEfiVariables = true;
    tmp = {
      useTmpfs = false;
      tmpfsSize = "30%";
    };
    binfmt.registrations.appimage = {
      wrapInterpreterInShell = false;
      interpreter = "${pkgs.appimage-run}/bin/appimage-run";
      recognitionType = "magic";
      offset = 0;
      mask = ''\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff'';
      magicOrExtension = ''\x7fELF....AI\x02'';
    };
    plymouth.enable = true;
  };

  vm.guest-services.enable = false;
  networking = {
    networkmanager = {
      enable = true;
      plugins = [ pkgs.networkmanager-openvpn ];
    };
  };

  programs = {
    dms-greeter = {
      enable = true;
      compositor.name = "niri";
      configHome = "/home/${username}";
      configFiles = [
        "/home/${username}/.config/DankMaterialShell/settings.json"
        "/home/${username}/.local/state/DankMaterialShell/session.json"
      ];
      logs = {
        save = true;
        path = "/tmp/dms-greeter-${host}.log";
      };
    };
    nix-ld = {
      enable = true;
      libraries = with pkgs; [
        libcap
        xz
        openssl
        zlib
      ];
    };
    xwayland.enable = true;
    gamescope = {
      enable = true;
      capSysNice = true;
    };
    gamemode = {
      enable = true;
      enableRenice = true;
    };
    niri.enable = true;
    firefox.enable = false;
    starship = {
      enable = true;
      settings = {
        add_newline = false;
        buf.symbol = " ";
        c.symbol = " ";
        directory.read_only = " 󰌾";
        docker_context.symbol = " ";
        fossil_branch.symbol = " ";
        git_branch.symbol = " ";
        golang.symbol = " ";
        hg_branch.symbol = " ";
        hostname.ssh_symbol = " ";
        lua.symbol = " ";
        memory_usage.symbol = "󰍛 ";
        meson.symbol = "󰔷 ";
        nim.symbol = "󰆥 ";
        nix_shell.symbol = " ";
        nodejs.symbol = " ";
        ocaml.symbol = " ";
        package.symbol = "󰏗 ";
        python.symbol = " ";
        rust.symbol = " ";
        swift.symbol = " ";
        zig.symbol = " ";
      };
    };
    dconf.enable = true;
    seahorse.enable = true;
    fuse.userAllowOther = true;
    mtr.enable = true;
    gnupg.agent.enable = true;
    virt-manager.enable = true;
    steam = {
      enable = true;
      package = pkgs.millennium-steam;
      remotePlay.openFirewall = true;
    };
    thunar = {
      enable = true;
      plugins = with pkgs.xfce; [
        thunar-archive-plugin
        thunar-volman
      ];
    };
    obs-studio = {
      enable = true;
      package = pkgs.obs-studio.override {
        cudaSupport = true;
      };
    };
  };

  nixpkgs = {
    overlays = [
      (final: prev: {
        vesktop = final.callPackage ../../packages/vesktop-bin.nix { };
        # Solaar 1.1.19 notification checks require a real private session bus
        # and notification server; the Nix sandbox has neither by default.
        # Keep every upstream test enabled and supply only test infrastructure.
        solaar =
          let
            package = prev.solaar;
          in
          if package.version == "1.1.19" then
            package.overrideAttrs (old: {
              nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
                (lib.getBin final.dbus)
                (lib.getBin final.dunst)
                (lib.getBin final.glib)
                final.xvfb-run
              ];
              preCheck = (old.preCheck or "") + ''
                export XDG_CACHE_HOME="$TMPDIR/solaar-test-cache"
                mkdir -p "$XDG_CACHE_HOME"
                export FONTCONFIG_FILE="${final.makeFontsConf { fontDirectories = [ final.dejavu_fonts ]; }}"
                dbus-daemon --config-file="${final.dbus}/share/dbus-1/session.conf" --fork --print-address=3 --print-pid=4 \
                  3> "$TMPDIR/solaar-test-bus-address" 4> "$TMPDIR/solaar-test-bus-pid"
                export DBUS_SESSION_BUS_ADDRESS="$(cat "$TMPDIR/solaar-test-bus-address")"
                solaarTestBusPid="$(cat "$TMPDIR/solaar-test-bus-pid")"
                xvfb-run -a dunst --config /dev/null > "$TMPDIR/solaar-test-notifications.log" 2>&1 &
                solaarNotificationPid=$!
                trap 'kill "$solaarNotificationPid" "$solaarTestBusPid" 2>/dev/null || true' EXIT
                gdbus wait --session --timeout 10 org.freedesktop.Notifications
              '';
              postCheck = ''
                kill "$solaarNotificationPid" "$solaarTestBusPid"
                wait "$solaarNotificationPid" || true
                trap - EXIT
              ''
              + (old.postCheck or "");
            })
          else
            package;
      })
      inputs.millennium.overlays.default
    ];
    config = {
      permittedInsecurePackages = [
        "ventoy-qt5-${pkgs.ventoy-full-qt.version}"
        "qtwebengine-5.15.19"
      ];
    };
  };

  users.mutableUsers = true;

  environment = {
    systemPackages = clientSystemPackages;
    variables = {
      STEAM_EXTRA_COMPAT_TOOLS_PATHS = "/home/${username}/.steam/root/compatibilitytools.d";
    };
    sessionVariables = {
      DOTNET_ROOT = "${pkgs.dotnetCorePackages.dotnet_9.sdk}";
    };
  };

  fonts = {
    packages = with pkgs; [
      noto-fonts-color-emoji
      noto-fonts-cjk-sans
      font-awesome
      symbola
      material-icons
      monaspace
      nerd-fonts.monaspace
      adwaita-qt
      adwaita-qt6
    ];
    enableDefaultPackages = true;
    fontconfig = {
      enable = true;
      cache32Bit = true;
    };
  };

  services = {
    xserver = {
      enable = false;
      xkb = {
        layout = keyboardLayout;
        variant = "";
      };
    };
    smartd = {
      enable = false;
      autodetect = true;
    };
    libinput.enable = true;
    gvfs.enable = true;
    # Thunar needs the D-Bus thumbnailer for thumbnails and its image preview pane.
    tumbler.enable = true;
    openssh.enable = true;
    flatpak.enable = true;
    printing.enable = true;
    gnome.gnome-keyring.enable = true;
    avahi = {
      enable = true;
      nssmdns4 = true;
      openFirewall = true;
    };
    ipp-usb.enable = true;
    syncthing = {
      enable = false;
      user = username;
      dataDir = "/home/${username}";
      configDir = "/home/${username}/.config/syncthing";
    };
    tailscale.enable = true;
    pipewire = {
      enable = true;
      alsa.enable = true;
      alsa.support32Bit = true;
      pulse.enable = true;
    };
    rpcbind.enable = false;
    nfs.server.enable = false;
    pulseaudio.enable = false;
  };

  systemd.services.greetd = {
    restartIfChanged = false;
    after = [ "systemd-udev-settle.service" ];
    wants = [ "systemd-udev-settle.service" ];
  };
  systemd.services.flatpak-repo = {
    path = [
      pkgs.flatpak
      pkgs.gnugrep
    ];
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    script = ''
      flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
      flatpak remote-add --if-not-exists GeForceNOW https://international.download.nvidia.com/GFNLinux/flatpak/geforcenow.flatpakrepo

      if flatpak info --system com.nvidia.geforcenow >/dev/null 2>&1; then
        if update_output=$(flatpak update --system -y --noninteractive com.nvidia.geforcenow 2>&1); then
          printf '%s\n' "$update_output"
        elif [[ $(printf '%s\n' "$update_output" | grep -c '^Error:') -eq 1 ]] \
          && printf '%s\n' "$update_output" \
            | grep -q '^Error: Failed to update com\.nvidia\.geforcenow: .*Update is older than current version$'; then
          printf 'GeForce NOW remote is older than the installed version; keeping the installed version.\n'
        else
          printf '%s\n' "$update_output" >&2
          exit 1
        fi
      else
        flatpak install --system -y --noninteractive GeForceNOW com.nvidia.geforcenow
      fi
    '';
  };

  hardware = {
    keyboard.zsa.enable = true;
    logitech.wireless.enable = true;
    logitech.wireless.enableGraphical = true;
    graphics = {
      enable = true;
      enable32Bit = true;
    };
  };

  security = {
    rtkit.enable = true;
    polkit = {
      enable = true;
      extraConfig = ''
        polkit.addRule(function(action, subject) {
          if (
            subject.isInGroup("users")
              && (
                action.id == "org.freedesktop.login1.reboot" ||
                action.id == "org.freedesktop.login1.reboot-multiple-sessions" ||
                action.id == "org.freedesktop.login1.power-off" ||
                action.id == "org.freedesktop.login1.power-off-multiple-sessions"
              )
            )
          {
            return polkit.Result.YES;
          }
        })
      '';
    };
  };

  virtualisation = {
    libvirtd = {
      enable = true;
      qemu.swtpm.enable = true;
    };
    docker.enable = true;
  };
}
