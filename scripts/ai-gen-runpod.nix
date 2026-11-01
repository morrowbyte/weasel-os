{ pkgs }:

pkgs.writeShellScriptBin "ai-gen-runpod" ''
    exec ${pkgs.python3}/bin/python3 - "$@" <<'PY'
  import datetime as dt
  import json
  import os
  import subprocess
  import sys
  import urllib.parse
  import urllib.request

  POD_ID = os.environ.get("RUNPOD_AI_GEN_POD_ID", "i2idrxq9fr3w9w")
  ENV_FILE = os.environ.get("RUNPOD_ENV_FILE", os.path.expanduser("~/.config/runpod/runpod.env"))
  GRAPHQL_URL = "https://api.runpod.io/graphql"
  REST_URL = "https://rest.runpod.io/v1"
  USER_AGENT = "weasel-os-ai-gen-runpod/1.0"


  def load_env_file(path):
      if not os.path.exists(path):
          return
      with open(path, "r", encoding="utf-8") as handle:
          for line in handle:
              line = line.strip()
              if not line or line.startswith("#") or "=" not in line:
                  continue
              key, value = line.split("=", 1)
              os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


  def api_key():
      load_env_file(ENV_FILE)
      key = os.environ.get("RUNPOD_API_KEY", "").strip()
      if not key:
          raise RuntimeError(f"missing RUNPOD_API_KEY in {ENV_FILE}")
      return key


  def request_json(url, method="GET", payload=None):
      body = None
      headers = {
          "Authorization": f"Bearer {api_key()}",
          "User-Agent": USER_AGENT,
      }
      if payload is not None:
          body = json.dumps(payload).encode("utf-8")
          headers["Content-Type"] = "application/json"
      req = urllib.request.Request(url, data=body, headers=headers, method=method)
      with urllib.request.urlopen(req, timeout=20) as response:
          raw = response.read().decode("utf-8")
      if not raw:
          return None
      return json.loads(raw)


  def graphql(query):
      data = request_json(GRAPHQL_URL, method="POST", payload={"query": query})
      if data.get("errors"):
          raise RuntimeError(data["errors"][0].get("message", "GraphQL error"))
      return data["data"]


  def rest(path, method="GET"):
      return request_json(f"{REST_URL}{path}", method=method)


  def month_range():
      now = dt.datetime.now(dt.timezone.utc)
      start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
      return start.isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")


  def billing_sum(kind):
      start, end = month_range()
      query = urllib.parse.urlencode({
          "startTime": start,
          "endTime": end,
          "bucketSize": "month",
      })
      try:
          rows = rest(f"/billing/{kind}?{query}") or []
      except Exception:
          return 0.0
      return float(sum(float(row.get("amount") or 0) for row in rows))


  def status_data():
      account = graphql("""
        query {
          myself {
            clientBalance
            currentSpendPerHr
          }
        }
      """)["myself"]
      pod = rest(f"/pods/{POD_ID}")
      pod_status = pod.get("desiredStatus") or "UNKNOWN"
      cost_per_hr = float(pod.get("costPerHr") or 0)
      month_pods = billing_sum("pods")
      month_volumes = billing_sum("networkvolumes")
      return {
          "pod_id": POD_ID,
          "pod_name": pod.get("name"),
          "pod_status": pod_status,
          "pod_cost_per_hr": cost_per_hr,
          "current_spend_per_hr": float(account.get("currentSpendPerHr") or 0),
          "client_balance": float(account.get("clientBalance") or 0),
          "month_spend": month_pods + month_volumes,
          "month_pod_spend": month_pods,
          "month_storage_spend": month_volumes,
          "comfy_url": f"https://{POD_ID}-8188.proxy.runpod.net/",
          "public_ip": pod.get("publicIp"),
          "ssh_port": (pod.get("portMappings") or {}).get("22"),
      }


  def money(value):
      return "$" + format(value, ".2f")


  def status_short(status):
      return "RUN" if status == "RUNNING" else "STOP" if status in {"EXITED", "STOPPED"} else status[:4]


  def print_bar():
      try:
          data = status_data()
      except Exception:
          print("RP key?")
          return 2
      print(f"RP {status_short(data['pod_status'])}")
      return 0


  def print_status_json():
      data = status_data()
      data["status_short"] = status_short(data["pod_status"])
      print(json.dumps(data, separators=(",", ":")))


  def open_comfy():
      url = f"https://{POD_ID}-8188.proxy.runpod.net/"
      subprocess.Popen(["${pkgs.xdg-utils}/bin/xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
      print(url)


  def run_laptop_helper(helper):
      path = os.path.expanduser(f"~/ai-gen/bin/{helper}")
      subprocess.run([path], check=True)


  def main(argv):
      command = argv[1] if len(argv) > 1 else "status"
      if command == "status":
          print(json.dumps(status_data(), indent=2))
      elif command == "status-json":
          print_status_json()
      elif command == "bar":
          raise SystemExit(print_bar())
      elif command == "start":
          print(json.dumps(rest(f"/pods/{POD_ID}/start", method="POST"), indent=2))
      elif command == "stop":
          print(json.dumps(rest(f"/pods/{POD_ID}/stop", method="POST"), indent=2))
      elif command == "open":
          open_comfy()
      elif command == "sync-up":
          run_laptop_helper("ai-gen-sync-up")
      elif command == "sync-down":
          run_laptop_helper("ai-gen-sync-down")
      elif command == "sync-all":
          run_laptop_helper("ai-gen-sync-up")
          run_laptop_helper("ai-gen-sync-down")
      elif command == "secret-path":
          print(ENV_FILE)
      else:
          print("Usage: ai-gen-runpod {status|status-json|bar|start|stop|open|sync-up|sync-down|sync-all|secret-path}", file=sys.stderr)
          return 2
      return 0


  if __name__ == "__main__":
      raise SystemExit(main(sys.argv))
  PY
''
