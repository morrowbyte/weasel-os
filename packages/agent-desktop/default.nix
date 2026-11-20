{
  lib,
  stdenvNoCC,
  makeWrapper,
  callPackage,
  python3,
  fuzzel,
  imv,
  foot,
  nano,
  actor ? callPackage ../computer-use { },
  indicator ? callPackage ../computer-use-indicator { },
}:
let
  workspace = callPackage ../agent-workspace { };
in
stdenvNoCC.mkDerivation {
  pname = "weasel";
  version = "0.1.0";
  src = ./.;
  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    install -Dm644 -t "$out/libexec/weasel" *.py
    install -Dm644 README.md "$out/share/doc/weasel/README.md"
    mkdir -p "$out/bin"
    makeWrapper ${python3}/bin/python3 "$out/bin/weasel" \
      --add-flags "-EsB $out/libexec/weasel/weasel.py" \
      --prefix PATH : ${
        lib.makeBinPath [
          actor
          indicator
          workspace
          fuzzel
          imv
        ]
      } \
      --set WEASEL_PRIVATE_TERMINAL "${foot}/bin/foot" \
      --set WEASEL_PRIVATE_EDITOR "${nano}/bin/nano"
    runHook postInstall
  '';
  meta = {
    description = "Keyboard palette, CLI and stdio MCP for Niri and private Wayland workspaces";
    mainProgram = "weasel";
    platforms = lib.platforms.linux;
  };
}
