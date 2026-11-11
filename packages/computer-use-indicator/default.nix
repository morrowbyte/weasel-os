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
      ./assets/sudo/source.png
      ./assets/sudo/question-answer-source.png
      ./assets/sudo/pet.json
      ./assets/sudo/README.md
      ./monitor-match.h
      ./pet-motion.h
      ./test-monitor-match.c
      ./test-pet-motion.c
      ./test-asset-compiler.c
      ./test-pet-presentation.c
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
      asset-compiler.c -o pet-asset-compiler $(pkg-config --cflags --libs gdk-pixbuf-2.0) -lm
    ./pet-asset-compiler assets/lumi/source.png assets/lumi/pet.png
    ./weasel-desktop-pet-renderer --validate-sheet "$PWD/assets/lumi/pet.png" 256 256
    ./pet-asset-compiler --sudo assets/sudo/source.png assets/sudo/question-answer-source.png assets/sudo/pet.png
    ./weasel-desktop-pet-renderer --validate-sheet "$PWD/assets/sudo/pet.png" 256 256
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
    mkdir -p "$out/share/weasel-desktop-pet/sudo"
    cp assets/sudo/pet.png assets/sudo/pet.json "$out/share/weasel-desktop-pet/sudo/"
    makeWrapper ${python3}/bin/python3 "$out/bin/weasel-computer-use-indicator" \
      --add-flags "$out/libexec/indicator.py" \
      --set WEASEL_INDICATOR_RENDERER "$out/libexec/weasel-desktop-pet-renderer" \
      --set WEASEL_PET_PRESENTATION 1 \
      --set WEASEL_PET_SHEET "$out/share/weasel-desktop-pet/sudo/pet.png" \
      --set WEASEL_INDICATOR_NIRI "${niri}/bin/niri" \
      --set WEASEL_INDICATOR_SYSTEMCTL "${systemd}/bin/systemctl"
    makeWrapper ${python3}/bin/python3 "$out/bin/weasel-desktop-pet" \
      --add-flags "$out/libexec/companion.py" \
      --set WEASEL_PET_RENDERER "$out/libexec/weasel-desktop-pet-renderer" \
      --set WEASEL_PET_SHEET "$out/share/weasel-desktop-pet/sudo/pet.png" \
      --set WEASEL_PET_BUILTIN_ROOT "$out/share/weasel-desktop-pet" \
      --set WEASEL_INDICATOR_NIRI "${niri}/bin/niri" \
      --set WEASEL_INDICATOR_SYSTEMCTL "${systemd}/bin/systemctl"
    runHook postInstall
  '';
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    $CC -std=c11 -O2 -Wall -Wextra -Werror test-monitor-match.c -o test-monitor-match
    ./test-monitor-match
    $CC -std=c11 -O2 -Wall -Wextra -Werror test-pet-motion.c -o test-pet-motion
    ./test-pet-motion
    $CC -std=c11 -O2 -Wall -Wextra -Werror test-asset-compiler.c -o test-asset-compiler \
      $(pkg-config --cflags --libs gdk-pixbuf-2.0) -lm
    ./test-asset-compiler
    $CC -std=c11 -O2 -Wall -Wextra -Werror test-pet-presentation.c -o test-pet-presentation \
      $(pkg-config --cflags --libs gtk+-3.0 gtk-layer-shell-0) -lm
    ./test-pet-presentation
    export WEASEL_PET_RENDERER="$PWD/weasel-desktop-pet-renderer"
    export WEASEL_PET_BUILTIN_ROOT="$PWD/assets"
    ${python3}/bin/python3 -m unittest -v test_pet_protocol
    ${python3}/bin/python3 -m unittest -v test_indicator.IndicatorLifecycle.test_crew_disconnect_or_invalid_first_projection_after_reattach_keeps_cu_scope
    ${python3}/bin/python3 -m unittest -v test_indicator.IndicatorLifecycle.test_focus_query_failure_retries_without_ending_cu_and_keeps_stop_reachable
    ${python3}/bin/python3 -m unittest -v test_indicator.IndicatorLifecycle.test_focus_retry_does_not_mask_original_target_output_loss
    runHook postCheck
  '';
  meta = {
    description = "Animated Wayland desktop pet with confirmed Computer Use privacy and Crew notifications";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "weasel-computer-use-indicator";
  };
}
