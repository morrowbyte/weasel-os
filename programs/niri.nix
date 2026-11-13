{
  config,
  lib,
  pkgs,
  ...
}:
let
  dmsSession = import ../scripts/weasel-dms-session.nix { inherit pkgs; };
  cfg = config.weasel.session;
  repoPath = "${config.home.homeDirectory}/weasel-os";
  dmsRepoPath = "${repoPath}/programs/niri/dms";
  dmsConfigFiles = [
    "alttab.kdl"
    "binds.kdl"
    "clipboard.kdl"
    "colors.kdl"
    "cursor.kdl"
    "env.kdl"
    "layout.kdl"
    "outputs.kdl"
    "windowrules.kdl"
    "wpblur.kdl"
  ];
  dmsSpawnLine = lib.optionalString cfg.startDms ''
    spawn-at-startup "${cfg.dmsCommand}"
  '';
  dmsConfigFilesAttrs = builtins.listToAttrs (
    map (file: {
      name = "niri/dms/${file}";
      value = {
        source = config.lib.file.mkOutOfStoreSymlink "${dmsRepoPath}/${file}";
        force = true;
      };
    }) dmsConfigFiles
  );
  generatedConfig = lib.replaceStrings [ "// __WEASEL_DMS_SPAWN__" ] [ dmsSpawnLine ] (
    builtins.readFile ./niri/config.kdl
  );
in
{
  options.weasel.session = {
    startDms = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Whether to start the DMS shell automatically from Niri.";
    };

    dmsCommand = lib.mkOption {
      type = lib.types.str;
      default = lib.getExe dmsSession;
      description = "Command spawned by Niri to launch the DMS session wrapper.";
    };
  };

  config = {
    xdg.configFile = {
      "niri/config.kdl" = {
        text = generatedConfig;
        force = true;
      };
      "niri/base/animations.kdl" = {
        source = ./niri/base/animations.kdl;
        force = true;
      };
      "niri/base/binds.kdl" = {
        source = ./niri/base/binds.kdl;
        force = true;
      };
      "niri/base/input.kdl" = {
        source = ./niri/base/input.kdl;
        force = true;
      };
      "niri/base/layout.kdl" = {
        source = ./niri/base/layout.kdl;
        force = true;
      };
      "niri/base/windowrules.kdl" = {
        source = ./niri/base/windowrules.kdl;
        force = true;
      };
    }
    // dmsConfigFilesAttrs;

    home.activation.ensureNiriDmsBootstrap = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      mkdir -p "$HOME/.config/niri/dms/profiles"
    '';

    # Crew manages the contents; never turn its mutable include into a store link.
    home.activation.ensureCrewPetBindings = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      $DRY_RUN_CMD ${pkgs.python3}/bin/python3 - ${lib.escapeShellArg "${config.xdg.configHome}/niri/crew-pet.kdl"} <<'PY'
      import os
      import sys

      path = sys.argv[1]
      os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
      try:
          descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
      except FileExistsError:
          pass  # Preserve every existing entry, including dangling symlinks.
      else:
          os.close(descriptor)
      PY
    '';
  };
}
