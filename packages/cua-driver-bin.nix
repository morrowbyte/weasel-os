{
  stdenvNoCC,
  fetchurl,
  autoPatchelfHook,
  makeWrapper,
  lib,
  libXi,
  at-spi2-core,
  glib,
  libxkbcommon,
  wayland,
  libGL,
  fontconfig,
}:

stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "cua-driver";
  version = "0.38.0";

  src = fetchurl {
    url = "https://github.com/trycua/cua/releases/download/cua-driver-rs-v${finalAttrs.version}/cua-driver-rs-${finalAttrs.version}-linux-x86_64-binary.tar.gz";
    hash = "sha256-MauXCd2RznNnfkm58+dcbw0+TZYlwobJ5YJiv76mmN0=";
  };

  sourceRoot = ".";

  nativeBuildInputs = [
    autoPatchelfHook
    makeWrapper
  ];

  buildInputs = [
    libXi
    at-spi2-core
    glib
    libxkbcommon
    wayland
    libGL
    fontconfig
  ];

  installPhase = ''
    runHook preInstall
    install -Dm755 cua-driver "$out/bin/cua-driver"
    install -Dm755 cua-cursor-theme "$out/bin/cua-cursor-theme"
    install -Dm755 libcua_driver_sdk.so "$out/lib/libcua_driver_sdk.so"
    install -Dm644 cua_driver_node_runtime.node "$out/lib/cua_driver_node_runtime.node"
    cp -a wayland-helper "$out/lib/wayland-helper"
    wrapProgram "$out/bin/cua-driver" \
      --prefix LD_LIBRARY_PATH : "$out/lib" \
      --set-default CUA_DRIVER_RS_ENABLE_WAYLAND 1
    runHook postInstall
  '';

  meta = {
    description = "Pinned Cua background computer-use driver";
    homepage = "https://github.com/trycua/cua";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "cua-driver";
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
})
