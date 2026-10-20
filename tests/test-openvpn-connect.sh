#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
export TEST_VPN_DIR=$tmpdir
cat > "$tmpdir/nmcli" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
if [[ $* == '-t -f NAME,TYPE connection show' ]]; then printf '%s:vpn\n' "$SOPHOS_VPN_CONNECTION_NAME"
elif [[ $* == '-t -f NAME,TYPE,DEVICE connection show --active' ]]; then
  if [[ -f $TEST_VPN_DIR/active ]]; then printf '%s:vpn:tun0\n' "$SOPHOS_VPN_CONNECTION_NAME"; fi
elif [[ ${1:-} == -g && ${2:-} == vpn.user-name ]]; then printf 'fixture-user\n'
elif [[ ${1:-} == -g ]]; then printf '10.8.0.2/24\n'
elif [[ ${1:-} == --wait && ${4:-} == up ]]; then
  [[ $(cat "${8:?}") == "vpn.secrets.password:${TEST_EXPECTED_PASSWORD:?}" ]]
  touch "$TEST_VPN_DIR/active"
elif [[ ${1:-} == connection && ${2:-} == modify ]]; then
  printf '%s\n' "$*" > "$TEST_VPN_DIR/configured"
else exit 1
fi
MOCK
cat > "$tmpdir/secret-tool" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
account=${5:-}
if [[ ${TEST_LEGACY_ONLY:-0} == 1 ]]; then [[ $account == default ]] || exit 1
else [[ $account == "connection:$SOPHOS_VPN_CONNECTION_NAME" ]] || exit 1
fi
printf 'fixture-password'
MOCK
cat > "$tmpdir/legacy-probe" <<'MOCK'
#!/usr/bin/env bash
printf 'unexpected probe\n' >> "$TEST_VPN_DIR/legacy-probes"
exit 1
MOCK
chmod +x "$tmpdir/"{nmcli,secret-tool,legacy-probe}
export NMCLI_BIN=$tmpdir/nmcli SECRET_TOOL_BIN=$tmpdir/secret-tool
export SSH_BIN=$tmpdir/legacy-probe SS_BIN=$tmpdir/legacy-probe IP_BIN=$tmpdir/legacy-probe TIMEOUT_BIN=$tmpdir/legacy-probe
export SOPHOS_VPN_OPENVPN_ONLY=1 SOPHOS_VPN_CONNECTION_NAME='Fixture VPN' SOPHOS_VPN_SECRET_ACCOUNT='connection:Fixture VPN'
export SOPHOS_VPN_USE_TOTP=0 TEST_EXPECTED_PASSWORD=fixture-password
bash "$repo_root/scripts/sophos-vpn.sh" connect </dev/null | jq -e '.ok and .connected' >/dev/null
[[ ! -e $tmpdir/legacy-probes ]]
rm "$tmpdir/active"
export SOPHOS_VPN_USE_TOTP=1 TEST_EXPECTED_PASSWORD=fixture-password123456
printf '123456\n' | bash "$repo_root/scripts/sophos-vpn.sh" connect | jq -e '.ok and .connected' >/dev/null
rm "$tmpdir/active"
if printf '12\n' | bash "$repo_root/scripts/sophos-vpn.sh" connect > "$tmpdir/invalid.json"; then
  printf 'FAIL: invalid TOTP was accepted\n' >&2; exit 1
fi
[[ ! -e $tmpdir/active ]]
jq -e '.ok == false' "$tmpdir/invalid.json" >/dev/null
export TEST_LEGACY_ONLY=1 SOPHOS_VPN_USE_TOTP=0 TEST_EXPECTED_PASSWORD=fixture-password
# A different profile must never receive the old shared Sophos credential.
if bash "$repo_root/scripts/sophos-vpn.sh" connect </dev/null > "$tmpdir/isolated.json"; then
  printf 'FAIL: credential crossed profiles\n' >&2; exit 1
fi
export SOPHOS_VPN_CONNECTION_NAME='Sophos VPN' SOPHOS_VPN_SECRET_ACCOUNT='connection:Sophos VPN'
bash "$repo_root/scripts/sophos-vpn.sh" connect </dev/null | jq -e '.ok and .connected' >/dev/null
bash "$repo_root/scripts/sophos-vpn.sh" configure 'changed-user' | jq -e '.ok' >/dev/null
[[ $(cat "$tmpdir/configured") == 'connection modify id Sophos VPN vpn.user-name changed-user' ]]
[[ ! -e $tmpdir/legacy-probes ]]
printf 'PASS: optional TOTP, profile credentials, Sophos migration, username update, no legacy probes\n'
