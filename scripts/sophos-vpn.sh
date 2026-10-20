#!/usr/bin/env bash
set -euo pipefail

umask 077
ulimit -S -c 0 2>/dev/null || true

CONNECTION_NAME=${SOPHOS_VPN_CONNECTION_NAME:-Sophos VPN}
SECRET_SERVICE=${SOPHOS_VPN_SECRET_SERVICE:-sophos-vpn}
SECRET_ACCOUNT=${SOPHOS_VPN_SECRET_ACCOUNT:-default}
OPENVPN_ONLY=${SOPHOS_VPN_OPENVPN_ONLY:-0}
USE_TOTP=${SOPHOS_VPN_USE_TOTP:-1}
DEFAULT_IMPORT_USERNAME=${SOPHOS_VPN_USERNAME:-}
NMCLI_BIN=${NMCLI_BIN:-nmcli}
SECRET_TOOL_BIN=${SECRET_TOOL_BIN:-secret-tool}
SSH_BIN=${SSH_BIN:-ssh}
SS_BIN=${SS_BIN:-ss}
IP_BIN=${IP_BIN:-ip}
TIMEOUT_BIN=${TIMEOUT_BIN:-timeout}
BRIDGE_SSH_HOST=${SOPHOS_VPN_BRIDGE_SSH_HOST:-blain-ai}
BRIDGE_LOCAL_ADDRESS=${SOPHOS_VPN_BRIDGE_LOCAL_ADDRESS:-127.0.0.1}
BRIDGE_LOCAL_PORT=${SOPHOS_VPN_BRIDGE_LOCAL_PORT:-13389}
BRIDGE_TARGET_HOST=${SOPHOS_VPN_BRIDGE_TARGET_HOST:-10.145.5.50}
BRIDGE_TARGET_PORT=${SOPHOS_VPN_BRIDGE_TARGET_PORT:-3389}
BRIDGE_CONTROL_SOCKET=${SOPHOS_VPN_BRIDGE_CONTROL_SOCKET:-${XDG_RUNTIME_DIR:-/tmp}/sophos-vpn/rdp-bridge.sock}
STREAM_HOST=${SOPHOS_VPN_STREAM_HOST:-$BRIDGE_TARGET_HOST}
STREAM_CONTROL_PORT=${SOPHOS_VPN_STREAM_CONTROL_PORT:-47984}
STREAM_ROUTE_DEVICE=${SOPHOS_VPN_STREAM_ROUTE_DEVICE:-tailscale0}
STREAM_PROBE_TIMEOUT=${SOPHOS_VPN_STREAM_PROBE_TIMEOUT:-2}

usage() {
  cat <<'EOF'
Usage:
  sophos-vpn status
  sophos-vpn connect
  sophos-vpn disconnect
  sophos-vpn bridge-connect
  sophos-vpn bridge-disconnect
  sophos-vpn import <profile.ovpn> [--name <connection-name>] [--username <vpn-username>]
  sophos-vpn store-password
  sophos-vpn configure <username>

Notes:
  - connect reads the 6-digit TOTP from stdin when stdin is not a TTY.
  - SOPHOS_VPN_USE_TOTP=0 connects with the password only.
  - SOPHOS_VPN_OPENVPN_ONLY=1 disables legacy bridge and streaming checks.
  - otherwise connect prompts on /dev/tty without echo.
  - the base password lives in Secret Service under:
      service=sophos-vpn account=default kind=base-password
EOF
}

json_output() {
  jq -n "$@"
}

