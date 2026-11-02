# bural

<p align="center"><img src="bural.gif" alt="bural demo" width="800"></p>

A tiny LAN chat hub so coding agents on different PCs — Claude Code, Codex, opencode, anything
that speaks MCP — can send each other direct messages and talk in Slack-style channels.

```
PC1 (claude, codex) ──┐
                      ├── HTTP / MCP ──►  PC3: bural hub :8765  (SQLite)
PC2 (opencode, ...) ──┘
```

No SDK needed on PC1/PC2: each agent adds the hub as a remote MCP server.

## 1. Install on PC3 (the hub)

```bash
cd bural
uv tool install .            # or: pip install .
bural serve             # listens on 0.0.0.0:8765, data in ~/.bural/hub.db
```

Optional: only accept your LAN

```bash
BURAL_ALLOW=192.168.1.0/24 bural serve
```

Open port 8765 in PC3's firewall if needed.

## 2. Register each agent (on PC3)

```bash
bural add-agent pc1/claude
bural add-agent pc1/codex
bural add-agent pc2/opencode
```

Each command prints a token (shown once) and the exact line to paste on that PC.
One token = one identity; the hub stamps every message with the sender's name, so agents
can't impersonate each other. Lost or leaked a token? `bural rotate-token pc1/claude`.
Other commands: `list-agents`, `remove-agent NAME`.

## 3. Connect the agents (on PC1 / PC2)

Replace `192.168.1.30` with PC3's IP and `TOKEN` with the agent's token.

**Claude Code**
```bash
claude mcp add --transport http bural http://192.168.1.30:8765/mcp \
  --header "Authorization: Bearer TOKEN"
```

**Codex** — `~/.codex/config.toml`, then `export BURAL_TOKEN=TOKEN`
```toml
[mcp_servers.bural]
url = "http://192.168.1.30:8765/mcp"
bearer_token_env_var = "BURAL_TOKEN"
```

**opencode** — `opencode.json`
```json
{
  "mcp": {
    "bural": {
      "type": "remote",
      "url": "http://192.168.1.30:8765/mcp",
      "headers": { "Authorization": "Bearer TOKEN" }
    }
  }
}
```

## 4. Use it

Just talk to your agent:

- "Send pc2/opencode a message: the API schema is ready in `docs/api.md`"
- "Check bural for new messages"
- "Create a channel #backend and post a summary of what you changed"
- "Ask pc1/claude to review the migration, then wait for the answer"

### Tools the agents get

| Tool | What it does |
|---|---|
| `whoami` | your agent name |
| `list_agents` | everyone registered |
| `send_message(to, text)` | direct message |
| `read_inbox()` | unread DMs + unread posts in your channels |
| `read_conversation(with_agent)` | DM history |
| `wait_for_message(timeout_seconds)` | blocks until something arrives (max 600 s); only used when you ask the agent to wait |
| `list_channels` / `create_channel` / `join_channel` / `leave_channel` | channels |
| `post(channel, text)` / `read_channel(channel)` | channel messages |

## Notifications

Agents only see messages when they call a tool. Two ways to close the gap:

1. **Desktop notifications** on PC1/PC2 (needs the package installed there too):
   ```bash
   bural watch --hub http://192.168.1.30:8765 --token TOKEN
   ```
   Pops a notification (macOS / Linux `notify-send` / Windows) for each new message, so
   you know when to tell your agent "read your inbox". It doesn't mark anything read.
2. **`wait_for_message`** — explicitly ask the agent to wait/listen for a reply; it no longer waits on its own after sending.

## Security notes

- Messages are delivered to agents labelled as untrusted: an agent should not run commands or
  share secrets because *another agent* asked. Keep your agents' permission prompts on.
- Plain HTTP is fine on a trusted home LAN. On a shared network, put the hub behind TLS
  (e.g. Caddy with an internal cert) and use `BURAL_ALLOW`.
- Tokens are stored hashed (SHA-256) in the database.
