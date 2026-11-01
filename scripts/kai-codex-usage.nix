{ pkgs }:
let
  codexbar = import ./codexbar.nix { inherit pkgs; };
in
pkgs.writeShellScriptBin "kai-codex-usage" ''
    export PATH=${
      pkgs.lib.makeBinPath [
        codexbar
        pkgs.jq
        pkgs.coreutils
      ]
    }:$PATH
    exec ${pkgs.python3}/bin/python3 - "$@" <<'PY'
  import json
  import os
  import subprocess
  import sys
  from datetime import datetime, timezone

  STATE_PATH = os.environ.get("KAI_CODEX_USAGE_STATE", os.path.expanduser("~/.cache/kai/codex-usage.json"))


  def now_iso():
      return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


  def run_codexbar():
      proc = subprocess.run(
          ["codexbar", "usage", "--provider", "codex", "--source", os.environ.get("KAI_CODEX_USAGE_SOURCE", "auto"), "--format", "json", "--json-only"],
          text=True,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
          timeout=90,
      )
      raw = (proc.stdout or "").strip()
      if not raw:
          raw = (proc.stderr or "").strip()
      try:
          payload = json.loads(raw) if raw else None
      except Exception:
          payload = {"error": {"message": raw or "codexbar returned no JSON"}}
      return proc.returncode, payload


  def first_payload(payload):
      if isinstance(payload, list):
          return payload[0] if payload else {}
      return payload or {}


  def normalize(payload, exit_code=0):
      item = first_payload(payload)
      error = item.get("error") if isinstance(item, dict) else None
      usage = item.get("usage") if isinstance(item, dict) else None
      primary = (usage or {}).get("primary") or {}
      secondary = (usage or {}).get("secondary") or {}
      credits = item.get("credits") if isinstance(item, dict) else None
      dashboard = item.get("openaiDashboard") if isinstance(item, dict) else None

      def used_percent(window):
          value = window.get("usedPercent")
          if value is None:
              return None
          try:
              return float(value)
          except Exception:
              return None

      primary_used = used_percent(primary)
      weekly_used = used_percent(secondary)
      primary_remaining = None if primary_used is None else max(0, min(100, 100 - primary_used))
      weekly_remaining = None if weekly_used is None else max(0, min(100, 100 - weekly_used))

      return {
          "ok": error is None and exit_code == 0,
          "updated_at": now_iso(),
          "provider": item.get("provider", "codex") if isinstance(item, dict) else "codex",
          "source": item.get("source") if isinstance(item, dict) else None,
          "account": item.get("account") if isinstance(item, dict) else None,
          "version": item.get("version") if isinstance(item, dict) else None,
          "primary_used_percent": primary_used,
          "primary_remaining_percent": primary_remaining,
          "primary_resets_at": primary.get("resetsAt"),
          "primary_window_minutes": primary.get("windowMinutes"),
          "weekly_used_percent": weekly_used,
          "weekly_remaining_percent": weekly_remaining,
          "weekly_resets_at": secondary.get("resetsAt"),
          "weekly_window_minutes": secondary.get("windowMinutes"),
          "credits_remaining": (credits or {}).get("remaining") if isinstance(credits, dict) else None,
          "code_review_remaining_percent": (dashboard or {}).get("codeReviewRemainingPercent") if isinstance(dashboard, dict) else None,
          "identity": (usage or {}).get("identity") if isinstance(usage, dict) else None,
          "error": (error or {}).get("message") if isinstance(error, dict) else None,
          "raw": item,
      }


  def write_state(data):
      os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
      tmp = STATE_PATH + ".tmp"
      with open(tmp, "w", encoding="utf-8") as handle:
          json.dump(data, handle, indent=2, sort_keys=True)
          handle.write("\n")
      os.chmod(tmp, 0o600)
      os.replace(tmp, STATE_PATH)


  def read_state():
      with open(STATE_PATH, "r", encoding="utf-8") as handle:
          return json.load(handle)


  def fmt_percent(value):
      if value is None:
          return "?"
      return str(int(round(float(value))))


  def print_bar(data):
      if not data.get("ok"):
          print("CX ?")
          return 1
      print(f"CX {fmt_percent(data.get('primary_remaining_percent'))}% {fmt_percent(data.get('weekly_remaining_percent'))}%")
      return 0


  def main(argv):
      command = argv[1] if len(argv) > 1 else "status"
      if command == "update":
          code, payload = run_codexbar()
          data = normalize(payload, code)
          write_state(data)
          print(json.dumps(data, indent=2, sort_keys=True))
          return 0 if data.get("ok") else 1
      if command == "status":
          try:
              data = read_state()
          except Exception:
              code, payload = run_codexbar()
              data = normalize(payload, code)
              write_state(data)
          print(json.dumps(data, indent=2, sort_keys=True))
          return 0 if data.get("ok") else 1
      if command == "bar":
          try:
              data = read_state()
          except Exception:
              code, payload = run_codexbar()
              data = normalize(payload, code)
              write_state(data)
          return print_bar(data)
      if command == "state-path":
          print(STATE_PATH)
          return 0
      print("Usage: kai-codex-usage {update|status|bar|state-path}", file=sys.stderr)
      return 2

  if __name__ == "__main__":
      raise SystemExit(main(sys.argv))
  PY
''