json_status() {
  local state=$1
  local connected=$2
  local has_secret=$3
  local device=$4
  local ip4=$5
  local message=$6
  if [[ $OPENVPN_ONLY == 1 ]]; then
    local vpn_username
    vpn_username=$("$NMCLI_BIN" -g vpn.user-name connection show id "$CONNECTION_NAME" 2>/dev/null || true)
    # shellcheck disable=SC2016
    json_output --arg connection "$CONNECTION_NAME" --arg state "$state" \
      --arg device "$device" --arg ip4 "$ip4" --arg message "$message" \
      --arg username "$vpn_username" --argjson connected "$connected" --argjson has_secret "$has_secret" \
      '{ok:($state != "error"), connection:$connection, state:$state, connected:$connected,
        vpn_connected:$connected, device:$device, ip4:$ip4, username:$username,
        has_base_password:$has_secret, message:$message}'
    return
  fi
  local bar_text detail_text severity
  local bridge_connected=false
  local bridge_managed=false
  local stream_route_ready=false
  local stream_tcp_reachable=false
  local stream_status=route-missing

  if bridge_listener_active; then
    bridge_connected=true
  fi
  if bridge_managed_active; then
    bridge_managed=true
  fi
  if stream_route_ready; then
    stream_route_ready=true
    stream_status=control-unreachable
    if stream_tcp_reachable; then
      stream_tcp_reachable=true
      stream_status=control-reachable
    fi
  fi

  if [[ $connected == true && $bridge_connected == true ]]; then
    state=conflict
  elif [[ $connected != true && $bridge_connected == true ]]; then
    state=bridge-connected
  fi

  case "$state" in
    connected)
      bar_text="VPN ON"
      detail_text=${ip4:+$device $ip4}
      detail_text=${detail_text:-Connected}
      severity="ok"
      ;;
    bridge-connected)
      bar_text="RDP ON"
      if [[ $bridge_managed == true ]]; then
        detail_text="SSH bridge via $BRIDGE_SSH_HOST"
      else
        detail_text="SSH bridge active externally"
      fi
      severity="ok"
      ;;
    conflict)
      bar_text="VPN + RDP"
      detail_text="Both paths are active"
      severity="error"
      ;;
    connecting)
      bar_text="VPN ..."
      detail_text="Connecting"
      severity="warn"
      ;;
    missing)
      bar_text="VPN !"
      detail_text="Connection missing"
      severity="error"
      ;;
    error)
      bar_text="VPN !"
      detail_text=${message:-Error}
      severity="error"
      ;;
    *)
      bar_text="VPN OFF"
      detail_text="Disconnected"
      severity="inactive"
      ;;
  esac

  # shellcheck disable=SC2016
  json_output \
    --arg connection "$CONNECTION_NAME" \
    --arg state "$state" \
    --arg device "$device" \
    --arg ip4 "$ip4" \
    --arg message "$message" \
    --arg bar_text "$bar_text" \
    --arg detail_text "$detail_text" \
    --arg severity "$severity" \
    --arg bridge_host "$BRIDGE_SSH_HOST" \
    --arg bridge_local "$BRIDGE_LOCAL_ADDRESS:$BRIDGE_LOCAL_PORT" \
    --arg bridge_target "$BRIDGE_TARGET_HOST:$BRIDGE_TARGET_PORT" \
    --arg stream_host "$STREAM_HOST" \
    --arg stream_control "$STREAM_HOST:$STREAM_CONTROL_PORT" \
    --arg stream_route_device "$STREAM_ROUTE_DEVICE" \
    --arg stream_status "$stream_status" \
    --argjson connected "$connected" \
    --argjson has_secret "$has_secret" \
    --argjson bridge_connected "$bridge_connected" \
    --argjson bridge_managed "$bridge_managed" \
    --argjson stream_route_ready "$stream_route_ready" \
    --argjson stream_tcp_reachable "$stream_tcp_reachable" \
    '{
      ok: ($state != "error" and $state != "conflict"),
      connection: $connection,
      connected: $connected,
      vpn_connected: $connected,
      bridge_connected: $bridge_connected,
      bridge_managed: $bridge_managed,
      bridge_host: $bridge_host,
      bridge_local: $bridge_local,
      bridge_target: $bridge_target,
      stream_host: $stream_host,
      stream_control: $stream_control,
      stream_route_device: $stream_route_device,
      stream_route_ready: $stream_route_ready,
      stream_tcp_reachable: $stream_tcp_reachable,
      stream_udp_verified: false,
      stream_status: $stream_status,
      state: $state,
      device: $device,
      ip4: $ip4,
      has_base_password: $has_secret,
      bar_text: $bar_text,
      detail_text: $detail_text,
      severity: $severity,
      message: $message,
      available_actions:
        if $connected then ["disconnect","bridge-connect","status"]
        elif $bridge_connected then ["bridge-disconnect","connect","status"]
        else ["connect","bridge-connect","status"]
        end
    }'
}

