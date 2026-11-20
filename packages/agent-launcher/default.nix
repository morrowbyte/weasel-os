{
  lib,
  writeShellScriptBin,
  python3,
  glib,
  foot,
  coreutils,
  replaceVars,
  fuzzel,
  workspace,
}:
let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
  runPython = file: ''
    export GI_TYPELIB_PATH=${lib.getLib glib}/lib/girepository-1.0
    exec ${python}/bin/python3 -s ${file} "$@"
  '';
  privateScript = replaceVars ./private.py {
    foot = "${foot}/bin/foot";
    cat = "${coreutils}/bin/cat";
    sleep = "${coreutils}/bin/sleep";
  };
  privateLaunch = writeShellScriptBin "launch-desktop-entry" (runPython privateScript);
in
(writeShellScriptBin "weasel-agent-launcher" ''
  export XDG_DATA_DIRS="''${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:/run/current-system/sw/share"
  export PATH=${
    lib.makeBinPath [
      workspace
      fuzzel
      privateLaunch
    ]
  }:$PATH
  ${runPython ./launcher.py}
'').overrideAttrs
  (old: {
    checkPhase = old.checkPhase + ''
      export GI_TYPELIB_PATH=${lib.getLib glib}/lib/girepository-1.0 PYTHONDONTWRITEBYTECODE=1
      ${python}/bin/python3 -s ${./.}/checks/desktop_entry.py
    '';
  })
