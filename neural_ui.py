"""neural_ui.py — NeuralPanel Qt widget for AIDE."""
from __future__ import annotations

import time
from typing import Callable, Dict, List

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from neural import NeuralBus


# ── colours (match AIDE palette) ─────────────────────────────────────────────
_C_PANEL   = QColor("#1e1e2e")
_C_BG      = QColor("#11111b")
_C_SURFACE = QColor("#313244")
_C_ACCENT  = QColor("#cba6f7")
_C_FG      = QColor("#cdd6f4")
_C_MUTED   = QColor("#6c7086")
_C_GREEN   = QColor("#a6e3a1")
_C_RED     = QColor("#f38ba8")
_C_YELLOW  = QColor("#f9e2af")

_PANEL_SS  = f"background:{_C_PANEL.name()};border-left:1px solid {_C_SURFACE.name()};"
_HDR_SS    = (f"color:{_C_ACCENT.name()};font-weight:bold;font-size:12px;"
              f"background:transparent;")
_MUTED_SS  = f"color:{_C_MUTED.name()};font-size:10px;background:transparent;"
_CARD_SS   = (f"background:{_C_BG.name()};border:1px solid {_C_SURFACE.name()};"
              f"border-radius:4px;")
_APPROVE_SS = (f"QPushButton{{background:{_C_GREEN.name()};color:#000;border:none;"
               f"font-size:10px;font-weight:bold;border-radius:3px;padding:2px 8px;}}"
               f"QPushButton:hover{{background:#b9f5b4;}}")
_DENY_SS    = (f"QPushButton{{background:{_C_RED.name()};color:#000;border:none;"
               f"font-size:10px;font-weight:bold;border-radius:3px;padding:2px 8px;}}"
               f"QPushButton:hover{{background:#f5a5b8;}}")


def _ts(t: float) -> str:
    delta = time.time() - t
    if delta < 60:    return f"{int(delta)}s ago"
    if delta < 3600:  return f"{int(delta/60)}m ago"
    return f"{int(delta/3600)}h ago"


# ── individual cards ──────────────────────────────────────────────────────────

class _AgentCard(QFrame):
    def __init__(self, name: str, session_id: int, tag: str, app: str,
                 role: str, task: str, last_seen: float, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_CARD_SS)
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 6, 8, 6); lay.setSpacing(2)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        name_lbl = QLabel(f"🤖 {name}")
        name_lbl.setStyleSheet(f"color:{_C_FG.name()};font-weight:bold;font-size:11px;"
                               f"background:transparent;")
        sid_lbl = QLabel(f"[{session_id}]")
        sid_lbl.setStyleSheet(_MUTED_SS)
        hdr.addWidget(name_lbl); hdr.addStretch(); hdr.addWidget(sid_lbl)
        lay.addLayout(hdr)

        meta_parts = []
        if tag:  meta_parts.append(f"[{tag}]")
        if app:  meta_parts.append(app)
        if meta_parts:
            meta_lbl = QLabel("  ".join(meta_parts))
            meta_lbl.setStyleSheet(f"color:{_C_ACCENT.name()};font-size:10px;"
                                   f"background:transparent;")
            lay.addWidget(meta_lbl)

        if role:
            role_lbl = QLabel(role)
            role_lbl.setStyleSheet(f"color:{_C_MUTED.name()};font-size:10px;"
                                   f"background:transparent;")
            role_lbl.setWordWrap(True)
            lay.addWidget(role_lbl)

        if task:
            task_lbl = QLabel(task)
            task_lbl.setStyleSheet(f"color:{_C_YELLOW.name()};font-size:10px;"
                                   f"background:transparent;")
            task_lbl.setWordWrap(True)
            lay.addWidget(task_lbl)

        seen_lbl = QLabel(_ts(last_seen))
        seen_lbl.setStyleSheet(_MUTED_SS)
        lay.addWidget(seen_lbl)


