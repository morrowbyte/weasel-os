{
  lib,
  stdenv,
  makeWrapper,
  pkg-config,
  wayland-scanner,
  wayland,
  wlr-protocols,
  python3,
  bubblewrap,
  slirp4netns,
  cacert,
  sway-unwrapped,
  dbus,
  grim,
  wtype,
  wl-clipboard,
  bash,
  coreutils,
  makeFontsConf,
  dejavu_fonts,
}:
let
  fonts = makeFontsConf { fontDirectories = [ dejavu_fonts ]; };
in
stdenv.mkDerivation {
  pname = "weasel-workspace";
  version = "0.1.0";
  src = ./.;
  strictDeps = true;
  nativeBuildInputs = [
    makeWrapper
    pkg-config
    wayland-scanner
  ];
  buildInputs = [ wayland ];
  dontConfigure = true;
  buildPhase = ''
    runHook preBuild
    protocol=${wlr-protocols}/share/wlr-protocols/unstable/wlr-virtual-pointer-unstable-v1.xml
    wayland-scanner client-header "$protocol" pointer-protocol.h
    wayland-scanner private-code "$protocol" pointer-protocol.c
    $CC -Wall -Wextra -Werror pointer.c pointer-protocol.c -o workspace-pointer $(pkg-config --cflags --libs wayland-client)
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    dir="$out/libexec/weasel-workspace"
    mkdir -p "$dir" "$out/bin"
    cp *.py workspace-pointer sway.conf passwd group resolv.conf "$dir/"
    substituteInPlace "$dir/sandbox.py" \
      --replace-fail '@bwrap@' '${bubblewrap}/bin/bwrap' \
      --replace-fail '@slirp@' '${slirp4netns}/bin/slirp4netns' \
      --replace-fail '@dbusConf@' '${dbus}/share/dbus-1/session.conf' \
      --replace-fail '@cert@' '${cacert}/etc/ssl/certs/ca-bundle.crt' \
      --replace-fail '@shell@' '${bash}/bin/bash' \
      --replace-fail '@env@' '${coreutils}/bin/env' \
      --replace-fail '@fonts@' '${fonts}' \
      --replace-fail '@path@' '${
        lib.makeBinPath [
          sway-unwrapped
          dbus
          grim
          wtype
          wl-clipboard
          bash
          coreutils
        ]
      }'
    makeWrapper ${python3}/bin/python3 "$out/bin/weasel-workspace" --add-flags "-I $dir/workspace.py"
    install -Dm644 README.md "$out/share/doc/weasel-workspace/README.md"
    runHook postInstall
  '';
  meta = {
    description = "Control a private headless Wayland desktop for background tasks";
    mainProgram = "weasel-workspace";
    platforms = lib.platforms.linux;
  };
}
