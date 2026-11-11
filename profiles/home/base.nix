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
  repoDefaultPath = "${config.home.homeDirectory}/weasel-os";
in
{
  home = {
    file = {
      "Pictures/wallpapers" = {
        source = ../../pictures/wallpapers;
        recursive = true;
      };
      "Pictures/face.png".source = ../../pictures/weasel.png;
    };
    packages = [
      (import ../../scripts/emopicker9000.nix { inherit pkgs; })
      (import ../../scripts/squirtle.nix { inherit pkgs; })
      (import ../../scripts/nvidia-offload.nix { inherit pkgs; })
      (import ../../scripts/wallsetter.nix {
        inherit pkgs username;
      })
      (import ../../scripts/weasel-shell-helpers.nix {
        inherit
          config
          host
          pkgs
          pkgsUnstable
          ;
      })
      (import ../../scripts/weasel-dms-session.nix { inherit pkgs; })
      (import ../../scripts/weasel-collect-session-debug.nix { inherit pkgs; })
      (import ../../scripts/web-search.nix { inherit pkgs; })
      (import ../../scripts/rofi-launcher.nix { inherit pkgs; })
      (import ../../scripts/screenshootin.nix { inherit pkgs; })
      pkgs.helix
      pkgs.adw-gtk3
      pkgs.bibata-cursors
      pkgs.capitaine-cursors
      pkgs.phinger-cursors
      pkgs.papirus-icon-theme
      pkgs.simp1e-cursors
      pkgs.vanilla-dmz
      pkgs.volantes-cursors
      pkgs.kitty
      pkgs.localsend
      pkgsUnstable.stremio-linux-shell
      pkgsUnstable.zed-editor
    ];
    sessionVariables = {
      GTK_THEME = "adw-gtk3-dark";
      QT_STYLE_OVERRIDE = "adwaita-dark";
      QT_QPA_PLATFORMTHEME = "gtk3";
    };
  };

  imports = [
    inputs.dms.homeModules."dank-material-shell"
    ../../programs/emoji.nix
    ../../programs/fastfetch
    ../../programs/matugen.nix
    ../../programs/niri.nix
    ../../programs/neovim.nix
    ../../programs/helix.nix
    ../../programs/terminal-stack.nix
    ../../programs/vscode.nix
    ../../programs/zed.nix
    ../../programs/rofi/rofi.nix
    ../../programs/rofi/config-emoji.nix
    ../../programs/rofi/config-long.nix
    ../../programs/swaync.nix
  ];

  xdg = {
    userDirs = {
      enable = true;
      createDirectories = true;
    };
    configFile = {
      "mimeapps.list" = {
        source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/mimeapps.list";
        force = true;
      };
      "kitty/open-actions.conf" = {
        source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/kitty/open-actions.conf";
        force = true;
      };
      "DankMaterialShell/settings.json" = {
        source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/dank-material-shell/settings.json";
        force = true;
      };
      "swappy/config".text = ''
        [Default]
        save_dir=/home/${username}/Pictures/Screenshots
        save_filename_format=swappy-%Y%m%d-%H%M%S.png
        show_panel=false
        line_size=5
        text_size=20
        text_font=Ubuntu
        paint_mode=brush
        early_exit=true
        fill_shape=false
      '';
    };
  };

  home.file = {
    ".config/gtk-3.0/gtk.css" = {
      text = ''
        @import url("dank-colors.css");
      '';
      force = true;
    };
    ".config/gtk-4.0/gtk.css" = {
      text = ''
        @import url("dank-colors.css");
      '';
      force = true;
    };
    ".config/kitty/kitty.conf" = {
      source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/kitty/kitty.conf";
      force = true;
    };
    ".config/qt5ct/qt5ct.conf" = {
      source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/qt5ct.conf";
      force = true;
    };
    ".config/qt6ct/qt6ct.conf" = {
      source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/qt6ct.conf";
      force = true;
    };
  };

  home.file.".local/share/applications/kitty.desktop" = {
    source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/kitty.desktop";
  };

  home.activation.ensureThemeIconDirs = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    install -d "$HOME/.local/share/icons"

    ln -sfn "${pkgs.adwaita-icon-theme}/share/icons/Adwaita" "$HOME/.local/share/icons/Adwaita"

    ln -sfn "${pkgs.bibata-cursors}/share/icons/Bibata-Modern-Amber" "$HOME/.local/share/icons/Bibata-Modern-Amber"
    ln -sfn "${pkgs.bibata-cursors}/share/icons/Bibata-Modern-Classic" "$HOME/.local/share/icons/Bibata-Modern-Classic"
    ln -sfn "${pkgs.bibata-cursors}/share/icons/Bibata-Modern-Ice" "$HOME/.local/share/icons/Bibata-Modern-Ice"

    ln -sfn "${pkgs.capitaine-cursors}/share/icons/capitaine-cursors" "$HOME/.local/share/icons/capitaine-cursors"
    ln -sfn "${pkgs.capitaine-cursors}/share/icons/capitaine-cursors-white" "$HOME/.local/share/icons/capitaine-cursors-white"

    ln -sfn "${pkgs.numix-icon-theme}/share/icons/Numix" "$HOME/.local/share/icons/Numix"
    ln -sfn "${pkgs.numix-icon-theme}/share/icons/Numix-Light" "$HOME/.local/share/icons/Numix-Light"

    ln -sfn "${pkgs.papirus-icon-theme}/share/icons/Papirus" "$HOME/.local/share/icons/Papirus"
    ln -sfn "${pkgs.papirus-icon-theme}/share/icons/Papirus-Dark" "$HOME/.local/share/icons/Papirus-Dark"
    ln -sfn "${pkgs.papirus-icon-theme}/share/icons/Papirus-Light" "$HOME/.local/share/icons/Papirus-Light"

    ln -sfn "${pkgs.phinger-cursors}/share/icons/phinger-cursors-dark" "$HOME/.local/share/icons/phinger-cursors-dark"
    ln -sfn "${pkgs.phinger-cursors}/share/icons/phinger-cursors-light" "$HOME/.local/share/icons/phinger-cursors-light"

    ln -sfn "${pkgs.rose-pine-icon-theme}/share/icons/rose-pine" "$HOME/.local/share/icons/rose-pine"
    ln -sfn "${pkgs.rose-pine-icon-theme}/share/icons/rose-pine-dawn" "$HOME/.local/share/icons/rose-pine-dawn"
    ln -sfn "${pkgs.rose-pine-icon-theme}/share/icons/rose-pine-moon" "$HOME/.local/share/icons/rose-pine-moon"

    ln -sfn "${pkgs.simp1e-cursors}/share/icons/Simp1e" "$HOME/.local/share/icons/Simp1e"
    ln -sfn "${pkgs.simp1e-cursors}/share/icons/Simp1e-Adw-Dark" "$HOME/.local/share/icons/Simp1e-Adw-Dark"
    ln -sfn "${pkgs.simp1e-cursors}/share/icons/Simp1e-Rose-Pine-Moon" "$HOME/.local/share/icons/Simp1e-Rose-Pine-Moon"
    ln -sfn "${pkgs.simp1e-cursors}/share/icons/Simp1e-Tokyo-Night" "$HOME/.local/share/icons/Simp1e-Tokyo-Night"

    ln -sfn "${pkgs.tela-icon-theme}/share/icons/Tela" "$HOME/.local/share/icons/Tela"
    ln -sfn "${pkgs.tela-icon-theme}/share/icons/Tela-dark" "$HOME/.local/share/icons/Tela-dark"
    ln -sfn "${pkgs.tela-icon-theme}/share/icons/Tela-dracula" "$HOME/.local/share/icons/Tela-dracula"
    ln -sfn "${pkgs.tela-icon-theme}/share/icons/Tela-light" "$HOME/.local/share/icons/Tela-light"
    ln -sfn "${pkgs.tela-icon-theme}/share/icons/Tela-nord" "$HOME/.local/share/icons/Tela-nord"

    ln -sfn "${pkgs.vanilla-dmz}/share/icons/DMZ-White" "$HOME/.local/share/icons/DMZ-White"

    ln -sfn "${pkgs.volantes-cursors}/share/icons/volantes_cursors" "$HOME/.local/share/icons/volantes_cursors"
    ln -sfn "${pkgs.volantes-cursors}/share/icons/volantes_light_cursors" "$HOME/.local/share/icons/volantes_light_cursors"
  '';

  dconf.settings = {
    "org/virt-manager/virt-manager/connections" = {
      autoconnect = [ "qemu:///system" ];
      uris = [ "qemu:///system" ];
    };
  };

  gtk = {
    theme = {
      name = "adw-gtk3-dark";
      package = pkgs.adw-gtk3;
    };
    iconTheme = {
      name = "Papirus-Dark";
      package = pkgs.papirus-icon-theme;
    };
    gtk3.extraConfig.gtk-application-prefer-dark-theme = 1;
    gtk4.extraConfig.gtk-application-prefer-dark-theme = 1;
  };

  qt = {
    enable = true;
    style.name = lib.mkForce "adwaita-dark";
  };

  systemd.user.sessionVariables = {
    QT_QPA_PLATFORMTHEME = "gtk3";
  };
}
