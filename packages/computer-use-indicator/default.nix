{
  lib,
  stdenv,
  pkg-config,
  gtk3,
  gtk-layer-shell,
  makeWrapper,
  python3,
  niri,
  systemd,
}:
stdenv.mkDerivation {
  pname = "weasel-computer-use-indicator";
  version = "0.1.0";
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./renderer.c
      ./pet-renderer.c
      ./asset-compiler.c
      ./assets/lumi/source.png
      ./assets/lumi/pet.json
      ./assets/lumi/README.md
      ./monitor-match.h
      ./test-monitor-match.c
      ./indicator.py
      ./companion.py
      ./pet_protocol.py
      ./pet_bridge.py
      ./output_probe.py
      ./test_pet_protocol.py
      ./test_indicator.py
    ];
  };
  strictDeps = true;
  nativeBuildInputs = [
    pkg-config
    makeWrapper
  ];
  buildInputs = [
    gtk3
    gtk-layer-shell
  ];
  buildPhase = ''
    runHook preBuild
    $CC -std=c11 -O2 -Wall -Wextra -Werror \
      renderer.c -o weasel-computer-use-indicator-renderer \
      $(pkg-config --cflags --libs gtk+-3.0 gtk-layer-shell-0)
    $CC -std=c11 -O2 -Wall -Wextra -Werror \
      pet-renderer.c -o weasel-desktop-pet-renderer \
      $(pkg-config --cflags --libs gtk+-3.0 gtk-layer-shell-0) -lm
    $CC -std=c11 -O2 -Wall -Wextra -Werror \
      asset-compiler.c -o pet-asset-compiler $(pkg-config --cflags --libs gdk-pixbuf-2.0)
    ./pet-asset-compiler assets/lumi/source.png assets/lumi/pet.png
    ./weasel-desktop-pet-renderer --validate-sheet "$PWD/assets/lumi/pet.png" 256 256
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin" "$out/libexec"
    cp weasel-computer-use-indicator-renderer "$out/libexec/"
    cp weasel-desktop-pet-renderer "$out/libexec/"
    cp indicator.py "$out/libexec/indicator.py"
    cp companion.py pet_protocol.py pet_bridge.py output_probe.py "$out/libexec/"
    mkdir -p "$out/share/weasel-desktop-pet/lumi"
    cp assets/lumi/pet.png assets/lumi/pet.json "$out/share/weasel-desktop-pet/lumi/"
    makeWrapper ${python3}/bin/python3 "$out/bin/weasel-computer-use-indicator" \
      --add-flags "$out/libexec/indicator.py" \
      --set WEASEL_INDICATOR_RENDERER "$out/libexec/weasel-desktop-pet-renderer" \
      --set WEASEL_PET_PRESENTATION 1 \
      --set WEASEL_PET_SHEET "$out/share/weasel-desktop-pet/lumi/pet.png" \
      --set WEASEL_INDICATOR_NIRI "${niri}/bin/niri" \
      --set WEASEL_INDICATOR_SYSTEMCTL "${systemd}/bin/systemctl"
    makeWrapper ${python3}/bin/python3 "$out/bin/weasel-desktop-pet" \
      --add-flags "$out/libexec/companion.py" \
      --set WEASEL_PET_RENDERER "$out/libexec/weasel-desktop-pet-renderer" \
      --set WEASEL_PET_SHEET "$out/share/weasel-desktop-pet/lumi/pet.png" \
      --set WEASEL_INDICATOR_NIRI "${niri}/bin/niri" \
      --set WEASEL_INDICATOR_SYSTEMCTL "${systemd}/bin/systemctl"
    runHook postInstall
  '';
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    $CC -std=c11 -O2 -Wall -Wextra -Werror test-monitor-match.c -o test-monitor-match
    ./test-monitor-match
    ${python3}/bin/python3 -m unittest -v test_pet_protocol
    ${python3}/bin/python3 -m unittest -v test_indicator.IndicatorLifecycle.test_crew_disconnect_or_invalid_first_projection_after_reattach_keeps_cu_scope
    runHook postCheck
  '';
  meta = {
    description = "Animated Wayland desktop pet with confirmed Computer Use privacy and Crew notifications";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "weasel-computer-use-indicator";
  };
}
