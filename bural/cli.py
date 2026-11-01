"""bural command line: run the hub, manage agents, watch for messages."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _setup_snippets(name: str, token: str, url: str) -> str:
    return f"""
Agent '{name}' registered. Token (shown once, keep it secret):

  {token}

Connect it from that PC — pick the tool:

  Claude Code:
    claude mcp add --transport http bural {url} --header "Authorization: Bearer {token}"

  Codex  (~/.codex/config.toml, then: export BURAL_TOKEN={token}):
    [mcp_servers.bural]
    url = "{url}"
    bearer_token_env_var = "BURAL_TOKEN"

  opencode  (opencode.json):
    "mcp": {{ "bural": {{ "type": "remote", "url": "{url}",
      "headers": {{ "Authorization": "Bearer {token}" }} }} }}

  Desktop notifications on that PC:
    bural watch --hub {url.rsplit('/mcp', 1)[0]} --token {token}
"""


def _notify(title: str, body: str) -> None:
    body = body[:200]
    try:
        if platform.system() == "Darwin":
            script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
            subprocess.run(["osascript", "-e", script], check=False)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, body], check=False)
        elif platform.system() == "Windows":
            ps = (
                "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null;"
                "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
                f"$n.Visible=$true;$n.ShowBalloonTip(5000,{json.dumps(title)},{json.dumps(body)},'Info');Start-Sleep 6"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
    except Exception:
        pass


def cmd_watch(args) -> None:
    base = args.hub.rstrip("/")
    token = args.token or os.environ.get("BURAL_TOKEN")
    if not token:
        sys.exit("Need --token or BURAL_TOKEN")
    seen = 0
    first = True
    print(f"Watching {base} (Ctrl+C to stop)")
    while True:
        try:
            req = urllib.request.Request(f"{base}/api/peek?after={seen}",
                                         headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.load(r)
            msgs = data.get("messages", [])
            for m in msgs:
                seen = max(seen, m["id"])
                where = m.get("channel") or "DM"
                line = f"[{m['time']}] {where} {m['from']}: {m['text']}"
                print(line, flush=True)
                if not first or args.notify_existing:
                    _notify(f"bural · {m['from']} ({where})", m["text"])
            if first and msgs:
                print(f"({len(msgs)} unread waiting — ask your agent to read_inbox)")
            first = False
        except urllib.error.HTTPError as e:
            if e.code == 401:
                sys.exit("Hub rejected the token (401).")
            print(f"hub error {e.code}, retrying...", file=sys.stderr)
        except (urllib.error.URLError, OSError) as e:
            print(f"hub unreachable ({e}), retrying...", file=sys.stderr)
        time.sleep(args.interval)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="bural", description="LAN chat hub for coding agents")
    p.add_argument("--db", help="SQLite path (default ~/.bural/hub.db or $BURAL_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the hub (on PC3)")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8765)

    a = sub.add_parser("add-agent", help="register an agent and print its token + setup lines")
    a.add_argument("name", help="e.g. pc1/claude")
    a.add_argument("--url", help="hub URL to print in setup lines (default http://<lan-ip>:8765/mcp)")

    r = sub.add_parser("rotate-token", help="issue a new token for an agent (old one stops working)")
    r.add_argument("name")
    r.add_argument("--url")

    rm = sub.add_parser("remove-agent", help="delete an agent")
    rm.add_argument("name")

    sub.add_parser("list-agents", help="list registered agents")

    w = sub.add_parser("watch", help="desktop notifications for new messages (on PC1/PC2)")
    w.add_argument("--hub", required=True, help="e.g. http://192.168.1.30:8765")
    w.add_argument("--token")
    w.add_argument("--interval", type=float, default=3.0)
    w.add_argument("--notify-existing", action="store_true", help="also notify for messages already waiting")

    args = p.parse_args(argv)
    if args.db:
        os.environ["BURAL_DB"] = args.db

    if args.cmd == "watch":
        return cmd_watch(args)

    from .db import Store
    from .server import NAME_RE

    store = Store()
    if args.cmd == "serve":
        from .server import serve
        serve(args.host, args.port)
    elif args.cmd in ("add-agent", "rotate-token"):
        name = args.name.strip().lower()
        if not NAME_RE.match(name):
            sys.exit("Agent names: lowercase letters, digits, . _ - /  (e.g. pc1/claude)")
        url = args.url or f"http://{_lan_ip()}:8765/mcp"
        try:
            token = store.add_agent(name) if args.cmd == "add-agent" else store.rotate_token(name)
        except KeyError:
            sys.exit(f"No agent named {name}")
        except Exception as e:
            sys.exit(f"Could not register {name}: {e}")
        print(_setup_snippets(name, token, url))
    elif args.cmd == "remove-agent":
        print("removed" if store.remove_agent(args.name.strip().lower()) else "not found")
    elif args.cmd == "list-agents":
        for n in store.list_agents():
            print(n)


if __name__ == "__main__":
    main()
