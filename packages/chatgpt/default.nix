{
  alsa-lib,
  asar,
  at-spi2-atk,
  at-spi2-core,
  autoPatchelfHook,
  cairo,
  cups,
  dbus,
  expat,
  fetchurl,
  gdk-pixbuf,
  glib,
  glibc,
  gtk3,
  lib,
  libdrm,
  libgbm,
  libglvnd,
  libnotify,
  libpulseaudio,
  libsecret,
  libusb1,
  libxkbcommon,
  makeWrapper,
  mesa,
  nspr,
  nss,
  pango,
  qt5,
  qt6,
  rpmextract,
  stdenv,
  systemd,
  wrapGAppsHook3,
  xdg-utils,
  xorg,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "chatgpt";
  version = "26.1023.51421";

  # The official documentation currently exposes a mutable `latest` link. The
  # versioned object below returned `Cache-Control: immutable` and was hashed
  # independently before it was pinned here.
  src = fetchurl {
    url = "https://persistent.oaistatic.com/codex-app-prod/linux/rpm/x86_64/chatgpt-${finalAttrs.version}-1.x86_64.rpm";
    hash = "sha256-38hXtUnGipCQDvfuuuk6HYnAnHy2NN0mCysXwL0+5WI=";
  };

  dontUnpack = true;
  dontBuild = true;

  nativeBuildInputs = [
    asar
    rpmextract
    autoPatchelfHook
    makeWrapper
    wrapGAppsHook3
  ];

  buildInputs = [
    alsa-lib
    at-spi2-atk
    at-spi2-core
    cairo
    cups
    dbus
    expat
    gdk-pixbuf
    glib
    gtk3
    libdrm
    libgbm
    libnotify
    libpulseaudio
    libsecret
    libusb1
    libxkbcommon
    mesa
    nspr
    nss
    pango
    qt5.qtbase.out
    qt6.qtbase.out
    systemd
    xorg.libX11
    xorg.libXcomposite
    xorg.libXdamage
    xorg.libXext
    xorg.libXfixes
    xorg.libXrandr
  ];

  # The payload carries both glibc and musl prebuilds. On NixOS it selects the
  # glibc variants; leave the unused musl objects untouched.
  autoPatchelfIgnoreMissingDeps = [ "libc.musl-x86_64.so.1" ];

  installPhase = ''
    runHook preInstall

    rpmextract "$src"
    mkdir -p "$out/lib"
    cp -a usr/lib/chatgpt "$out/lib/"

    # autoPatchelf can move PT_INTERP beyond detect-libc's 2048-byte ELF
    # probe. Its next probe assumes /usr/bin/ldd, absent on NixOS; falling
    # through to process.report.getReport() crashes Electron's Git worker.
    # Point that filesystem probe at the glibc used by this package.
    asar extract "$out/lib/chatgpt/resources/app.asar" app
    substituteInPlace app/node_modules/@parcel/watcher/node_modules/detect-libc/lib/filesystem.js \
      --replace-fail "const LDD_PATH = '/usr/bin/ldd';" \
      "const LDD_PATH = '${lib.getBin glibc}/bin/ldd';"
    # Keep native modules and their supporting files available to dlopen.
    asar pack app "$out/lib/chatgpt/resources/app.asar" --unpack-dir node_modules

    install -Dm644 usr/share/applications/chatgpt.desktop \
      "$out/share/applications/chatgpt.desktop"
    install -Dm644 usr/share/pixmaps/chatgpt.png \
      "$out/share/pixmaps/chatgpt.png"

    runHook postInstall
  '';

  postFixup = ''
    # Nix supplies all runtime libraries through the closure. Do not install or
    # execute the RPM's scriptlets: those register OpenAI's DNF repository,
    # write a GPG key below /etc, and enable imperative package-manager updates.
    makeWrapper "$out/lib/chatgpt/ChatGPT" "$out/bin/chatgpt" \
      --prefix PATH : ${lib.makeBinPath [ xdg-utils ]} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ libglvnd ]} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ libpulseaudio ]}
  '';

  meta = {
    description = "Official OpenAI ChatGPT desktop application with Codex support";
    homepage = "https://developers.openai.com/codex/app";
    license = lib.licenses.unfree;
    mainProgram = "chatgpt";
    platforms = [ "x86_64-linux" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
})
