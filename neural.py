"""neural.py — AIDE Neural message bus.

Runs a local HTTP server so agents (Claude Code sessions) can register,
announce their current task, and send messages to each other.
All inter-agent messages require human approval before delivery.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict, List, Optional


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class NeuralAgent:
    session_id: int
    name: str
    task: str
    token: str
    registered_at: float = field(default_factory=time.time)
    last_seen: float    = field(default_factory=time.time)


@dataclass
class NeuralMessage:
    id:           str   = field(default_factory=lambda: uuid.uuid4().hex[:8])
    from_session: int   = 0
    to_session:   int   = -1  # -1 = broadcast
    content:      str   = ""
    timestamp:    float = field(default_factory=time.time)
    status:       str   = "pending"  # pending | approved | denied | delivered


# ── Bus ──────────────────────────────────────────────────────────────────────

class NeuralBus:
    """HTTP message bus. Call start() to bind a port and return it."""

    def __init__(self, on_request: Callable[[NeuralMessage], None]):
        self._agents:     Dict[str, NeuralAgent]   = {}  # token → agent
        self._by_session: Dict[int, str]            = {}  # session_id → token
        self._messages:   List[NeuralMessage]       = []
        self._on_request  = on_request
        self._lock        = threading.RLock()
        self._server: Optional[HTTPServer] = None
        self.port: int = 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> int:
        bus = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass

            def _body(self):
                n = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(n)) if n else {}

            def _ok(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def _err(self, code, msg):
                body = json.dumps({"error": msg}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def do_POST(self):
                try:
                    d = self._body()
                    p = self.path
                    if p == "/register":
                        token = bus.register(int(d["session_id"]), d["name"], d.get("task", ""))
                        self._ok({"token": token})
                    elif p == "/task":
                        bus.update_task(d["token"], d["task"]); self._ok({"ok": True})
                    elif p == "/send":
                        mid = bus.send(d["token"], int(d["to"]), d["content"])
                        if mid: self._ok({"id": mid, "status": "pending"})
                        else:   self._err(404, "target agent not found")
                    else:
                        self._err(404, "not found")
                except Exception as e:
                    self._err(400, str(e))

            def do_GET(self):
                try:
                    tok = self.headers.get("X-Token", "")
                    p   = self.path
                    if p == "/agents":  self._ok(bus.list_agents(tok))
                    elif p == "/inbox": self._ok(bus.get_inbox(tok))
                    else:               self._err(404, "not found")
                except Exception as e:
                    self._err(400, str(e))

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port    = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.port

    def stop(self):
        if self._server:
            self._server.shutdown()

    # ── agent management ──────────────────────────────────────────────────────

    def register(self, session_id: int, name: str, task: str) -> str:
        with self._lock:
            old = self._by_session.get(session_id)
            if old: self._agents.pop(old, None)
            token = uuid.uuid4().hex
            self._agents[token] = NeuralAgent(session_id=session_id, name=name,
                                               task=task, token=token)
            self._by_session[session_id] = token
        return token

    def unregister(self, session_id: int):
        with self._lock:
            tok = self._by_session.pop(session_id, None)
            if tok: self._agents.pop(tok, None)

    def update_task(self, token: str, task: str):
        with self._lock:
            a = self._agents.get(token)
            if a: a.task = task; a.last_seen = time.time()

    def list_agents(self, token: str) -> List[dict]:
        with self._lock:
            return [
                {"session_id": a.session_id, "name": a.name,
                 "task": a.task, "last_seen": a.last_seen}
                for t, a in self._agents.items() if t != token
            ]

    def all_agents(self) -> List[NeuralAgent]:
        with self._lock:
            return list(self._agents.values())

    # ── messaging ─────────────────────────────────────────────────────────────

    def send(self, token: str, to_session_id: int, content: str) -> Optional[str]:
        with self._lock:
            agent = self._agents.get(token)
            if not agent: return None
            if to_session_id != -1 and to_session_id not in self._by_session:
                return None
            msg = NeuralMessage(from_session=agent.session_id,
                                to_session=to_session_id,
                                content=content)
            self._messages.append(msg)
            agent.last_seen = time.time()
        self._on_request(msg)
        return msg.id

    def approve(self, msg_id: str):
        with self._lock:
            for m in self._messages:
                if m.id == msg_id: m.status = "approved"; break

    def deny(self, msg_id: str):
        with self._lock:
            for m in self._messages:
                if m.id == msg_id: m.status = "denied"; break

    def get_inbox(self, token: str) -> List[dict]:
        with self._lock:
            a = self._agents.get(token)
            if not a: return []
            result = []
            for m in self._messages:
                if m.status != "approved": continue
                if m.to_session != a.session_id and m.to_session != -1: continue
                m.status = "delivered"
                sender = next((x for x in self._agents.values()
                               if x.session_id == m.from_session), None)
                result.append({"id": m.id,
                               "from": sender.name if sender else f"Session {m.from_session}",
                               "content": m.content,
                               "timestamp": m.timestamp})
        return result

    def get_pending(self) -> List[dict]:
        """Snapshot of all pending messages for the approval UI."""
        with self._lock:
            out = []
            for m in self._messages:
                if m.status != "pending": continue
                sender = next((a for a in self._agents.values()
                               if a.session_id == m.from_session), None)
                target = next((a for a in self._agents.values()
                               if a.session_id == m.to_session), None) \
                         if m.to_session != -1 else None
                out.append({
                    "id":        m.id,
                    "from_name": sender.name if sender else f"Session {m.from_session}",
                    "to_name":   target.name if target else ("All" if m.to_session == -1
                                                             else f"Session {m.to_session}"),
                    "content":   m.content,
                    "timestamp": m.timestamp,
                })
        return out


# ── Client script ─────────────────────────────────────────────────────────────

_CLIENT_SRC = '''\
#!/usr/bin/env python3
"""neural — AIDE agent communication client.

