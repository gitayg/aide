"""neural.py — AIDE Neural message bus.

Runs a local HTTP server so agents (Claude Code sessions) can register,
announce their current task, and send messages to each other.
Also serves an MCP SSE endpoint so Claude Code can use AIDE as its
--permission-prompt-tool, routing tool-permission requests to the human
via a Qt dialog.
"""
from __future__ import annotations

import json
import os
import queue
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse, parse_qs


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class NeuralAgent:
    session_id: int
    name: str
    task: str
    token: str
    tag:  str = ""
    app:  str = ""
    role: str = ""
    registered_at: float = field(default_factory=time.time)
    last_seen: float    = field(default_factory=time.time)


@dataclass
class NeuralMessage:
    id:           str   = field(default_factory=lambda: uuid.uuid4().hex[:8])
    from_session: int   = 0
    to_session:   int   = -1  # -1 = broadcast
    content:      str   = ""
    timestamp:    float = field(default_factory=time.time)
    status:       str   = "delivered"  # delivered | read


# ── Threaded server ──────────────────────────────────────────────────────────

class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ── Bus ──────────────────────────────────────────────────────────────────────

class NeuralBus:
    """HTTP message bus. Call start() to bind a port and return it."""

    def __init__(self, on_message: Callable[[NeuralMessage], None],
                 on_permission: Optional[Callable[[str, dict], None]] = None):
        self._agents:     Dict[str, NeuralAgent]   = {}  # token → agent
        self._by_session: Dict[int, str]            = {}  # session_id → token
        self._messages:   List[NeuralMessage]       = []
        self._on_message  = on_message
        self._lock        = threading.RLock()
        self._server: Optional[_ThreadedHTTPServer] = None
        self.port: int = 0

        # MCP permission-prompt state
        self._on_permission = on_permission
        self._mcp_lock     = threading.Lock()
        self._mcp_sessions: Dict[str, queue.Queue] = {}   # sessionId → SSE queue
        self._perm_events:  Dict[str, threading.Event] = {}
        self._perm_results: Dict[str, dict] = {}

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

            # ── MCP SSE stream ─────────────────────────────────────────────
            def _mcp_sse(self, sid: str):
                q: queue.Queue = queue.Queue()
                with bus._mcp_lock:
                    bus._mcp_sessions[sid] = q
                # Tell Claude where to POST requests for this session
                endpoint_url = f"http://127.0.0.1:{bus.port}/mcp?sessionId={sid}"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    self.wfile.write(
                        f"event: endpoint\ndata: {endpoint_url}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=15)
                            if msg is None:   # sentinel — close stream
                                break
                            self.wfile.write(
                                f"event: message\ndata: {json.dumps(msg)}\n\n".encode())
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with bus._mcp_lock:
                        bus._mcp_sessions.pop(sid, None)

            # ── MCP JSON-RPC POST ──────────────────────────────────────────
            def _mcp_post(self, sid: str):
                d    = self._body()
                meth = d.get("method", "")
                rid  = d.get("id")

                if meth == "initialize":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Mcp-Session-Id", sid)
                    result = json.dumps({
                        "jsonrpc": "2.0", "id": rid,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "aide-neural", "version": "3.0.0"},
                        }
                    }).encode()
                    self.send_header("Content-Length", str(len(result)))
                    self.end_headers(); self.wfile.write(result)
                    return

                if meth == "notifications/initialized":
                    self.send_response(202); self.end_headers(); return

                if meth == "tools/list":
                    self._ok({
                        "jsonrpc": "2.0", "id": rid,
                        "result": {"tools": [{
                            "name": "permission_prompt",
                            "description": "Ask the human whether to allow a Claude Code tool call.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "tool_name":  {"type": "string"},
                                    "tool_input": {"type": "object"},
                                },
                                "required": ["tool_name", "tool_input"],
                            }
                        }]}
                    }); return

                if meth == "tools/call" and d.get("params", {}).get("name") == "permission_prompt":
                    inp     = d.get("params", {}).get("arguments", {})
                    perm_id = uuid.uuid4().hex[:8]
                    ev      = threading.Event()
                    with bus._mcp_lock:
                        bus._perm_events[perm_id] = ev
                    # Ask the Qt main thread (non-blocking from our side)
                    if bus._on_permission:
                        bus._on_permission(perm_id, inp)
                    approved = ev.wait(timeout=300)
                    with bus._mcp_lock:
                        res = bus._perm_results.pop(perm_id, None)
                        bus._perm_events.pop(perm_id, None)
                    if not approved or res is None:
                        decision = "deny"
                    else:
                        decision = res.get("decision", "deny")
                    behavior = "allow" if decision == "allow" else "deny"
                    self._ok({
                        "jsonrpc": "2.0", "id": rid,
                        "result": {
                            "content": [{"type": "text", "text": behavior}],
                            "behavior": behavior,
                        }
                    }); return

                self._err(404, f"unknown method: {meth}")

            def do_POST(self):
                try:
                    parsed = urlparse(self.path)
                    p      = parsed.path
                    qs     = parse_qs(parsed.query)
                    if p == "/register":
                        d = self._body()
                        token = bus.register(int(d["session_id"]), d["name"], d.get("task", ""))
                        self._ok({"token": token})
                    elif p == "/task":
                        d = self._body()
                        bus.update_task(d["token"], d["task"]); self._ok({"ok": True})
                    elif p == "/send":
                        d = self._body()
                        mid = bus.send(d["token"], int(d["to"]), d["content"])
                        if mid: self._ok({"id": mid, "status": "pending"})
                        else:   self._err(404, "target agent not found")
                    elif p == "/mcp":
                        sid = (qs.get("sessionId") or [""])[0] or \
                              self.headers.get("Mcp-Session-Id", "")
                        self._mcp_post(sid)
                    else:
                        self._err(404, "not found")
                except Exception as e:
                    self._err(400, str(e))

            def do_GET(self):
                try:
                    parsed = urlparse(self.path)
                    p      = parsed.path
                    qs     = parse_qs(parsed.query)
                    tok    = self.headers.get("X-Token", "")
                    if p == "/agents":       self._ok(bus.list_agents(tok))
                    elif p == "/inbox":      self._ok(bus.get_inbox(tok))
                    elif p == "/mcp/sse":
                        sid = (qs.get("sessionId") or [uuid.uuid4().hex])[0]
                        self._mcp_sse(sid)
                    else:                    self._err(404, "not found")
                except Exception as e:
                    self._err(400, str(e))

        self._server = _ThreadedHTTPServer(("127.0.0.1", 0), _Handler)
        self.port    = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.port

    def stop(self):
        if self._server:
            self._server.shutdown()

    # ── MCP permission resolution ─────────────────────────────────────────────

    def resolve_permission(self, perm_id: str, approved: bool):
        """Called from the Qt main thread after the human decides."""
        with self._mcp_lock:
            ev = self._perm_events.get(perm_id)
            self._perm_results[perm_id] = {"decision": "allow" if approved else "deny"}
        if ev:
            ev.set()

    # ── agent management ──────────────────────────────────────────────────────

    def register(self, session_id: int, name: str, task: str,
                 extras: Optional[Dict] = None) -> str:
        with self._lock:
            old = self._by_session.get(session_id)
            if old: self._agents.pop(old, None)
            token = uuid.uuid4().hex
            e = extras or {}
            self._agents[token] = NeuralAgent(
                session_id=session_id, name=name, task=task, token=token,
                tag=e.get("tag", ""), app=e.get("app", ""), role=e.get("role", ""))
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
                {"session_id": a.session_id, "name": a.name, "tag": a.tag,
                 "app": a.app, "role": a.role, "task": a.task, "last_seen": a.last_seen}
                for t, a in self._agents.items() if t != token
            ]

    def all_agents(self) -> List[NeuralAgent]:
        with self._lock:
            return list(self._agents.values())

    # ── messaging ─────────────────────────────────────────────────────────────

    def send(self, token: str, to_session_id: int, content: str) -> Optional[str]:
        """Immediately delivers the message. The receiving agent's Claude Code
        handles any approval UX itself via its own tool-permission prompts."""
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
        self._on_message(msg)
        return msg.id

    def sender_name(self, session_id: int) -> str:
        with self._lock:
            for a in self._agents.values():
                if a.session_id == session_id: return a.name
            return f"Session {session_id}"

    def recent_messages(self, limit: int = 20) -> List[dict]:
        """All recent messages for the panel history view."""
        with self._lock:
            out = []
            for m in self._messages[-limit:]:
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

    def get_inbox(self, token: str) -> List[dict]:
        """Unread messages for an agent (backup for when PTY injection misses)."""
        with self._lock:
            a = self._agents.get(token)
            if not a: return []
            result = []
            for m in self._messages:
                if m.status == "read": continue
                if m.to_session != a.session_id and m.to_session != -1: continue
                m.status = "read"
                sender = next((x for x in self._agents.values()
                               if x.session_id == m.from_session), None)
                result.append({"id": m.id,
                               "from": sender.name if sender else f"Session {m.from_session}",
                               "content": m.content,
                               "timestamp": m.timestamp})
        return result


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
  neural brain
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
    print(f"Message delivered (id: {r['id']})")