json_error() {
  local action=$1
  local message=$2
  # shellcheck disable=SC2016
  json_output \
    --arg action "$action" \
    --arg message "$message" \
    --arg connection "$CONNECTION_NAME" \
    '{ok:false, action:$action, connection:$connection, state:"error", message:$message, bar_text:"VPN !", severity:"error"}'
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

require_bins() {
  command_exists "$NMCLI_BIN" || {
    json_error "bootstrap" "nmcli not found" >&2
    exit 127
  }
  command_exists "$SECRET_TOOL_BIN" || {
    json_error "bootstrap" "secret-tool not found" >&2
    exit 127
  }
  command_exists "$SSH_BIN" || {
    json_error "bootstrap" "ssh not found" >&2
    exit 127
  }
  command_exists "$SS_BIN" || {
    json_error "bootstrap" "ss not found" >&2
    exit 127
  }
  command_exists "$IP_BIN" || {
    json_error "bootstrap" "ip not found" >&2
    exit 127
  }
  command_exists "$TIMEOUT_BIN" || {
    json_error "bootstrap" "timeout not found" >&2
    exit 127
  }
  command_exists jq || {
    printf '%s\n' '{"ok":false,"action":"bootstrap","state":"error","message":"jq not found"}' >&2
    exit 127
  }
}

secret_lookup() {
  local secret
  secret=$("$SECRET_TOOL_BIN" lookup service "$SECRET_SERVICE" account "$SECRET_ACCOUNT" kind base-password 2>/dev/null || true)
  # Retain the existing Sophos credential without sharing it with other profiles.
  if [[ -z $secret && $SECRET_ACCOUNT == "connection:Sophos VPN" ]]; then
    secret=$("$SECRET_TOOL_BIN" lookup service "$SECRET_SERVICE" account default kind base-password 2>/dev/null || true)
  fi
  printf '%s' "$secret"
}

has_secret() {
  [[ -n $(secret_lookup) ]]
}

read_hidden_line() {
  local prompt=$1
  local value=
  if [[ -t 0 ]]; then
    printf '%s' "$prompt" > /dev/tty
    IFS= read -r -s value < /dev/tty || true
    printf '\n' > /dev/tty
  else
    IFS= read -r value || true
  fi
  printf '%s' "$value"
}

ensure_connection_exists() {
  "$NMCLI_BIN" -t -f NAME,TYPE connection show 2>/dev/null | grep -Fxq "$CONNECTION_NAME:vpn"
}

active_device() {
  "$NMCLI_BIN" -t -f NAME,TYPE,DEVICE connection show --active 2>/dev/null | awk -F: -v target="$CONNECTION_NAME" '$1 == target && $2 == "vpn" { print $3; exit }'
}

active_ip4() {
  local device=$1
  [[ -n $device ]] || return 0
  "$NMCLI_BIN" -g IP4.ADDRESS device show "$device" 2>/dev/null | paste -sd, - | sed 's#/.*##g'
}

bridge_managed_active() {
  [[ -e $BRIDGE_CONTROL_SOCKET ]] || return 1
  "$SSH_BIN" -S "$BRIDGE_CONTROL_SOCKET" -O check "$BRIDGE_SSH_HOST" >/dev/null 2>&1
}

bridge_listener_active() {
  "$SS_BIN" -H -ltn "sport = :$BRIDGE_LOCAL_PORT" 2>/dev/null | grep -q .
}

stream_route_ready() {
  local route
  route=$("$IP_BIN" route get "$STREAM_HOST" 2>/dev/null | head -n 1) || return 1
  [[ " $route " == *" dev $STREAM_ROUTE_DEVICE "* ]]
}

stream_tcp_reachable() {
  stream_route_ready || return 1
  # The inner shell expands its positional host and port arguments.
  # shellcheck disable=SC2016
  "$TIMEOUT_BIN" "$STREAM_PROBE_TIMEOUT" bash -c 'exec 3<>"/dev/tcp/$1/$2"' _ "$STREAM_HOST" "$STREAM_CONTROL_PORT" 2>/dev/null
}

start_managed_bridge() {
  local socket_dir

  if bridge_managed_active; then
    return 0
  fi
  if bridge_listener_active; then
    return 2
  fi

  socket_dir=$(dirname "$BRIDGE_CONTROL_SOCKET")
  mkdir -p "$socket_dir"
  chmod 0700 "$socket_dir"
  rm -f "$BRIDGE_CONTROL_SOCKET"

  "$SSH_BIN" \
    -M \
    -S "$BRIDGE_CONTROL_SOCKET" \
    -fN \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L "$BRIDGE_LOCAL_ADDRESS:$BRIDGE_LOCAL_PORT:$BRIDGE_TARGET_HOST:$BRIDGE_TARGET_PORT" \
    "$BRIDGE_SSH_HOST"
}

stop_managed_bridge() {
  if ! bridge_managed_active; then
    return 1
  fi

  "$SSH_BIN" -S "$BRIDGE_CONTROL_SOCKET" -O exit "$BRIDGE_SSH_HOST" >/dev/null 2>&1
  rm -f "$BRIDGE_CONTROL_SOCKET"
}

print_status() {
  local device ip4
  local secret_present=false
  if has_secret; then
    secret_present=true
  fi

  if ! ensure_connection_exists; then
    json_status missing false "$secret_present" "" "" "connection not imported"
    return 0
  fi

  device=$(active_device)
  if [[ -n $device ]]; then
    ip4=$(active_ip4 "$device")
    json_status connected true "$secret_present" "$device" "$ip4" ""
    return 0
  fi

  json_status disconnected false "$secret_present" "" "" ""
}

store_password() {
  local password
  password=$(read_hidden_line "Base VPN password: ")
  if [[ -z $password ]]; then
    json_error store-password "empty password"
    return 1
  fi

  if printf '%s' "$password" | "$SECRET_TOOL_BIN" store --label="$CONNECTION_NAME base password" service "$SECRET_SERVICE" account "$SECRET_ACCOUNT" kind base-password >/dev/null; then
    # shellcheck disable=SC2016
    json_output --arg action "store-password" --arg connection "$CONNECTION_NAME" '{ok:true, action:$action, connection:$connection, message:"base password stored", severity:"ok"}'
    return 0
  fi

  json_error store-password "failed to store password"
  return 1
}

configure_vpn() {
  if ! ensure_connection_exists; then
    json_error configure "Verbindung nicht gefunden. Bitte ein OpenVPN-Profil importieren."
    return 1
  fi
  if ! "$NMCLI_BIN" connection modify id "$CONNECTION_NAME" vpn.user-name "${1:-}" >/dev/null 2>&1; then
    json_error configure "Benutzername konnte nicht gespeichert werden."
    return 1
  fi
  json_output '{ok:true, action:"configure"}'
}

connect_vpn() {
  local base_password totp combined
  local tmpdir passwd_file stderr_file
  local bridge_was_active=false

  if ! ensure_connection_exists; then
    json_error connect "connection not imported"
    return 1
  fi

  base_password=$(secret_lookup)
  if [[ -z $base_password ]]; then
    json_error connect "base password missing in Secret Service"
    return 1
  fi

  totp=
  if [[ $USE_TOTP == 1 ]]; then
    totp=$(read_hidden_line "TOTP (6 digits): ")
    if [[ ! $totp =~ ^[0-9]{6}$ ]]; then
      json_error connect "TOTP must be exactly 6 digits"
      return 1
    fi
  fi

  if [[ $OPENVPN_ONLY != 1 ]] && bridge_listener_active && ! bridge_managed_active; then
    json_error connect "RDP bridge is active outside the plugin; stop its ssh command first"
    return 1
  fi
  if [[ $OPENVPN_ONLY != 1 ]] && bridge_managed_active; then
    bridge_was_active=true
    if ! stop_managed_bridge; then
      json_error connect "failed to stop RDP bridge"
      return 1
    fi
  fi

  combined=${base_password}${totp}
  tmpdir=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/sophos-vpn.XXXXXX")
  passwd_file=$tmpdir/passwd
  stderr_file=$tmpdir/nmcli.stderr
  trap 'rm -rf -- "${tmpdir:-}"' RETURN

  printf 'vpn.secrets.password:%s\n' "$combined" > "$passwd_file"

  if "$NMCLI_BIN" --wait 45 connection up id "$CONNECTION_NAME" passwd-file "$passwd_file" > /dev/null 2>"$stderr_file"; then
    print_status | jq '. + {action:"connect", message:"connected"}'
    return 0
  fi

  print_status >/dev/null 2>&1 || true
  local safe_error
  safe_error=$(tail -n 1 "$stderr_file" | tr -d '\r' | sed 's/[[:space:]]\+/ /g')
  safe_error=${safe_error:-connection failed}
  if [[ $bridge_was_active == true ]]; then
    start_managed_bridge >/dev/null 2>&1 || true
  fi
  json_error connect "$safe_error"
  return 1
}

disconnect_vpn() {
  if ! ensure_connection_exists; then
    json_error disconnect "connection not imported"
    return 1
  fi

  if [[ -z $(active_device) ]]; then
    # shellcheck disable=SC2016
    json_output --arg action "disconnect" --arg connection "$CONNECTION_NAME" '{ok:true, action:$action, connection:$connection, connected:false, state:"disconnected", message:"already disconnected", severity:"inactive"}'
    return 0
  fi

  if "$NMCLI_BIN" --wait 30 connection down id "$CONNECTION_NAME" > /dev/null 2>&1; then
    # shellcheck disable=SC2016
    json_output --arg action "disconnect" --arg connection "$CONNECTION_NAME" '{ok:true, action:$action, connection:$connection, connected:false, state:"disconnected", message:"disconnected", bar_text:"VPN OFF", severity:"inactive"}'
    return 0
  fi

  json_error disconnect "disconnect failed"
  return 1
}

connect_bridge() {
  local device
  device=$(active_device)

  if [[ -n $device ]]; then
    if ! "$NMCLI_BIN" --wait 30 connection down id "$CONNECTION_NAME" >/dev/null 2>&1; then
      json_error bridge-connect "failed to disconnect OpenVPN before starting RDP bridge"
      return 1
    fi
  fi

  if bridge_managed_active; then
    print_status | jq '. + {action:"bridge-connect", message:"RDP bridge already connected"}'
    return 0
  fi
  if bridge_listener_active; then
    print_status | jq '. + {action:"bridge-connect", message:"RDP bridge is active outside the plugin"}'
    return 0
  fi

  if start_managed_bridge; then
    print_status | jq '. + {action:"bridge-connect", message:"RDP bridge connected"}'
    return 0
  fi

  json_error bridge-connect "failed to start RDP bridge"
  return 1
}

disconnect_bridge() {
  if bridge_managed_active; then
    if stop_managed_bridge; then
      print_status | jq '. + {action:"bridge-disconnect", message:"RDP bridge disconnected"}'
      return 0
    fi
    json_error bridge-disconnect "failed to stop RDP bridge"
    return 1
  fi

  if bridge_listener_active; then
    json_error bridge-disconnect "RDP bridge is active outside the plugin; stop its original ssh command"
    return 1
  fi

  print_status | jq '. + {action:"bridge-disconnect", message:"RDP bridge already disconnected"}'
}

import_profile() {
  local profile_path=${1:-}
  local import_name=$CONNECTION_NAME
  local import_username=$DEFAULT_IMPORT_USERNAME
  local before_file after_file stderr_file tmpdir import_profile_path new_uuid imported_uuid_count

  [[ -n $profile_path ]] || {
    json_error import "missing .ovpn path"
    return 1
  }
  [[ -f $profile_path ]] || {
    json_error import "profile not found"
    return 1
  }

  shift || true
  while (($#)); do
    case $1 in
      --name)
        import_name=${2:-}
        shift 2
        ;;
      --username)
        import_username=${2:-}
        shift 2
        ;;
      *)
        json_error import "unknown argument: $1"
        return 1
        ;;
    esac
  done

  if [[ $OPENVPN_ONLY == 1 ]] && "$NMCLI_BIN" -g connection.type connection show id "$import_name" >/dev/null 2>&1; then
    json_error import "Verbindungsname bereits vorhanden. Für einen neuen Import einen anderen Namen wählen."
    return 1
  fi

  tmpdir=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/sophos-vpn-import.XXXXXX")
  before_file=$tmpdir/before
  after_file=$tmpdir/after
  stderr_file=$tmpdir/nmcli.stderr
  import_profile_path=$tmpdir/profile.ovpn
  trap 'rm -rf -- "${tmpdir:-}"' RETURN

  # NetworkManager rejects OpenVPN's remote_host route placeholder. It already
  # keeps the VPN server reachable through the pre-tunnel gateway itself.
  awk '
    {
      normalized = $0
      sub(/^[[:space:]]+/, "", normalized)
      count = split(normalized, field, /[[:space:]]+/)
      if (count == 4 && field[1] == "route" && field[2] == "remote_host" && field[4] == "net_gateway") {
        next
      }
      print
    }
  ' "$profile_path" > "$import_profile_path"

  "$NMCLI_BIN" -t -f UUID,TYPE connection show 2>/dev/null | awk -F: '$2 == "vpn" { print $1 }' | sort > "$before_file"

  if ! "$NMCLI_BIN" connection import type openvpn file "$import_profile_path" > /dev/null 2>"$stderr_file"; then
    json_error import "$(tail -n 1 "$stderr_file" | tr -d '\r')"
    return 1
  fi

  "$NMCLI_BIN" -t -f UUID,TYPE connection show 2>/dev/null | awk -F: '$2 == "vpn" { print $1 }' | sort > "$after_file"
  new_uuid=$(comm -13 "$before_file" "$after_file" | head -n 1)
  imported_uuid_count=$(comm -13 "$before_file" "$after_file" | wc -l | tr -d ' ')

  if [[ -z $new_uuid || $imported_uuid_count != 1 ]]; then
    json_error import "could not identify imported connection; inspect nmcli connection show"
    return 1
  fi

  if [[ $OPENVPN_ONLY == 1 ]]; then
    "$NMCLI_BIN" connection modify uuid "$new_uuid" connection.id "$import_name" connection.autoconnect no >/dev/null
  else
    "$NMCLI_BIN" connection modify uuid "$new_uuid" connection.id "$import_name" connection.autoconnect no ipv4.never-default yes ipv6.never-default yes >/dev/null
  fi
  if [[ -n $import_username ]]; then
    "$NMCLI_BIN" connection modify id "$import_name" vpn.user-name "$import_username" >/dev/null
  fi

  # shellcheck disable=SC2016
  json_output \
    --arg action "import" \
    --arg connection "$import_name" \
    --arg username "$import_username" \
    '{
      ok:true,
      action:$action,
      connection:$connection,
      state:"imported",
      username: $username,
      message:"profile imported; base password remains external",
      severity:"ok"
    }'
}

main() {
  require_bins
  local cmd=${1:-status}
  case "$cmd" in
    status)
      shift
      print_status
      ;;
    configure)
      shift
      configure_vpn "$@"
      ;;
    connect)
      shift
      connect_vpn "$@"
      ;;
    disconnect)
      shift
      disconnect_vpn "$@"
      ;;
    bridge-connect)
      shift
      connect_bridge "$@"
      ;;
    bridge-disconnect)
      shift
      disconnect_bridge "$@"
      ;;
    import)
      shift
      import_profile "$@"
      ;;
    store-password)
      shift
      store_password
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

main "$@"
