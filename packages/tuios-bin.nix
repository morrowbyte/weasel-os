{
  stdenvNoCC,
  fetchurl,
  lib,
}:

stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "tuios";
  version = "0.10.1";

  src = fetchurl {
    url = "https://github.com/Gaurav-Gosain/tuios/releases/download/v${finalAttrs.version}/tuios_${finalAttrs.version}_Linux_x86_64.tar.gz";
    hash = "sha256-haS/M5qaZ5Pgs9FavczZqWyo0v92ByAH7Jm+MfGhal8=";
  };

  sourceRoot = ".";

  installPhase = ''
    runHook preInstall
    install -Dm755 tuios "$out/bin/tuios"
    runHook postInstall
  '';

  meta = {
    description = "Terminal multiplexer and window manager";
    homepage = "https://github.com/Gaurav-Gosain/tuios";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "tuios";
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
})
