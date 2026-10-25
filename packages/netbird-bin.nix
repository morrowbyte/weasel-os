{
  lib,
  stdenvNoCC,
  fetchurl,
}:

stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "netbird-client-bin";
  version = "0.81.4";

  src = fetchurl {
    url = "https://github.com/netbirdio/netbird/releases/download/v${finalAttrs.version}/netbird_${finalAttrs.version}_linux_amd64.tar.gz";
    hash = "sha256-6bPX45ZYTdwlE9ULyRaP5LaonBpScwjB5RMxx6HmcWU=";
  };

  dontUnpack = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    tar -xzf "$src"
    install -Dm755 netbird "$out/bin/netbird"
    runHook postInstall
  '';

  meta = {
    description = "Pinned NetBird mesh VPN client binary";
    homepage = "https://netbird.io";
    license = lib.licenses.bsd3;
    mainProgram = "netbird";
    platforms = [ "x86_64-linux" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
})
