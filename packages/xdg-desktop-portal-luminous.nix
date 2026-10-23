{ pkgs, pkgsUnstable }:
pkgsUnstable.xdg-desktop-portal-luminous.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./luminous-remote-permission.patch ];
  postPatch = (old.postPatch or "") + ''
    substituteInPlace src/remotedesktop.rs \
      --replace-fail '@permissionDialog@' '${pkgs.yad}/bin/yad'
  '';
  # Upstream's Meson command otherwise lets Cargo use every laptop core.
  preBuild = (old.preBuild or "") + ''
    export CARGO_BUILD_JOBS="$NIX_BUILD_CORES"
  '';
})