class _MessageCard(QFrame):
    """Recent neural message (display-only; no approval buttons)."""
    def __init__(self, from_name: str, to_name: str, content: str,
                 timestamp: float, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{_C_BG.name()};"
                           f"border-left:3px solid {_C_ACCENT.name()};"
                           f"border-radius:4px;")
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 6, 8, 6); lay.setSpacing(2)
        hdr = QLabel(f"{from_name}  →  {to_name}")
        hdr.setStyleSheet(f"color:{_C_ACCENT.name()};font-size:10px;font-weight:bold;"
                          f"background:transparent;")
        lay.addWidget(hdr)
        msg_lbl = QLabel(content)
        msg_lbl.setStyleSheet(f"color:{_C_FG.name()};font-size:11px;background:transparent;")
        msg_lbl.setWordWrap(True)
        lay.addWidget(msg_lbl)
        ts_lbl = QLabel(_ts(timestamp))
        ts_lbl.setStyleSheet(_MUTED_SS)
        lay.addWidget(ts_lbl)


# ── main panel ────────────────────────────────────────────────────────────────

class NeuralPanel(QWidget):
    """Side panel showing registered agents and recent neural messages."""

    def __init__(self, bus: NeuralBus, parent=None):
        super().__init__(parent)
        self._bus = bus
        self.setMinimumWidth(180)
        self.setStyleSheet(_PANEL_SS)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(8)

        # ── header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        title = QLabel("🤖  Neural")
        title.setStyleSheet(_HDR_SS)
        hdr.addWidget(title); hdr.addStretch()
        root.addLayout(hdr)

        info = QLabel("Messages are delivered immediately — the receiving "
                      "agent's Claude Code handles any approval itself.")
        info.setStyleSheet(_MUTED_SS); info.setWordWrap(True)
        root.addWidget(info)

        # ── recent messages ───────────────────────────────────────────────────
        msgs_hdr = QLabel("Recent messages")
        msgs_hdr.setStyleSheet(_HDR_SS + "font-size:10px;")
        root.addWidget(msgs_hdr)

        self._msgs_area = QScrollArea()
        self._msgs_area.setWidgetResizable(True)
        self._msgs_area.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._msgs_inner = QWidget()
        self._msgs_inner.setStyleSheet("background:transparent;")
        self._msgs_lay = QVBoxLayout(self._msgs_inner)
        self._msgs_lay.setContentsMargins(0, 0, 0, 0)
        self._msgs_lay.setSpacing(4)
        self._no_msgs = QLabel("No messages yet.")
        self._no_msgs.setStyleSheet(_MUTED_SS + "padding:4px;")
        self._msgs_lay.addWidget(self._no_msgs)
        self._msgs_lay.addStretch()
        self._msgs_area.setWidget(self._msgs_inner)
        root.addWidget(self._msgs_area)

        # ── agents list ───────────────────────────────────────────────────────
        agents_hdr = QLabel("Registered agents")
        agents_hdr.setStyleSheet(_HDR_SS + "font-size:10px;")
        root.addWidget(agents_hdr)

        self._agents_area = QScrollArea()
        self._agents_area.setWidgetResizable(True)
        self._agents_area.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._agents_inner = QWidget()
        self._agents_inner.setStyleSheet("background:transparent;")
        self._agents_lay = QVBoxLayout(self._agents_inner)
        self._agents_lay.setContentsMargins(0, 0, 0, 0)
        self._agents_lay.setSpacing(4)
        self._no_agents = QLabel("No agents connected.\nRun  neural register  inside any terminal.")
        self._no_agents.setStyleSheet(_MUTED_SS + "padding:4px;")
        self._no_agents.setWordWrap(True)
        self._agents_lay.addWidget(self._no_agents)
        self._agents_lay.addStretch()
        self._agents_area.setWidget(self._agents_inner)
        root.addWidget(self._agents_area, 1)

        # ── agent onboarding prompt ───────────────────────────────────────────
        copy_btn = QPushButton("📋  Copy agent prompt")
        copy_btn.setStyleSheet(
            f"QPushButton{{background:{_C_SURFACE.name()};color:{_C_FG.name()};"
            f"border:none;border-radius:3px;font-size:10px;padding:4px 8px;}}"
            f"QPushButton:hover{{background:{_C_ACCENT.name()}44;color:{_C_ACCENT.name()};}}")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_agent_prompt)
        root.addWidget(copy_btn)

        # ── refresh timer ─────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1500)

        self._url = ""  # set by MainWindow after bus starts
        self._seen_msg_ids: set = set()

    # ── public ────────────────────────────────────────────────────────────────

    def notify_new_message(self):
        """Called whenever a new message is delivered (triggers immediate refresh)."""
        self.refresh()

    def refresh(self):
        self._refresh_messages()
        self._refresh_agents()

    # ── internals ─────────────────────────────────────────────────────────────

    def _refresh_messages(self):
        msgs = self._bus.recent_messages(limit=20)
        # Full rebuild — small list, simpler than diffing
        for i in range(self._msgs_lay.count() - 1, -1, -1):
            item = self._msgs_lay.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _MessageCard):
                self._msgs_lay.takeAt(i); w.deleteLater()
        self._no_msgs.setVisible(len(msgs) == 0)
        for m in reversed(msgs):  # newest first
            card = _MessageCard(m["from_name"], m["to_name"],
                                m["content"], m["timestamp"])
            self._msgs_lay.insertWidget(0, card)

    def _refresh_agents(self):
        agents = self._bus.all_agents()
        for i in range(self._agents_lay.count() - 1, -1, -1):
            item = self._agents_lay.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _AgentCard):
                self._agents_lay.takeAt(i); w.deleteLater()
        self._no_agents.setVisible(len(agents) == 0)
        for a in agents:
            card = _AgentCard(a.name, a.session_id, a.tag, a.app,
                              a.role, a.task, a.last_seen)
            self._agents_lay.insertWidget(self._agents_lay.count() - 1, card)

    def set_url(self, url: str):
        self._url = url

    def _copy_agent_prompt(self):
        from PyQt6.QtWidgets import QApplication as _App
        url = self._url or "http://127.0.0.1:<port>"
        prompt = f"""\
## Neural Bus — Agent Operating Instructions

You are an AI agent running inside AIDE, connected to the Neural Bus.
The Neural Bus lets agents working on different tasks coordinate with each other.

Messages you send to other agents are **delivered immediately** — there is
no human approval queue. Whenever the human needs to approve an action you
take, Claude Code's own tool-permission prompts handle that at the point
of action (e.g. before you run a command or edit a file). The bus itself
is a trusted communication channel between agents.

### Your environment
- `AIDE_NEURAL_URL={url}` — the bus HTTP endpoint
- `AIDE_SESSION_ID` — your session number (check with: echo $AIDE_SESSION_ID)
- The `neural` command is on your PATH

### On startup
Run this to announce yourself:
```
neural register "<your role name>" "<what you are working on>"
```
Example: `neural register "AppHub Agent" "Deploying auth service"`

### Discovering other agents
```
neural agents
```
Lists all registered agents with their session IDs, tags, app, role, and current task.

### Sending a message
```
neural send <session_id> "<message>"
```
Delivered immediately to the target agent's terminal. Use this when you
need information from another agent, want to coordinate work, or need to
flag a dependency or conflict.

### Updating your task
```
neural task "<what you are doing now>"
```
Keep this current so other agents know your status.

### Receiving messages
Incoming messages appear directly in your terminal output as a line
prefixed with `# 🤖 neural from [<sender>]:`. You will see them on your
next read of the terminal. You can also run `neural inbox` to see any
messages you missed.

### Guidelines
- Only send messages when genuinely necessary for coordination.
- Be concise.
- When you receive a message, acknowledge it and respond via `neural send`.
- Do not use the bus for routine status updates; use it for cross-agent decisions.
"""
        _App.clipboard().setText(prompt)
