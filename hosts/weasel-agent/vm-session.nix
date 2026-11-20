{
  pkgs,
  niri ? pkgs.niri,
}:
let
  ready = pkgs.writeShellScript "weasel-vm-desktop-ready" ''
    set -eu
    export XDG_CURRENT_DESKTOP=niri XDG_SESSION_TYPE=wayland
    ${pkgs.dbus}/bin/dbus-update-activation-environment --systemd \
      WAYLAND_DISPLAY NIRI_SOCKET XDG_CURRENT_DESKTOP XDG_SESSION_TYPE
    exec ${pkgs.systemd}/bin/systemctl --user start weasel-vm-graphical.target
  '';
in
# Software scanout for the VM. The child compositor chooses the windowed
# backend from Weston's socket and passes its own socket to its applications.
pkgs.writeShellScript "weasel-vm-session" ''
  export XDG_CURRENT_DESKTOP=niri XDG_SESSION_TYPE=wayland
  trap '${pkgs.systemd}/bin/systemctl --user stop weasel-vm-graphical.target' EXIT
  ${pkgs.weston}/bin/weston \
    --backend=drm --renderer=pixman --shell=kiosk --idle-time=0 \
    --socket=weasel-vm-display --no-config -- ${niri}/bin/niri -- ${ready}
''
