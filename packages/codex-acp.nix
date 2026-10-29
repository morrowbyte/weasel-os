{
  stdenvNoCC,
  fetchurl,
  lib,
  makeWrapper,
  nodejs,
  codexPackage,
}:
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "codex-acp";
  version = "2.4.0";
  src = fetchurl {
    url = "https://registry.npmjs.org/@agentclientprotocol/codex-acp/-/codex-acp-${finalAttrs.version}.tgz";
    hash = "sha256-hr0ZdMyTwnW6DVCIvBrCzlKQf0gAPibixnNqX3erK/Q=";
  };
  nativeBuildInputs = [ makeWrapper ];
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/codex-acp" "$out/bin"
    cp -r dist package.json "$out/lib/codex-acp/"
    makeWrapper ${nodejs}/bin/node "$out/bin/codex-acp" \
      --add-flags "$out/lib/codex-acp/dist/index.js" \
      --set CODEX_PATH ${codexPackage}/bin/codex
    runHook postInstall
  '';
  meta = {
    description = "Pinned ACP adapter using the declarative Codex app-server";
    homepage = "https://github.com/agentclientprotocol/codex-acp";
    license = lib.licenses.asl20;
    mainProgram = "codex-acp";
    platforms = [ "x86_64-linux" ];
  };
})
