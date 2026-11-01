{ pkgs }:

pkgs.stdenvNoCC.mkDerivation rec {
  pname = "codexbar";
  version = "0.37.2";

  src = pkgs.fetchurl {
    url = "https://github.com/steipete/CodexBar/releases/download/v${version}/CodexBarCLI-v${version}-linux-musl-x86_64.tar.gz";
    hash = "sha256-giiZX1r3VMkRXgPJnlmakol33KWtqnLyTfNv1lSlmZs=";
  };

  nativeBuildInputs = [
    pkgs.gnutar
    pkgs.gzip
  ];

  unpackPhase = ''
    tar -xzf "$src"
  '';

  installPhase = ''
    install -Dm755 CodexBarCLI "$out/bin/CodexBarCLI"
    ln -s "$out/bin/CodexBarCLI" "$out/bin/codexbar"
  '';

  meta = with pkgs.lib; {
    description = "CLI for querying Codex and other AI coding usage windows";
    homepage = "https://github.com/steipete/CodexBar";
    license = licenses.mit;
    platforms = [ "x86_64-linux" ];
  };
}
