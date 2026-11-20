{
  lib,
  writeShellScriptBin,
  python3,
  glib,
  fuzzel,
  workspace,
}:
let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
  runPython = file: ''
    export GI_TYPELIB_PATH=${glib}/lib/girepository-1.0
    exec ${python}/bin/python3 -s ${file} "$@"
  '';
  privateLaunch = writeShellScriptBin "launch-desktop-entry" (runPython ./private.py);
in
writeShellScriptBin "weasel-agent-launcher" ''
  export XDG_DATA_DIRS="''${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:/run/current-system/sw/share"
  export PATH=${
    lib.makeBinPath [
      workspace
      fuzzel
      privateLaunch
    ]
  }:$PATH
  ${runPython ./launcher.py}
''
