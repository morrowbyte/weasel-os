{
  config,
  lib,
  pkgs,
  pkgsUnstable,
  ...
}:
let
  repoDefaultPath = "${config.home.homeDirectory}/weasel-os";
in
{
  home.packages = [
    (import ../../scripts/ai-gen-runpod.nix { inherit pkgs; })
    (import ../../scripts/kai-codex-usage.nix { inherit pkgs; })
    (import ../../scripts/sophos-vpn.nix { inherit pkgs; })
    pkgs.deskflow
    pkgs.glab
    pkgs.krita
    pkgs.moonlight-qt
    pkgs.rustdesk-flutter
    pkgsUnstable.telegram-desktop
  ];

  programs = {
    "dank-material-shell" = {
      enable = lib.mkDefault true;
      systemd = {
        enable = lib.mkDefault false;
        restartIfChanged = lib.mkDefault true;
      };
      enableDynamicTheming = lib.mkDefault true;
    };
    vscode = {
      enable = lib.mkDefault true;
      package = lib.mkDefault pkgs.vscode.fhs;
    };
  };

  xdg.configFile."DankMaterialShell/plugins/RunPodControl" = {
    source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/dank-material-shell/plugins/RunPodControl";
    recursive = true;
    force = true;
  };

  xdg.configFile."DankMaterialShell/plugins/CodexUsage" = {
    source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/dank-material-shell/plugins/CodexUsage";
    recursive = true;
    force = true;
  };

  xdg.configFile."DankMaterialShell/plugins/SophosVPN" = {
    source = config.lib.file.mkOutOfStoreSymlink "${repoDefaultPath}/programs/dank-material-shell/plugins/SophosVPN";
    recursive = true;
    force = true;
  };

  home.activation.enableSophosVpnPlugin = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    plugin_settings="$HOME/.config/DankMaterialShell/plugin_settings.json"
    plugin_tmp="$plugin_settings.hm-sophos-vpn"
    mkdir -p "$HOME/.config/DankMaterialShell"

    if [ -f "$plugin_settings" ]; then
      ${pkgs.jq}/bin/jq '.sophosVpn.enabled = true' "$plugin_settings" > "$plugin_tmp"
    else
      printf '%s\n' '{"sophosVpn":{"enabled":true}}' > "$plugin_tmp"
    fi

    chmod 0644 "$plugin_tmp"
    mv -f "$plugin_tmp" "$plugin_settings"
  '';

  home.activation.enableSophosVpnBarWidget = lib.hm.dag.entryAfter [ "enableSophosVpnPlugin" ] ''
    dms_settings="$HOME/.config/DankMaterialShell/settings.json"
    dms_tmp="$dms_settings.hm-sophos-vpn"

    if [ -f "$dms_settings" ]; then
      ${pkgs.jq}/bin/jq '
        def widget_id: if type == "object" then .id else . end;
        def has_widget($widgets; $id): any($widgets[]?; widget_id == $id);
        .barConfigs |= map(
          if .id == "default" and (.rightWidgets | has_widget(.; "sophosVpn") | not) then
            .rightWidgets |= (map(if widget_id == "runPodControl" then [{"id":"sophosVpn","enabled":true}, .] else . end) | flatten)
          else . end
        )
      ' "$dms_settings" > "$dms_tmp"
      chmod 0644 "$dms_tmp"
      mv -f "$dms_tmp" "$dms_settings"
    fi
  '';

  home.activation.enableRunPodControlPlugin = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    settings="$HOME/.config/DankMaterialShell/plugin_settings.json"
    tmp="$settings.hm-runpod-control"
    mkdir -p "$HOME/.config/DankMaterialShell"

    if [ -f "$settings" ]; then
      ${pkgs.jq}/bin/jq '.runPodControl.enabled = true' "$settings" > "$tmp"
    else
      printf '%s\n' '{"runPodControl":{"enabled":true}}' > "$tmp"
    fi

    chmod 0644 "$tmp"
    mv -f "$tmp" "$settings"
  '';

  home.activation.enableRunPodControlBarWidget =
    lib.hm.dag.entryAfter [ "enableRunPodControlPlugin" ]
      ''
        dms_settings="$HOME/.config/DankMaterialShell/settings.json"
        dms_tmp="$dms_settings.hm-runpod-control"

        if [ -f "$dms_settings" ]; then
          ${pkgs.jq}/bin/jq '
            def has_widget($widgets): any($widgets[]?; (if type == "object" then .id else . end) == "runPodControl");
            .barConfigs |= map(
              if .id == "default" and (.rightWidgets | has_widget(.) | not) then
                .rightWidgets |= (map(if (type == "object" and .id == "controlCenterButton") or . == "controlCenterButton" then [{"id":"runPodControl","enabled":true}, .] else . end) | flatten)
              else . end
            )
          ' "$dms_settings" > "$dms_tmp"
          chmod 0644 "$dms_tmp"
          mv -f "$dms_tmp" "$dms_settings"
        fi
      '';

  home.activation.enableCodexUsagePlugin = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        plugin_settings="$HOME/.config/DankMaterialShell/plugin_settings.json"
        plugin_tmp="$plugin_settings.hm-codex-usage"
        mkdir -p "$HOME/.config/DankMaterialShell"

        if [ -f "$plugin_settings" ]; then
          ${pkgs.jq}/bin/jq '.codexUsage.enabled = true' "$plugin_settings" > "$plugin_tmp"
        else
          printf '%s
    ' '{"codexUsage":{"enabled":true}}' > "$plugin_tmp"
        fi

        chmod 0644 "$plugin_tmp"
        mv -f "$plugin_tmp" "$plugin_settings"
  '';

  home.activation.enableCodexUsageBarWidget = lib.hm.dag.entryAfter [ "enableCodexUsagePlugin" ] ''
    dms_settings="$HOME/.config/DankMaterialShell/settings.json"
    dms_tmp="$dms_settings.hm-codex-usage"

    if [ -f "$dms_settings" ]; then
      ${pkgs.jq}/bin/jq '
        def widget_id: if type == "object" then .id else . end;
        def has_widget($widgets; $id): any($widgets[]?; widget_id == $id);
        .barConfigs |= map(
          if .id == "default" and (.rightWidgets | has_widget(.; "codexUsage") | not) then
            .rightWidgets |= (map(if widget_id == "runPodControl" then [{"id":"codexUsage","enabled":true}, .] else . end) | flatten)
          else . end
        )
      ' "$dms_settings" > "$dms_tmp"
      chmod 0644 "$dms_tmp"
      mv -f "$dms_tmp" "$dms_settings"
    fi
  '';

  systemd.user.services.kai-codex-usage = {
    Unit.Description = "Refresh cached Codex usage for DMS and Kai dashboard";
    Service = {
      Type = "oneshot";
      ExecStart = "${
        import ../../scripts/kai-codex-usage.nix { inherit pkgs; }
      }/bin/kai-codex-usage update";
    };
  };

  systemd.user.timers.kai-codex-usage = {
    Unit.Description = "Refresh cached Codex usage every five minutes";
    Timer = {
      OnBootSec = "2m";
      OnUnitActiveSec = "5m";
      Unit = "kai-codex-usage.service";
    };
    Install.WantedBy = [ "timers.target" ];
  };

}
