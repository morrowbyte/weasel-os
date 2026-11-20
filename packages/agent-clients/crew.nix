{
  lib,
  stdenv,
  fetchurl,
  appimageTools,
  autoPatchelfHook,
  makeWrapper,
  callPackage,
  python3,
  wrapGAppsHook3,
  makeDesktopItem,
  alsa-lib,
  at-spi2-atk,
  at-spi2-core,
  cairo,
  cups,
  dbus,
  dbus-glib,
  expat,
  gdk-pixbuf,
  glib,
  gtk3,
  libdrm,
  libdbusmenu,
  libdbusmenu-gtk3,
  libgbm,
  libglvnd,
  libnotify,
  libpulseaudio,
  libsecret,
  libx11,
  libxcb,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxkbcommon,
  libxrandr,
  nspr,
  nss,
  openssl,
  pango,
  systemd,
  xdg-utils,
}:
let
  pname = "t3code-morrowbyte";
  crewCodex = callPackage ./crew-codex.nix { };
  version = "0.1.11-nightly.20261118.131";
  src = fetchurl {
    url = "https://github.com/morrowbyte/t3code/releases/download/v${version}/T3-Code-${version}-x86_64.AppImage";
    sha256 = "b6a573dac5093643b6981cbf6d23a8ff1347334d494c2998e621f992e1d04849";
  };
  desktop = makeDesktopItem {
    name = pname;
    desktopName = "Crew";
    exec = "${pname} %U";
    icon = "t3code";
    startupWMClass = "T3 Code";
    categories = [
      "Development"
      "IDE"
    ];
  };
  # Runtime search path from Morrowbyte's packaging/nixos/flake.nix at this tag.
  runtimeLibraries = [
    alsa-lib
    at-spi2-core
    dbus
    expat
    glib
    libgbm
    libglvnd
    libpulseaudio
    libsecret
    libx11
    libxcb
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxkbcommon
    libxrandr
    nspr
    nss
    systemd
  ];
in
stdenv.mkDerivation {
  inherit pname version;
  src = appimageTools.extractType2 { inherit pname version src; };
  dontUnpack = true;
  dontBuild = true;
  dontWrapGApps = true;
  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
    wrapGAppsHook3
  ];
  buildInputs = runtimeLibraries ++ [
    at-spi2-atk
    cairo
    cups
    dbus-glib
    gdk-pixbuf
    gtk3
    libdrm
    libdbusmenu
    libdbusmenu-gtk3
    libnotify
    openssl
    pango
    stdenv.cc.cc.lib
  ];
  # The AppImage includes optional musl variants alongside the glibc prebuilds.
  autoPatchelfIgnoreMissingDeps = [ "libc.musl-x86_64.so.1" ];
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/${pname}" "$out/share"
    cp -a "$src"/. "$out/lib/${pname}/"
    chmod -R u+w "$out/lib/${pname}"
    cp -a "$src/usr/share/icons" "$out/share/"
    cp -r ${desktop}/share/applications "$out/share/"
    runHook postInstall
  '';
  postFixup = ''
    test -x "$out/lib/${pname}/t3code"
    makeShellWrapper "$out/lib/${pname}/t3code" "$out/bin/${pname}" \
      "''${gappsWrapperArgs[@]}" \
      --run '${python3}/bin/python3 -I ${./initialize-crew.py} || exit $?' \
      --prefix PATH : ${lib.makeBinPath [ xdg-utils ]} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath runtimeLibraries} \
      --unset NO_AT_BRIDGE \
      --add-flags "--force-renderer-accessibility" \
      --set T3CODE_DISABLE_AUTO_UPDATE true \
      --set T3CODE_DESKTOP_LINUX_URL_HANDLER_LAUNCHER /run/current-system/sw/bin/${pname}
    ln -s ${crewCodex}/bin/crew-codex "$out/bin/crew-codex"
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    export T3CODE_HOME="$TMPDIR/crew-profile"
    seed() {
      ${python3}/bin/python3 -I ${./initialize-crew.py}
    }
    seed
    test -s "$T3CODE_HOME/userdata/settings.json"
    printf '%s\n' '{"providerInstances":{"codex":{"driver":"codex","enabled":false,"config":{}}}}' > "$TMPDIR/user-choice.json"
    cp "$TMPDIR/user-choice.json" "$T3CODE_HOME/userdata/settings.json"
    seed
    cmp "$TMPDIR/user-choice.json" "$T3CODE_HOME/userdata/settings.json"
    export T3CODE_HOME="$TMPDIR/existing-crew-profile"
    mkdir -p "$T3CODE_HOME/runtime"
    seed
    test ! -e "$T3CODE_HOME/userdata/settings.json"
    runHook postInstallCheck
  '';
  meta = {
    description = "Crew desktop client by Morrowbyte";
    homepage = "https://github.com/morrowbyte/t3code";
    license = lib.licenses.mit;
    mainProgram = pname;
    platforms = [ "x86_64-linux" ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