def _broadcast(a):
    if not a: sys.exit('Usage: neural broadcast "<message>"')
    t = _tok()
    if not t: sys.exit("Not registered.")
    r = _req("POST", "/send", {"token": t, "to": -1, "content": a[0]})
    print(f"Broadcast delivered (id: {r['id']})")

def _inbox(_):
    t = _tok()
    if not t: sys.exit("Not registered.")
    msgs = _req("GET", "/inbox", token=t)
    if not msgs: print("No messages."); return
    for m in msgs:
        print(f"  From {m['from']}: {m['content']}")

def _brain(_):
    bf = os.environ.get("AIDE_NEURAL_BRAIN_FILE", "")
    if not bf: sys.exit("AIDE_NEURAL_BRAIN_FILE not set — are you inside an AIDE terminal?")
    try:
        content = open(bf, encoding="utf-8").read()
        if not content.strip(): print("(neural brain is empty)"); return
        print(content)
    except FileNotFoundError:
        print("(neural brain is empty)")

_CMDS = {"register": _register, "task": _task, "agents": _agents,
         "send": _send, "broadcast": _broadcast, "inbox": _inbox, "brain": _brain}

if __name__ == "__main__":
    if not URL: sys.exit("AIDE_NEURAL_URL not set — are you inside an AIDE terminal?")
    args = sys.argv[1:]
    if not args or args[0] not in _CMDS:
        print(__doc__); sys.exit(1)
    _CMDS[args[0]](args[1:])
