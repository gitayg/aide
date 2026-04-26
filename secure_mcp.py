"""secure_mcp.py — Secure MCP (Model Context Protocol) SSE server for AIDE.

Hosts the MCP protocol layer: SSE event stream, JSON-RPC dispatch, tool
registration. Independent of the HTTP transport — neural.py wires this
into its existing HTTP server.

Tools are registered with `register_tool(name, description, schema, handler)`.
The handler runs in a daemon thread when invoked (so blocking calls like
the permission_prompt dialog don't stall the HTTP request).

The "Secure" prefix reflects that this server only binds to 127.0.0.1
and only exposes tools registered explicitly by AIDE — it doesn't surface
arbitrary local resources or filesystem access.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Callable, Dict, Optional


class SecureMCP:
    def __init__(self, server_name: str = "aide-neural",
                 server_version: str = "1.0.0"):
        self.name = server_name
        self.version = server_version
        self._tools: Dict[str, dict] = {}
        self._sessions: Dict[str, queue.Queue] = {}
        self._lock = threading.Lock()

    # ── Tool registration ────────────────────────────────────────────────────

    def register_tool(self, name: str, description: str,
                      input_schema: dict,
                      handler: Callable[[dict], str]) -> None:
        """Register a tool. handler(arguments_dict) → text content (str)."""
        self._tools[name] = {
            "description": description,
            "schema":      input_schema,
            "handler":     handler,
        }

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    # ── SSE session lifecycle ────────────────────────────────────────────────

    def open_sse(self, sid: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._sessions[sid] = q
        return q

    def close_sse(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def push_to_session(self, sid: str, msg: dict) -> None:
        with self._lock:
            q = self._sessions.get(sid)
        if q is not None:
            q.put(msg)

    # ── JSON-RPC dispatch ────────────────────────────────────────────────────

    def handle_jsonrpc(self, sid: str, msg: dict) -> None:
        """Process one JSON-RPC message from a client. Responses are pushed
        to the session's SSE queue (per MCP SSE transport spec)."""
        meth = msg.get("method", "")
        rid  = msg.get("id")

        if meth == "initialize":
            self.push_to_session(sid, {
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities":    {"tools": {}},
                    "serverInfo":      {"name": self.name, "version": self.version},
                },
            })
            return

        if meth == "notifications/initialized":
            return  # one-way notification

        if meth == "tools/list":
            tools_list = [
                {
                    "name":        n,
                    "description": t["description"],
                    "inputSchema": t["schema"],
                }
                for n, t in self._tools.items()
            ]
            self.push_to_session(sid, {
                "jsonrpc": "2.0", "id": rid,
                "result":  {"tools": tools_list},
            })
            return

        if meth == "tools/call":
            params = msg.get("params", {}) or {}
            name   = params.get("name", "")
            args   = params.get("arguments", {}) or {}
            tool   = self._tools.get(name)
            if not tool:
                self.push_to_session(sid, {
                    "jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"Unknown tool: {name}"},
                })
                return

            def _call():
                try:
                    text = tool["handler"](args)
                except Exception as e:
                    self.push_to_session(sid, {
                        "jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"},
                    })
                    return
                self.push_to_session(sid, {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": str(text)}],
                    },
                })
            threading.Thread(target=_call, daemon=True).start()
            return

        if rid is not None:
            self.push_to_session(sid, {
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"Method not found: {meth}"},
            })
