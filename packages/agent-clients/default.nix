{
  buildEnv,
  callPackage,
  codex,
  foot,
  makeDesktopItem,
}:
let
  chatgpt = callPackage ../chatgpt { };
  crew = callPackage ./crew.nix { };
  codexDesktop = makeDesktopItem {
    name = "codex-cli";
    desktopName = "Codex CLI";
    exec = "${foot}/bin/foot ${codex}/bin/codex";
    icon = "utilities-terminal";
    categories = [ "Development" ];
    terminal = false;
  };
in
buildEnv {
  name = "agent-clients";
  paths = [
    codex
    codexDesktop
    chatgpt
    crew
  ];
  pathsToLink = [
    "/bin"
    "/share/applications"
    "/share/icons"
    "/share/pixmaps"
  ];
  postBuild = ''
    for program in codex chatgpt t3code-morrowbyte; do
      test -x "$out/bin/$program"
    done
    for launcher in codex-cli chatgpt t3code-morrowbyte; do
      test -s "$out/share/applications/$launcher.desktop"
    done
  '';
  passthru = { inherit codex chatgpt crew; };
  meta.platforms = [ "x86_64-linux" ];
}
