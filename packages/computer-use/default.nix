{
  lib,
  rustPlatform,
  makeWrapper,
  niri,
  grim,
  wtype,
  wl-clipboard,
  cua-driver ? null,
}:
rustPlatform.buildRustPackage {
  pname = "weasel-computer-use";
  version = "0.1.0";
  src = ./.;
  cargoRoot = "core";
  buildAndTestSubdir = "core";
  cargoLock.lockFile = ./core/Cargo.lock;
  # Cargo does not test path-dependency units when only the Core is selected.
  cargoTestFlags = [
    "--package"
    "weasel-computer-use-core"
    "--package"
    "weasel-computer-use-pointer"
  ];
  nativeBuildInputs = [ makeWrapper ];
  postInstall = ''
    mv "$out/bin/weasel-computer-use-core" "$out/bin/weasel-computer-use"
    wrapProgram "$out/bin/weasel-computer-use" \
      --prefix PATH : ${
        lib.makeBinPath (
          [
            niri
            grim
            wtype
            wl-clipboard
          ]
          ++ lib.optional (cua-driver != null) cua-driver
        )
      }
  '';
  meta = {
    description = "Persistent Niri desktop actor with observation guards and MCP transport";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "weasel-computer-use";
  };
}
