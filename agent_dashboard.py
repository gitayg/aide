"""agent_dashboard.py — Dense agent-status table replacing the terminal as the main view.

No imports from AIDE.py — receives data as plain dicts to avoid circular imports.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QDialog, QPlainTextEdit, QDialogButtonBox, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush

# ── Theme (mirrors AIDE.py constants) ─────────────────────────────────────────
_BG      = "#0d1117"
_FG      = "#e6edf3"
_ACCENT  = "#58a6ff"
_MUTED   = "#7d8590"
_SURFACE = "#21262d"
_PANEL   = "#161b22"
_GREEN   = "#3fb950"
_RED     = "#f85149"
_ORANGE  = "#f0883e"

_STATUS_ORDER = ["waiting", "thinking", "working", "idle"]
_STATUS_COLOR = {
    "waiting":    _ORANGE,
    "thinking":   _ACCENT,
    "working":    _GREEN,
    "idle":       _MUTED,
    "validate":   _RED,
    "task_error": _RED,
}
_STATUS_LABEL = {
    "waiting":    "Pending Answer",
    "thinking":   "Thinking",
    "working":    "Working",
    "idle":       "Idle",
    "validate":   "Needs Validation",
    "task_error": "Task Error",
}

_TBL_SS = f"""
QTableWidget {{
    background: {_BG};
    color: {_FG};
    border: none;
    font-family: Menlo, Consolas, Monospace;
    font-size: 12px;
    gridline-color: {_SURFACE};
    selection-background-color: {_ACCENT}22;
    outline: none;
}}
QHeaderView::section {{
    background: {_PANEL};
    color: {_MUTED};
    border: none;
    border-bottom: 1px solid {_SURFACE};
    font-size: 10px;
    padding: 4px 8px;
    font-weight: bold;
    letter-spacing: 1px;
}}
QTableWidget::item {{ padding: 4px 8px; border: none; }}
QTableWidget::item:selected {{ background: {_ACCENT}22; color: {_FG}; }}
QScrollBar:vertical {{ background: {_BG}; width: 6px; border: none; }}
QScrollBar::handle:vertical {{ background: {_SURFACE}; border-radius: 3px; min-height: 20px; }}
"""

_BTN_SS = (
    f"QPushButton{{background:{_SURFACE};color:{_FG};border:none;"
    f"border-radius:3px;font-size:11px;padding:3px 10px;}}"
    f"QPushButton:hover{{background:{_ACCENT}33;color:{_ACCENT};}}"
)
_SEARCH_SS = (
    f"QLineEdit{{background:{_SURFACE};color:{_FG};border:1px solid {_SURFACE};"
    f"border-radius:4px;font-size:12px;padding:4px 10px;}}"
    f"QLineEdit:focus{{border-color:{_ACCENT};}}"
)

_COL_DOT     = 0
_COL_NAME    = 1
_COL_STATUS  = 2
_COL_ACTIVE  = 3
_COL_TAGS    = 4
_COL_DIR     = 5
_COL_CMD     = 6
_COL_MODEL   = 7
_COL_TOKENS  = 8
_COL_ACCT    = 9
_COL_ACTIONS = 10
_N_COLS      = 11


def _fmt_age(ts: float) -> str:
    if ts <= 0:
        return "—"
    age = time.time() - ts
    if age < 60:   return f"{int(age)}s ago"
    if age < 3600: return f"{int(age/60)}m ago"
    if age < 86400:return f"{int(age/3600)}h ago"
    return f"{int(age/86400)}d ago"


_STATUS_SORT = {"task_error": 0, "validate": 1, "waiting": 2, "thinking": 3, "working": 4, "idle": 5}
_SORT_ROLE   = Qt.ItemDataRole.UserRole + 1


class _SortableItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        a = self.data(_SORT_ROLE)
        b = other.data(_SORT_ROLE)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return super().__lt__(other)


class _ValidationDialog(QDialog):
    """Non-blocking dialog to set a pending-validation note."""
    note_accepted = pyqtSignal(str)

    def __init__(self, agent_name: str, existing_note: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Pending Validation — {agent_name}")
        self.setFixedWidth(480)
        self.setStyleSheet(f"QDialog{{background:{_BG};color:{_FG};}}")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lbl = QLabel("Note what the agent has done (users will see this):")
        lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        lay.addWidget(lbl)
        self._edit = QPlainTextEdit(existing_note)
        self._edit.setStyleSheet(
            f"QPlainTextEdit{{background:{_SURFACE};color:{_FG};"
            f"border:1px solid {_SURFACE};border-radius:4px;"
            f"font-family:Menlo,Consolas,Monospace;font-size:11px;padding:6px;}}")
        self._edit.setMinimumHeight(120)
        lay.addWidget(self._edit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.setStyleSheet(
            f"QPushButton{{background:{_SURFACE};color:{_FG};border:none;"
            f"border-radius:4px;padding:5px 16px;}}")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.close)
        lay.addWidget(bb)

    def _on_accept(self):
        self.note_accepted.emit(self._edit.toPlainText().strip())
        self.close()


class _ChatBar(QWidget):
    message_sent = pyqtSignal(str)
    closed       = pyqtSignal()

    def __init__(self, agent_name: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{_PANEL};border-top:1px solid {_SURFACE};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)
        lbl = QLabel(f"→ {agent_name}:")
        lbl.setStyleSheet(
            f"color:{_ACCENT};font-size:12px;font-weight:bold;min-width:120px;")
        lay.addWidget(lbl)
        self._inp = QLineEdit()
        self._inp.setPlaceholderText("Type a task and press Enter — claude runs once and exits…")
        self._inp.setStyleSheet(_SEARCH_SS)
        self._inp.returnPressed.connect(self._send)
        lay.addWidget(self._inp, 1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_MUTED};border:none;"
            f"font-size:14px;}}QPushButton:hover{{color:{_RED};}}")
        close_btn.clicked.connect(self.closed)
        lay.addWidget(close_btn)
        self._inp.setFocus()

    def _send(self):
        txt = self._inp.text().strip()
        if txt:
            self.message_sent.emit(txt)
            self._inp.clear()


class AgentTable(QWidget):
    """Dense table of all running agents with status, tags, and quick actions.

    Signals:
        open_terminal(tid)             — open terminal view for this agent
        new_agent()                    — add a new agent terminal
        launch_agent(tid)              — re-launch autostart for this agent
        send_message(tid, msg)         — send a chat message to agent terminal
        set_validation(tid, note, on)  — mark/unmark pending validation
    """

    open_terminal  = pyqtSignal(int)
    open_detail    = pyqtSignal(int)
    new_agent      = pyqtSignal()
    launch_agent   = pyqtSignal(int)
    send_message   = pyqtSignal(int, str)
    set_validation = pyqtSignal(int, str, bool)
    run_task       = pyqtSignal(int, str)   # tid, task text — one-shot agent run

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: List[dict] = []
        self._tag_filter: set = set()
        self._search: str = ""
        self._chat_tid: int = -1
        self._chat_bar: Optional[_ChatBar] = None
        self._build()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # toolbar
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f"background:{_PANEL};border-bottom:1px solid {_SURFACE};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(8)

        self._tag_area = QWidget()
        self._tag_area.setStyleSheet("background:transparent;")
        self._tag_lay = QHBoxLayout(self._tag_area)
        self._tag_lay.setContentsMargins(0, 0, 0, 0)
        self._tag_lay.setSpacing(4)
        self._all_tag_btn = QPushButton("All")
        self._all_tag_btn.setCheckable(True)
        self._all_tag_btn.setChecked(True)
        self._all_tag_btn.setStyleSheet(self._tag_btn_ss(True))
        self._all_tag_btn.clicked.connect(self._clear_tag_filter)
        self._tag_lay.addWidget(self._all_tag_btn)
        bl.addWidget(self._tag_area, 1)

        self._search_inp = QLineEdit()
        self._search_inp.setPlaceholderText("Search agents…")
        self._search_inp.setFixedWidth(200)
        self._search_inp.setStyleSheet(_SEARCH_SS)
        self._search_inp.textChanged.connect(self._on_search)
        bl.addWidget(self._search_inp)

        add_btn = QPushButton("+ Agent")
        add_btn.setStyleSheet(_BTN_SS)
        add_btn.setToolTip("Add a new agent terminal")
        add_btn.clicked.connect(self.new_agent)
        bl.addWidget(add_btn)

        root.addWidget(bar)

        # table
        self._tbl = QTableWidget(0, _N_COLS)
        self._tbl.setHorizontalHeaderLabels(
            ["●", "Name", "Status", "Last Active", "Tags", "Dir", "Command",
             "Model", "Tokens", "Account", "Actions"])
        h = self._tbl.horizontalHeader()
        h.setSectionResizeMode(_COL_DOT,     QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(_COL_NAME,    QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(_COL_STATUS,  QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(_COL_ACTIVE,  QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(_COL_TAGS,    QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(_COL_DIR,     QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(_COL_CMD,     QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(_COL_MODEL,   QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(_COL_TOKENS,  QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(_COL_ACCT,    QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(_COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        self._tbl.setColumnWidth(_COL_DOT,     28)
        self._tbl.setColumnWidth(_COL_NAME,    150)
        self._tbl.setColumnWidth(_COL_STATUS,  110)
        self._tbl.setColumnWidth(_COL_ACTIVE,  90)
        self._tbl.setColumnWidth(_COL_TAGS,    110)
        self._tbl.setColumnWidth(_COL_CMD,     160)
        self._tbl.setColumnWidth(_COL_MODEL,   90)
        self._tbl.setColumnWidth(_COL_TOKENS,  80)
        self._tbl.setColumnWidth(_COL_ACCT,    80)
        self._tbl.setColumnWidth(_COL_ACTIONS, 170)
        h.setSortIndicatorShown(True)
        h.setSectionsClickable(True)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl.setShowGrid(True)
        self._tbl.setAlternatingRowColors(False)
        self._tbl.setStyleSheet(_TBL_SS)
        self._tbl.verticalHeader().setDefaultSectionSize(38)
        self._tbl.doubleClicked.connect(self._on_double_click)
        self._tbl.selectionModel().selectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._tbl, 1)

        # chat bar (hidden by default)
        self._chat_container = QWidget()
        self._chat_container.setFixedHeight(54)
        self._chat_container.setVisible(False)
        self._chat_lay = QVBoxLayout(self._chat_container)
        self._chat_lay.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._chat_container)

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(self, sessions: List[dict]):
        self._sessions = sessions
        self._rebuild_tags()
        self._repopulate()

    # ── Tag management ─────────────────────────────────────────────────────────

    @staticmethod
    def _tag_btn_ss(active: bool) -> str:
        if active:
            return (f"QPushButton{{background:{_ACCENT}33;color:{_ACCENT};"
                    f"border:1px solid {_ACCENT}66;border-radius:10px;"
                    f"font-size:11px;padding:2px 10px;}}")
        return (f"QPushButton{{background:{_SURFACE};color:{_MUTED};"
                f"border:1px solid {_SURFACE};border-radius:10px;"
                f"font-size:11px;padding:2px 10px;}}"
                f"QPushButton:hover{{color:{_FG};}}")

    def _rebuild_tags(self):
        all_tags: set = set()
        for s in self._sessions:
            for t in s.get("tags", []):
                all_tags.add(t)
        while self._tag_lay.count() > 1:
            item = self._tag_lay.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()
        for tag in sorted(all_tags):
            active = tag in self._tag_filter
            btn = QPushButton(tag)
            btn.setCheckable(True)
            btn.setChecked(active)
            btn.setStyleSheet(self._tag_btn_ss(active))
            btn.clicked.connect(lambda _checked, t=tag, b=btn: self._toggle_tag(t, b))
            self._tag_lay.addWidget(btn)

    def _clear_tag_filter(self):
        self._tag_filter.clear()
        self._sync_tag_buttons()
        self._all_tag_btn.setChecked(True)
        self._repopulate()

    def _toggle_tag(self, tag: str, btn: QPushButton):
        shift = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not shift:
            # Single-select: clear all others, then toggle this one
            already_only = self._tag_filter == {tag}
            self._tag_filter.clear()
            self._sync_tag_buttons()
            if not already_only:
                self._tag_filter.add(tag)
        else:
            if tag in self._tag_filter:
                self._tag_filter.discard(tag)
            else:
                self._tag_filter.add(tag)
        self._sync_tag_buttons()
        self._all_tag_btn.setChecked(not self._tag_filter)
        self._repopulate()

    def _sync_tag_buttons(self):
        for i in range(1, self._tag_lay.count()):
            w = self._tag_lay.itemAt(i).widget()
            if isinstance(w, QPushButton):
                active = w.text() in self._tag_filter
                w.setChecked(active)
                w.setStyleSheet(self._tag_btn_ss(active))

    def _on_search(self, text: str):
        self._search = text.lower()
        self._repopulate()

    # ── Table population ───────────────────────────────────────────────────────

    def _filtered(self) -> List[dict]:
        result = []
        for s in self._sessions:
            if self._tag_filter and not self._tag_filter.intersection(set(s.get("tags", []))):
                continue
            if self._search:
                name = s.get("name", "").lower()
                tags = " ".join(s.get("tags", [])).lower()
                if self._search not in name and self._search not in tags:
                    continue
            result.append(s)
        order = {st: i for i, st in enumerate(_STATUS_ORDER)}
        result.sort(key=lambda s: (
            0 if s.get("pending_validation") else 1,
            order.get(s.get("status", "idle"), 99),
            -(s.get("last_active", 0)),
        ))
        return result

    def _repopulate(self):
        sessions = self._filtered()
        self._tbl.setSortingEnabled(False)
        self._tbl.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            tid         = s.get("tid", -1)
            task_result = s.get("task_result", "")
            if task_result:
                status = "task_error"
            elif s.get("pending_validation"):
                status = "validate"
            else:
                status = s.get("status", "idle")
            color  = _STATUS_COLOR.get(status, _MUTED)
            label  = _STATUS_LABEL.get(status, status.capitalize())

            def _item(text: str, sort_key=None) -> _SortableItem:
                it = _SortableItem(text)
                it.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                it.setData(Qt.ItemDataRole.UserRole, tid)
                if sort_key is not None:
                    it.setData(_SORT_ROLE, sort_key)
                return it

            dot = _SortableItem("●")
            dot.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setForeground(QBrush(QColor(color)))
            dot.setData(Qt.ItemDataRole.UserRole, tid)
            dot.setData(_SORT_ROLE, _STATUS_SORT.get(status, 99))
            raw_model = s.get("model", "") or ""
            model_short = raw_model.split("-")[1] if raw_model and "-" in raw_model else (raw_model or "default")
            tokens = s.get("tokens_used", 0)
            tok_str = f"{tokens:,}" if tokens else "—"
            ts = s.get("last_active", 0) or 0
            cmd_item = _item(s.get("cmd", ""))
            sid = s.get("session_id", "")
            if sid:
                cmd_item.setToolTip(f"Session: {sid}")
            neural = s.get("neural_on_bus", False)
            name_str = ("⬡ " if neural else "") + s.get("name", f"Agent {tid}")
            name_item = _item(name_str)
            if neural:
                name_item.setToolTip("Connected to Neural Brain")
            status_item = _item(label, _STATUS_SORT.get(status, 99))
            if task_result:
                status_item.setToolTip(task_result)
            self._tbl.setItem(row, _COL_DOT,    dot)
            self._tbl.setItem(row, _COL_NAME,   name_item)
            self._tbl.setItem(row, _COL_STATUS, status_item)
            self._tbl.setItem(row, _COL_ACTIVE, _item(_fmt_age(ts), -ts))
            self._tbl.setItem(row, _COL_TAGS,   _item(", ".join(s.get("tags", []))))
            self._tbl.setItem(row, _COL_DIR,    _item(s.get("dir", "")))
            self._tbl.setItem(row, _COL_CMD,    cmd_item)
            self._tbl.setItem(row, _COL_MODEL,  _item(model_short))
            self._tbl.setItem(row, _COL_TOKENS, _item(tok_str, tokens))
            self._tbl.setItem(row, _COL_ACCT,   _item(s.get("profile", "") or "default"))
            self._tbl.setCellWidget(row, _COL_ACTIONS,
                                    self._make_action_btns(tid, status, s.get("name", f"Agent {tid}")))

            if status in ("validate", "task_error"):
                bg = QColor(_RED + "22")
            elif status == "waiting":
                bg = QColor(_ORANGE + "18")
            elif status in ("working", "thinking"):
                bg = QColor(_GREEN + "12")
            else:
                bg = QColor(_BG)
            for col in range(_N_COLS):
                item = self._tbl.item(row, col)
                if item:
                    item.setBackground(QBrush(bg))
        self._tbl.setSortingEnabled(True)

    def _tid_at_row(self, row: int) -> int:
        item = self._tbl.item(row, _COL_DOT)
        return item.data(Qt.ItemDataRole.UserRole) if item else -1

    def _on_double_click(self, index):
        if index.column() == _COL_ACTIONS:
            return
        tid = self._tid_at_row(index.row())
        if tid >= 0:
            self.open_detail.emit(tid)

    def _on_selection_changed(self):
        if not self._chat_bar or not self._chat_container.isVisible():
            return
        rows = self._tbl.selectionModel().selectedRows()
        if not rows:
            return
        tid = self._tid_at_row(rows[0].row())
        if tid < 0 or tid == self._chat_tid:
            return
        s = next((x for x in self._sessions if x.get("tid") == tid), None)
        if s:
            self._open_chat(tid, s.get("name", f"Agent {tid}"))

    def _make_action_btns(self, tid: int, status: str, name: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        _ss = (f"QPushButton{{background:{_SURFACE};color:{_FG};border:none;"
               f"border-radius:3px;font-size:10px;padding:2px 8px;}}"
               f"QPushButton:hover{{background:{_ACCENT}33;color:{_ACCENT};}}")

        if status == "waiting":
            ans_btn = QPushButton("Answer")
            ans_btn.setStyleSheet(_ss)
            ans_btn.setToolTip("Open chat to answer this agent")
            ans_btn.clicked.connect(lambda: self._open_chat(tid, name))
            lay.addWidget(ans_btn)
        else:
            rev_btn = QPushButton("Review")
            rev_btn.setStyleSheet(_ss)
            rev_btn.setToolTip("Open terminal to review agent's work")
            rev_btn.clicked.connect(lambda: self.open_terminal.emit(tid))
            lay.addWidget(rev_btn)

        commit_btn = QPushButton("Commit")
        commit_btn.setStyleSheet(_ss)
        commit_btn.setToolTip("Ask agent to commit staged changes")
        commit_btn.clicked.connect(
            lambda: self.run_task.emit(tid, "commit your staged changes with a descriptive commit message"))
        lay.addWidget(commit_btn)
        lay.addStretch()
        return w

    def _open_chat(self, tid: int, name: str):
        if self._chat_bar:
            self._chat_lay.removeWidget(self._chat_bar)
            self._chat_bar.deleteLater()
            self._chat_bar = None
        if self._chat_tid == tid:
            self._chat_tid = -1
            self._chat_container.setVisible(False)
            return
        self._chat_tid = tid
        bar = _ChatBar(name, self._chat_container)
        self._chat_lay.addWidget(bar)
        bar.message_sent.connect(lambda msg, t=tid: self.run_task.emit(t, msg))
        bar.closed.connect(lambda: self._open_chat(tid, name))
        self._chat_bar = bar
        self._chat_container.setVisible(True)

    def _ask_validation(self, tid: int, name: str, existing: str):
        dlg = _ValidationDialog(name, existing, self)
        dlg.note_accepted.connect(
            lambda note, t=tid: self.set_validation.emit(t, note, True))
        dlg.show()

    def _ask_redirect_task(self, tid: int, agent_name: str):
        dlg = _TaskInputDialog(agent_name, self)
        dlg.task_accepted.connect(lambda task, t=tid: self.run_task.emit(t, task))
        dlg.show()


class _TaskInputDialog(QDialog):
    """Ask the user what task to send to the target agent (on-demand run)."""
    task_accepted = pyqtSignal(str)

    def __init__(self, agent_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Redirect task to {agent_name}")
        self.setFixedWidth(500)
        self.setStyleSheet(f"QDialog{{background:{_BG};color:{_FG};}}")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lbl = QLabel(f"Task for <b>{agent_name}</b> (agent will run once and exit):")
        lbl.setStyleSheet(f"color:{_MUTED};font-size:11px;")
        lay.addWidget(lbl)
        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText("Describe the task…")
        self._edit.setStyleSheet(
            f"QPlainTextEdit{{background:{_SURFACE};color:{_FG};"
            f"border:1px solid {_SURFACE};border-radius:4px;"
            f"font-family:Menlo,Consolas,Monospace;font-size:11px;padding:6px;}}")
        self._edit.setMinimumHeight(100)
        lay.addWidget(self._edit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.setStyleSheet(
            f"QPushButton{{background:{_SURFACE};color:{_FG};border:none;"
            f"border-radius:4px;padding:5px 16px;}}")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.close)
        lay.addWidget(bb)

    def _on_accept(self):
        task = self._edit.toPlainText().strip()
        if task:
            self.task_accepted.emit(task)
        self.close()
