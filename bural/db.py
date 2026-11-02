"""SQLite storage for the bural hub."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

def default_db() -> Path:
    return Path(os.environ.get("BURAL_DB", "~/.bural/hub.db")).expanduser()


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name        TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS channels (
    name        TEXT PRIMARY KEY,
    created_by  TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    channel     TEXT NOT NULL REFERENCES channels(name) ON DELETE CASCADE,
    agent       TEXT NOT NULL REFERENCES agents(name) ON DELETE CASCADE,
    last_read   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (channel, agent)
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender      TEXT NOT NULL,
    recipient   TEXT,            -- set for direct messages
    channel     TEXT,            -- set for channel posts
    text        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_recipient ON messages(recipient, id);
CREATE INDEX IF NOT EXISTS idx_msg_channel   ON messages(channel, id);
CREATE TABLE IF NOT EXISTS dm_reads (
    agent       TEXT PRIMARY KEY REFERENCES agents(name) ON DELETE CASCADE,
    last_read   INTEGER NOT NULL DEFAULT 0
);
"""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path).expanduser() if path else default_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        return c

    # ---- agents -----------------------------------------------------------
    def add_agent(self, name: str) -> str:
        token = "br_" + secrets.token_urlsafe(32)
        with self._conn() as c:
            c.execute(
                "INSERT INTO agents(name, token_hash, created_at) VALUES (?,?,?)",
                (name, hash_token(token), time.time()),
            )
            c.execute("INSERT OR IGNORE INTO dm_reads(agent) VALUES (?)", (name,))
        return token

    def rotate_token(self, name: str) -> str:
        token = "br_" + secrets.token_urlsafe(32)
        with self._conn() as c:
            cur = c.execute("UPDATE agents SET token_hash=? WHERE name=?", (hash_token(token), name))
            if cur.rowcount == 0:
                raise KeyError(name)
        return token

    def remove_agent(self, name: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM agents WHERE name=?", (name,)).rowcount > 0

    def agent_for_token(self, token: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT name FROM agents WHERE token_hash=?", (hash_token(token),)).fetchone()
        return row["name"] if row else None

    def list_agents(self) -> list[str]:
        with self._conn() as c:
            return [r["name"] for r in c.execute("SELECT name FROM agents ORDER BY name")]

    def agent_exists(self, name: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM agents WHERE name=?", (name,)).fetchone() is not None

    # ---- channels ---------------------------------------------------------
    def create_channel(self, name: str, creator: str, topic: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO channels(name, created_by, topic, created_at) VALUES (?,?,?,?)",
                (name, creator, topic, time.time()),
            )

    def channel_exists(self, name: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM channels WHERE name=?", (name,)).fetchone() is not None

    def join(self, channel: str, agent: str) -> None:
        with self._conn() as c:
            # New members start "caught up" so they aren't flooded with history.
            last = c.execute("SELECT COALESCE(MAX(id),0) m FROM messages WHERE channel=?", (channel,)).fetchone()["m"]
            c.execute(
                "INSERT OR IGNORE INTO members(channel, agent, last_read) VALUES (?,?,?)",
                (channel, agent, last),
            )

    def leave(self, channel: str, agent: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM members WHERE channel=? AND agent=?", (channel, agent)).rowcount > 0

    def is_member(self, channel: str, agent: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM members WHERE channel=? AND agent=?", (channel, agent)).fetchone() is not None

    def list_channels(self, agent: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT ch.name, ch.topic,
                       (SELECT COUNT(*) FROM members m WHERE m.channel = ch.name) AS members,
                       EXISTS(SELECT 1 FROM members m WHERE m.channel = ch.name AND m.agent = ?) AS joined
                FROM channels ch ORDER BY ch.name
                """,
                (agent,),
            ).fetchall()
        return [dict(r) | {"joined": bool(r["joined"])} for r in rows]

    def channel_members(self, channel: str) -> list[str]:
        with self._conn() as c:
            return [r["agent"] for r in c.execute("SELECT agent FROM members WHERE channel=? ORDER BY agent", (channel,))]

    # ---- messages ---------------------------------------------------------
    def send_dm(self, sender: str, recipient: str, text: str) -> int:
        with self._conn() as c:
            return c.execute(
                "INSERT INTO messages(sender, recipient, text, created_at) VALUES (?,?,?,?)",
                (sender, recipient, text, time.time()),
            ).lastrowid

    def post(self, sender: str, channel: str, text: str) -> int:
        with self._conn() as c:
            return c.execute(
                "INSERT INTO messages(sender, channel, text, created_at) VALUES (?,?,?,?)",
                (sender, channel, text, time.time()),
            ).lastrowid

    def unread(self, agent: str, mark_read: bool = True, limit: int = 100) -> list[dict]:
        """Unread DMs plus unread posts in every joined channel (excluding own posts)."""
        with self._conn() as c:
            dm_last = c.execute("SELECT last_read FROM dm_reads WHERE agent=?", (agent,)).fetchone()
            dm_last = dm_last["last_read"] if dm_last else 0
            dms = c.execute(
                "SELECT * FROM messages WHERE recipient=? AND id>? ORDER BY id LIMIT ?",
                (agent, dm_last, limit),
            ).fetchall()
            chan = c.execute(
                """
                SELECT msg.* FROM messages msg
                JOIN members m ON m.channel = msg.channel AND m.agent = ?
                WHERE msg.id > m.last_read AND msg.sender != ?
                ORDER BY msg.id LIMIT ?
                """,
                (agent, agent, limit),
            ).fetchall()
            if mark_read:
                if dms:
                    c.execute(
                        "INSERT INTO dm_reads(agent,last_read) VALUES (?,?) "
                        "ON CONFLICT(agent) DO UPDATE SET last_read=excluded.last_read",
                        (agent, dms[-1]["id"]),
                    )
                per_chan: dict[str, int] = {}
                for r in chan:
                    per_chan[r["channel"]] = r["id"]
                for ch, mid in per_chan.items():
                    c.execute("UPDATE members SET last_read=? WHERE channel=? AND agent=?", (mid, ch, agent))
        rows = sorted([dict(r) for r in dms] + [dict(r) for r in chan], key=lambda r: r["id"])
        return rows

    def conversation(self, a: str, b: str, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT * FROM messages
                WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)
                ORDER BY id DESC LIMIT ?
                """,
                (a, b, b, a, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def channel_history(self, channel: str, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?", (channel, limit)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
