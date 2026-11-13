{ pkgs, pkgsUnstable }:
let
  # 0.1.22 adds restore data and separate desktop-space coordinates. Preserve
  # the exact existing consent hunk and rebase only the keyboard-only branch.
  legacyPatch = builtins.readFile ./luminous-remote-permission.patch;
  permissionHunks = pkgs.lib.splitString "@@ -457,7 +477,25 @@" legacyPatch;
  remotePermissionPatch =
    assert builtins.length permissionHunks == 2;
    pkgs.writeText "luminous-remote-permission.patch" (
      builtins.head permissionHunks
      + ''
        @@ -421,7 +421,29 @@
                     wl_output,
                     output_name,
                     ..
        -        } = if let Some(RestoreData {
        +        } = if !screen_share_enabled
        +            && device_type == DeviceType::Keyboard
        +        {
        +            // Keyboard-only sessions need no monitor coordinates or picker.
        +            let outputs = connection.get_all_outputs();
        +            let output = outputs.first().ok_or_else(|| {
        +                zbus::fdo::Error::Failed("No Wayland output available".into())
        +            })?;
        +            let libwayshot::Size {
        +                width: space_width,
        +                height: space_height,
        +            } = space_size(&connection);
        +            RemoteInfo {
        +                x: 0,
        +                y: 0,
        +                space_width,
        +                space_height,
        +                width: output.logical_region.inner.size.width,
        +                height: output.logical_region.inner.size.height,
        +                output_name: output.name.to_owned(),
        +                wl_output: output.wl_output.clone(),
        +            }
        +        } else if let Some(RestoreData {
                     vendor_name,
                     version,
                     data,
      ''
    );
in
pkgsUnstable.xdg-desktop-portal-luminous.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ remotePermissionPatch ];
  postPatch = (old.postPatch or "") + ''
    substituteInPlace src/remotedesktop.rs \
      --replace-fail '@permissionDialog@' '${pkgs.yad}/bin/yad'
  '';
  # Upstream's Meson command otherwise lets Cargo use every laptop core.
  preBuild = (old.preBuild or "") + ''
    export CARGO_BUILD_JOBS="$NIX_BUILD_CORES"
  '';
})
