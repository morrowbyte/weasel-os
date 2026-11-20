{
  systemd.user.targets.weasel-vm-graphical = {
    description = "Niri session on the VM display";
    bindsTo = [ "graphical-session.target" ];
    wants = [
      "graphical-session-pre.target"
      "xdg-desktop-autostart.target"
    ];
    after = [ "graphical-session-pre.target" ];
    before = [
      "graphical-session.target"
      "xdg-desktop-autostart.target"
    ];
  };
}
