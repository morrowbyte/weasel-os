{
  appimageTools,
  asar,
  coreutils,
  fetchurl,
  gnugrep,
  lib,
  makeWrapper,
  nodejs,
  runCommand,
  writeShellApplication,
}:

let
  pname = "vesktop";
  version = "1.6.8";

  src = fetchurl {
    url = "https://github.com/Vencord/Vesktop/releases/download/v${version}/Vesktop-${version}.AppImage";
    hash = "sha256-F1dCbZN6aYeuQwbnT6wv5tPE69SoiVzjrWxu585hDYQ=";
  };

  appimageContents = appimageTools.extractType2 {
    inherit pname version src;
  };

  # Vesktop downloads Vencord's runtime beside user settings. If that first-run
  # fetch is absent or partial, Vesktop silently degrades to plain Discord.
  # Fixed-output fallbacks keep Vencord available without changing user state.
  vencordDesktopMain = fetchurl {
    url = "https://github.com/Vendicated/Vencord/releases/download/devbuild/vencordDesktopMain.js";
    hash = "sha256-UcbheYz7qePlqs8LdJv3jDTrV411GrJRG7NUdNlA8kk=";
  };
  vencordDesktopPreload = fetchurl {
    url = "https://github.com/Vendicated/Vencord/releases/download/devbuild/vencordDesktopPreload.js";
    hash = "sha256-r65nEg/hpYInHS5VF+wa4Nqn3PkyzMk0MdbCwGfFd9I=";
  };
  vencordDesktopRenderer = fetchurl {
    url = "https://github.com/Vendicated/Vencord/releases/download/devbuild/vencordDesktopRenderer.js";
    hash = "sha256-ANdDKsJP92G3AFnmI2DtpkW7L6EOnlCt9qKqqZZMzXU=";
  };
  vencordDesktopRendererCss = fetchurl {
    url = "https://github.com/Vendicated/Vencord/releases/download/devbuild/vencordDesktopRenderer.css";
    hash = "sha256-QyzAhNM62s4FDk5vfpSW6BzLBEYhfv8oEgzCSrRLpfU=";
  };

  vencordBootstrap = writeShellApplication {
    name = "vesktop-vencord-bootstrap";
    runtimeInputs = [
      coreutils
      gnugrep
    ];
    text = ''
      user_data_dir="''${VENCORD_USER_DATA_DIR:-''${XDG_CONFIG_HOME:-$HOME/.config}/vesktop}"
      vencord_dir="$user_data_dir/sessionData/vencordFiles"

      if [ ! -s "$vencord_dir/package.json" ] \
        || [ ! -s "$vencord_dir/vencordDesktopMain.js" ] \
        || [ ! -s "$vencord_dir/vencordDesktopPreload.js" ] \
        || [ ! -s "$vencord_dir/vencordDesktopRenderer.js" ] \
        || [ ! -s "$vencord_dir/vencordDesktopRenderer.css" ] \
        || ! grep -Fq 'VoiceMessages' "$vencord_dir/vencordDesktopMain.js"; then
        mkdir -p "$vencord_dir"
        install -Dm600 ${vencordDesktopMain} "$vencord_dir/vencordDesktopMain.js"
        install -Dm600 ${vencordDesktopPreload} "$vencord_dir/vencordDesktopPreload.js"
        install -Dm600 ${vencordDesktopRenderer} "$vencord_dir/vencordDesktopRenderer.js"
        install -Dm600 ${vencordDesktopRendererCss} "$vencord_dir/vencordDesktopRenderer.css"
        printf '{}\n' > "$vencord_dir/package.json"
        chmod 0600 "$vencord_dir/package.json"
      fi
    '';
  };

  # Vesktop 1.6.8 asks its renderer to open a second, custom picker after
  # xdg-desktop-portal has already selected a Wayland source.  When that
  # renderer command rejects, the error is swallowed and Electron receives an
  # empty capture result.  Keep the working portal source and hand it directly
  # to Electron instead.  The patcher deliberately checks the exact bundled
  # code so a future Vesktop update cannot silently lose this workaround.
  patchedAppimageContents =
    runCommand "${pname}-${version}-wayland-patched"
      {
        nativeBuildInputs = [
          asar
          nodejs
        ];
      }
      ''
        mkdir -p "$out"
        cp -a ${appimageContents}/. "$out/"
        chmod -R u+w "$out/resources"

        mkdir app
        asar extract "$out/resources/app.asar" app
        # The 1.6.8 bundle only renames this handler's Wayland flag (vy -> Iy).
        # Retain the existing exact-one-match patch and the same capture behavior.
        substitute ${../scripts/patch-vesktop-wayland-capture.mjs} patch-vesktop-wayland-capture.mjs \
          --replace-fail 'if(vy){let c=i[0];' 'if(Iy){let c=i[0];' \
          --replace-fail 'Vesktop 1.6.7' 'Vesktop 1.6.8'
        node patch-vesktop-wayland-capture.mjs app/dist/js/main.js

        rm -f "$out/resources/app.asar"
        rm -rf "$out/resources/app.asar.unpacked"
        asar pack app "$out/resources/app.asar" --unpack '**/*.node'
      '';
in
appimageTools.wrapAppImage {
  inherit pname version;
  src = patchedAppimageContents;

  nativeBuildInputs = [ makeWrapper ];

  extraInstallCommands = ''
    install -Dm444 \
      ${patchedAppimageContents}/vesktop.desktop \
      "$out/share/applications/vesktop.desktop"
    substituteInPlace "$out/share/applications/vesktop.desktop" \
      --replace-fail 'Exec=AppRun %U' 'Exec=vesktop %U'

    cp -r ${patchedAppimageContents}/usr/share/icons "$out/share/"

    install -Dm444 ${vencordDesktopMain} \
      "$out/share/vesktop/vencord/vencordDesktopMain.js"
    install -Dm444 ${vencordDesktopPreload} \
      "$out/share/vesktop/vencord/vencordDesktopPreload.js"
    install -Dm444 ${vencordDesktopRenderer} \
      "$out/share/vesktop/vencord/vencordDesktopRenderer.js"
    install -Dm444 ${vencordDesktopRendererCss} \
      "$out/share/vesktop/vencord/vencordDesktopRenderer.css"

    wrapProgram "$out/bin/vesktop" \
      --run ${lib.escapeShellArg "${vencordBootstrap}/bin/vesktop-vencord-bootstrap"}
  '';

  meta = {
    description = "Vesktop Discord client with current Wayland screenshare fixes";
    homepage = "https://github.com/Vencord/Vesktop";
    license = lib.licenses.gpl3Plus;
    mainProgram = pname;
    platforms = [ "x86_64-linux" ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
