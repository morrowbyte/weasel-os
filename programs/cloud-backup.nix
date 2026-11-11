{ pkgs, ... }:
let
  cli = import ../packages/proton-drive-cli.nix { inherit pkgs; };
  scripts = pkgs.runCommand "weasel-cloud-backup-scripts" { } ''
    mkdir -p "$out"
    cp ${../scripts/weasel-cloud-backup.py} "$out/weasel-cloud-backup.py"
    cp ${../scripts/proton-restic-server.py} "$out/proton-restic-server.py"
    cp ${../scripts/weasel-backup-crew.py} "$out/weasel-backup-crew.py"
  '';
  backup = pkgs.writeShellApplication {
    name = "weasel-cloud-backup";
    runtimeInputs = [
      pkgs.python3
      pkgs.restic
      cli
    ];
    text = ''
      exec python3 ${scripts}/weasel-cloud-backup.py "$@" \
        --cli ${cli}/bin/proton-drive --restic ${pkgs.restic}/bin/restic \
        --excludes ${../config/backup-excludes.txt}
    '';
  };
  job = mode: {
    Unit = {
      Description = "Proton Drive ${mode} of personal files and mutable state";
      After = [ "dbus.socket" ] ++ pkgs.lib.optional (mode == "weekly") "weasel-cloud-backup.service";
      Wants = [ "dbus.socket" ];
      OnFailure = [ "weasel-cloud-backup-notify.service" ];
    };
    Service = {
      Type = "oneshot";
      ExecStart = "${backup}/bin/weasel-cloud-backup ${mode}";
      UMask = "0077";
      Nice = 19;
      IOSchedulingClass = "idle";
      TimeoutStartSec = "infinity";
      Environment = [
        "XDG_RUNTIME_DIR=%t"
        "DBUS_SESSION_BUS_ADDRESS=unix:path=%t/bus"
        "PROTON_DRIVE_LOG_LEVEL=WARNING"
      ];
    };
  };
in
{
  home.packages = [
    cli
    pkgs.restic
    backup
  ];
  systemd.user.services.weasel-cloud-backup = job "backup";
  systemd.user.services.weasel-cloud-backup-maintain = job "weekly";
  systemd.user.services.weasel-cloud-backup-notify = {
    Unit.Description = "Report failed cloud backup on the laptop desktop";
    Service = {
      Type = "oneshot";
      ExecStart = "${pkgs.libnotify}/bin/notify-send --urgency=critical 'Proton-Drive-Backup fehlgeschlagen' 'Der letzte Lauf war nicht erfolgreich. Bitte systemctl --user status weasel-cloud-backup prüfen.'";
    };
  };
  systemd.user.timers.weasel-cloud-backup = {
    Unit.Description = "Monday to Saturday incremental Proton Drive backup";
    Timer = {
      OnCalendar = "Mon..Sat *-*-* 03:30:00";
      Persistent = true;
      RandomizedDelaySec = "10m";
    };
    Install.WantedBy = [ "timers.target" ];
  };
  systemd.user.timers.weasel-cloud-backup-maintain = {
    Unit.Description = "Sunday restore point, verification and cloud backup retention";
    Timer = {
      OnCalendar = "Sun *-*-* 03:30:00";
      Persistent = true;
    };
    Install.WantedBy = [ "timers.target" ];
  };
}
