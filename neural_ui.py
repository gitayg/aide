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
    def __init__(self, name: str, session_id: int, task: str, last_seen: float,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet(_CARD_SS)
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 6, 8, 6); lay.setSpacing(2)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        name_lbl = QLabel(f"⬡ {name}")
        name_lbl.setStyleSheet(f"color:{_C_FG.name()};font-weight:bold;font-size:11px;"
                               f"background:transparent;")
        sid_lbl = QLabel(f"[{session_id}]")
        sid_lbl.setStyleSheet(_MUTED_SS)
        hdr.addWidget(name_lbl); hdr.addStretch(); hdr.addWidget(sid_lbl)
        lay.addLayout(hdr)

        if task:
            task_lbl = QLabel(task)
            task_lbl.setStyleSheet(f"color:{_C_YELLOW.name()};font-size:10px;"
                                   f"background:transparent;")
            task_lbl.setWordWrap(True)
            lay.addWidget(task_lbl)

        seen_lbl = QLabel(_ts(last_seen))
        seen_lbl.setStyleSheet(_MUTED_SS)
        lay.addWidget(seen_lbl)


class _PendingCard(QFrame):
    approved = pyqtSignal(str)
    denied   = pyqtSignal(str)

    def __init__(self, msg_id: str, from_name: str, to_name: str,
                 content: str, timestamp: float, parent=None):
        super().__init__(parent)
        self.msg_id = msg_id
        self.setStyleSheet(f"background:{_C_BG.name()};"
                           f"border:1px solid {_C_ACCENT.name()}44;"
                           f"border-left:3px solid {_C_ACCENT.name()};"
                           f"border-radius:4px;")
        lay = QVBoxLayout(self); lay.setContentsMargins(8, 6, 8, 6); lay.setSpacing(4)

        # header: from → to
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

        btns = QHBoxLayout(); btns.setSpacing(6)
        approve_btn = QPushButton("✓ Allow"); approve_btn.setStyleSheet(_APPROVE_SS)
        deny_btn    = QPushButton("✗ Deny");  deny_btn.setStyleSheet(_DENY_SS)
        approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        approve_btn.clicked.connect(lambda: self.approved.emit(msg_id))
        deny_btn.clicked.connect(lambda: self.denied.emit(msg_id))
        btns.addWidget(approve_btn); btns.addWidget(deny_btn); btns.addStretch()
        lay.addLayout(btns)


# ── main panel ────────────────────────────────────────────────────────────────

class NeuralPanel(QWidget):
    """Side panel showing registered agents and pending inter-agent messages."""

    approval_made = pyqtSignal(str, bool)  # (msg_id, approved)

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
        title = QLabel("🧠  Neural")
        title.setStyleSheet(_HDR_SS)
        self._badge = QLabel("0")
        self._badge.setStyleSheet(
            f"background:{_C_ACCENT.name()};color:#000;font-size:10px;"
            f"font-weight:bold;border-radius:7px;padding:1px 6px;min-width:14px;")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setVisible(False)
        hdr.addWidget(title); hdr.addWidget(self._badge); hdr.addStretch()
        root.addLayout(hdr)

        # ── pending approvals ─────────────────────────────────────────────────
        self._pending_hdr = QLabel("Pending approval")
        self._pending_hdr.setStyleSheet(_HDR_SS + "font-size:10px;")
        self._pending_hdr.setVisible(False)
        root.addWidget(self._pending_hdr)

        self._pending_area = QScrollArea()
        self._pending_area.setWidgetResizable(True)
        self._pending_area.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._pending_area.setSizePolicy(QSizePolicy.Policy.Expanding,
                                         QSizePolicy.Policy.Preferred)
        self._pending_area.setVisible(False)
        self._pending_inner = QWidget()
        self._pending_inner.setStyleSheet("background:transparent;")
        self._pending_lay = QVBoxLayout(self._pending_inner)
        self._pending_lay.setContentsMargins(0, 0, 0, 0)
        self._pending_lay.setSpacing(4)
        self._pending_lay.addStretch()
        self._pending_area.setWidget(self._pending_inner)
        root.addWidget(self._pending_area)

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

        # ── refresh timer ─────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1500)

        self._known_pending: set = set()

    # ── public ────────────────────────────────────────────────────────────────

    def notify_new_request(self):
        """Called when a new message arrives for approval."""
        self.refresh()

    def refresh(self):
        self._refresh_pending()
        self._refresh_agents()

    # ── internals ─────────────────────────────────────────────────────────────

    def _refresh_pending(self):
        pending = self._bus.get_pending()
        ids = {p["id"] for p in pending}

        # Remove cards whose messages are no longer pending
        for i in range(self._pending_lay.count() - 1, -1, -1):
            item = self._pending_lay.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _PendingCard) and w.msg_id not in ids:
                self._pending_lay.takeAt(i)
                w.deleteLater()

        # Add new cards
        existing = {w.msg_id for i in range(self._pending_lay.count())
                    if isinstance((w := self._pending_lay.itemAt(i).widget()), _PendingCard)}
        for p in pending:
            if p["id"] not in existing:
                card = _PendingCard(p["id"], p["from_name"], p["to_name"],
                                    p["content"], p["timestamp"])
                card.approved.connect(self._on_approve)
                card.denied.connect(self._on_deny)
                self._pending_lay.insertWidget(self._pending_lay.count() - 1, card)

        n = len(pending)
        self._pending_hdr.setVisible(n > 0)
        self._pending_area.setVisible(n > 0)
        self._badge.setText(str(n)); self._badge.setVisible(n > 0)

    def _refresh_agents(self):
        agents = self._bus.all_agents()

        # Remove all agent cards and rebuild (list is small)
        for i in range(self._agents_lay.count() - 1, -1, -1):
            item = self._agents_lay.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _AgentCard):
                self._agents_lay.takeAt(i); w.deleteLater()

        self._no_agents.setVisible(len(agents) == 0)
        for a in agents:
            card = _AgentCard(a.name, a.session_id, a.task, a.last_seen)
            self._agents_lay.insertWidget(self._agents_lay.count() - 1, card)

    def _on_approve(self, msg_id: str):
        self._bus.approve(msg_id)
        self.approval_made.emit(msg_id, True)
        self.refresh()

    def _on_deny(self, msg_id: str):
        self._bus.deny(msg_id)
        self.approval_made.emit(msg_id, False)
        self.refresh()