'''


_CLAUDE_WRAPPER_SRC = '''\
#!/bin/bash
# AIDE claude wrapper — auto-injects --permission-prompt-tool when AIDE_PERMISSION_TOOL is set.
_dir="$(cd "$(dirname "$0")" && pwd)"
_stripped="${PATH//$_dir:/}"
_stripped="${_stripped//:$_dir/}"
_real="$(PATH="$_stripped" which claude 2>/dev/null)"
if [ -z "$_real" ]; then
  echo "error: claude not found in PATH (outside AIDE wrapper dir)" >&2
  exit 127
fi
_args=()
if [ -n "$AIDE_PERMISSION_TOOL" ]; then
  case " $* " in
    *"--permission-prompt-tool"*) ;;
    *) _args+=(--permission-prompt-tool "$AIDE_PERMISSION_TOOL") ;;
  esac
fi
exec "$_real" "${_args[@]}" "$@"
'''


def write_client(directory: str) -> str:
    """Write the `neural` and `claude` wrapper scripts to *directory*."""
    os.makedirs(directory, exist_ok=True)
    for name, src in [("neural", _CLIENT_SRC), ("claude", _CLAUDE_WRAPPER_SRC)]:
        path = os.path.join(directory, name)
        with open(path, "w") as f:
            f.write(src)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return os.path.join(directory, "neural")
