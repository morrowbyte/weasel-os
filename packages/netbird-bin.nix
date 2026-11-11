{
  lib,
  stdenvNoCC,
  fetchurl,
}:

stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "netbird-client-bin";
  version = "0.82.1";

  src = fetchurl {
    url = "https://github.com/netbirdio/netbird/releases/download/v${finalAttrs.version}/netbird_${finalAttrs.version}_linux_amd64.tar.gz";
    hash = "sha256-yPELid+gG4vK1ewltmaoPjUb5OIut4S4IkeLkyajVCM=";
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
