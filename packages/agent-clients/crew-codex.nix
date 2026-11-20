{
  lib,
  stdenvNoCC,
  fetchurl,
  jq,
}:

# The Linux x64 asset pinned by CodexInstallation.ts in Crew .131.
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "crew-codex";
  version = "0.163.0-morrowbyte.2";
  src = fetchurl {
    url = "https://github.com/morrowbyte/t3code/releases/download/codex-runtime-${finalAttrs.version}/codex-package-x86_64-unknown-linux-musl.tar.gz";
    sha256 = "6d517c9784c97636095974a929ef24e38567f2b21174c44d4c13fb9d699c2981";
  };
  dontUnpack = true;
  dontBuild = true;
  dontFixup = true;
  nativeBuildInputs = [ jq ];
  installPhase = ''
    runHook preInstall
    runtime="$out/libexec/codex"
    mkdir -p "$runtime" "$out/bin"
    tar -xzf "$src" -C "$runtime"
    jq -e --arg version "$version" '
      .layoutVersion == 1 and .version == $version and
      .target == "x86_64-unknown-linux-musl" and .entrypoint == "bin/codex"
    ' "$runtime/codex-package.json" >/dev/null
    test -x "$runtime/bin/codex-code-mode-host"
    test -x "$runtime/codex-path/rg"
    ln -s "$runtime/bin/codex" "$out/bin/crew-codex"
    runHook postInstall
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    export HOME="$TMPDIR/crew-codex-home"
    mkdir -p "$HOME"
    test "$("$out/bin/crew-codex" --version)" = "codex-cli $version"
    "$out/bin/crew-codex" app-server --help >/dev/null
    "$out/libexec/codex/codex-path/rg" --version >/dev/null
    runHook postInstallCheck
  '';
  meta = {
    description = "Local Codex provider runtime for Morrowbyte Crew";
    license = lib.licenses.asl20;
    mainProgram = "crew-codex";
    platforms = [ "x86_64-linux" ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
})