Usage:
  neural register "<name>" "<current task>"
  neural task "<what you are doing now>"
  neural agents
  neural send <session_id> "<message>"
  neural broadcast "<message>"
  neural inbox
"""
import os, sys, json
from urllib.request import urlopen, Request
from urllib.error import HTTPError

URL  = os.environ.get("AIDE_NEURAL_URL", "")
SID  = int(os.environ.get("AIDE_SESSION_ID", "-1"))
_TF  = os.path.expanduser(f"~/.aide_neural_{SID}.token")

def _tok():
    return open(_TF).read().strip() if os.path.exists(_TF) else ""

def _req(method, path, body=None, token=None):
    url  = URL + path
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type": "application/json"}
    if token: hdrs["X-Token"] = token
    r = Request(url, data=data, headers=hdrs, method=method)
    try:
        with urlopen(r, timeout=5) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        sys.exit(json.loads(e.read()).get("error", str(e)))

def _register(a):
    if len(a) < 2: sys.exit('Usage: neural register "<name>" "<task>"')
    r = _req("POST", "/register", {"session_id": SID, "name": a[0], "task": a[1]})
    open(_TF, "w").write(r["token"])
    print(f"Registered as \\"{a[0]}\\" (session {SID})")

def _task(a):
    if not a: sys.exit('Usage: neural task "<what you are doing>"')
    t = _tok()
    if not t: sys.exit("Not registered. Run: neural register \\"<name>\\" \\"<task>\\"")
    _req("POST", "/task", {"token": t, "task": a[0]})
    print(f"Task updated.")

def _agents(_):
    agents = _req("GET", "/agents", token=_tok())
    if not agents: print("No other agents registered."); return
    for a in agents:
        print(f"  [{a['session_id']}] {a['name']} — {a.get('task') or '(idle)'}")

def _send(a):
    if len(a) < 2: sys.exit("Usage: neural send <session_id> \\"<message>\\"")
    t = _tok()
    if not t: sys.exit("Not registered.")
    r = _req("POST", "/send", {"token": t, "to": int(a[0]), "content": a[1]})
    print(f"Message queued (id: {r['id']}) — awaiting human approval")

def _broadcast(a):
    if not a: sys.exit('Usage: neural broadcast "<message>"')
    t = _tok()
    if not t: sys.exit("Not registered.")
    r = _req("POST", "/send", {"token": t, "to": -1, "content": a[0]})
    print(f"Broadcast queued (id: {r['id']}) — awaiting human approval")

def _inbox(_):
    t = _tok()
    if not t: sys.exit("Not registered.")
    msgs = _req("GET", "/inbox", token=t)
    if not msgs: print("No messages."); return
    for m in msgs:
        print(f"  From {m['from']}: {m['content']}")

_CMDS = {"register": _register, "task": _task, "agents": _agents,
         "send": _send, "broadcast": _broadcast, "inbox": _inbox}

if __name__ == "__main__":
    if not URL: sys.exit("AIDE_NEURAL_URL not set — are you inside an AIDE terminal?")
    args = sys.argv[1:]
    if not args or args[0] not in _CMDS:
        print(__doc__); sys.exit(1)
    _CMDS[args[0]](args[1:])
'''


def write_client(directory: str) -> str:
    """Write the `neural` client script to *directory* and return its path."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "neural")
    with open(path, "w") as f:
        f.write(_CLIENT_SRC)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path
