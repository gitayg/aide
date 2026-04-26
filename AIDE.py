#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 Itay Glick. Licensed under the AGPL-3.0-or-later.
# See the LICENSE file in the project root for the full license text.
"""
  ███╗   ██╗ █████╗ ███╗   ██╗ ██████╗      █████╗ ██╗
  ████╗  ██║██╔══██╗████╗  ██║██╔═══██╗    ██╔══██╗██║
  ██╔██╗ ██║███████║██╔██╗ ██║██║   ██║    ███████║██║
  ██║╚██╗██║██╔══██║██║╚██╗██║██║   ██║    ██╔══██║██║
  ██║ ╚████║██║  ██║██║ ╚████║╚██████╔╝    ██║  ██║██║
  ╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝     ╚═╝  ╚═╝╚═╝  v2.0

  AIDE — AI Dev Env  —  Native Desktop App

  Install:  pip install PyQt6 pyte PyQt6-WebEngine
  Run:      python AIDE.py [--shell /bin/zsh] [--reset]

  Key bindings — Windows shortcuts:
    Ctrl+T new tab   Ctrl+W close tab   Ctrl+Tab / Ctrl+Shift+Tab next/prev
    Ctrl+1-9 jump to tab   ± focus notes/tasks panel
  Legacy Ctrl+B prefix also works:
    n/w/r/←/→/| /b/p/y/v/x/c/s/d
"""
from __future__ import annotations

import json, os, platform, queue, re, shlex, signal, struct, subprocess
import sys, threading, time, webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote
from urllib.request import urlopen, Request

IS_WINDOWS = platform.system() == "Windows"
IS_MAC     = platform.system() == "Darwin"
IS_LINUX   = platform.system() == "Linux"

if not IS_WINDOWS:
    import fcntl, pty, select, termios

def _check_deps():
    _DEPS = [
        # (pip name,          import name,                   required, description)
        ("pyte",              "pyte",                        True,  "Terminal emulation (ANSI/VT100)"),
        ("PyQt6",             "PyQt6",                       True,  "GUI framework"),
        ("PyQt6-WebEngine",   "PyQt6.QtWebEngineWidgets",    False, "Embedded Chromium browser (optional — falls back to text)"),
        ("cryptography",      "cryptography.fernet",         True,  "Encrypted variables vault (AES-128-CBC + HMAC via Fernet)"),
        ("keyring",           "keyring",                     True,  "Stores the vault key in the macOS login Keychain"),
    ]
    any_failed = False
    for pkg, import_name, required, desc in _DEPS:
        try:
            __import__(import_name)
            continue
        except ImportError:
            pass
        tag   = "required" if required else "optional"
        sym   = "✗" if required else "○"
        print(f"\n  {sym}  Missing [{tag}]: {pkg}")
        print(f"     {desc}")
        if sys.stdin.isatty():
            try:
                ans = input(f"     Install automatically? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
        else:
            ans = "n"
        if ans in ("", "y", "yes"):
            print(f"     Installing {pkg}…")
            rc = subprocess.run([sys.executable, "-m", "pip", "install", pkg]).returncode
            if rc == 0:
                print(f"     ✓  {pkg} installed.")
            else:
                print(f"     ✗  Install failed. Try:  pip install {pkg}")
                if required: any_failed = True
        else:
            if required:
                print(f"     Skipped — {pkg} is required. Exiting.")
                any_failed = True
            else:
                print(f"     Skipped — browser will use plain-text fallback.")
    if any_failed:
        print("\n  Cannot start AIDE — install required packages and retry.\n")
        sys.exit(1)

_check_deps()

import pyte
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QTextEdit, QLineEdit, QDialog,
    QDialogButtonBox, QSplitter, QCheckBox, QComboBox, QFormLayout,
    QFrame, QSpinBox, QDoubleSpinBox, QListWidget, QListWidgetItem,
    QStackedWidget, QGroupBox, QSlider, QMenu, QMenuBar, QScrollBar,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, QTimer, QSize, QRect, QPointF, QPoint, QEvent, pyqtSignal, QUrl, QMimeData
from PyQt6.QtGui import (
    QFont, QFontMetrics, QPainter, QColor, QPalette, QPen,
    QKeyEvent, QResizeEvent, QPixmap, QDrag, QTextCursor, QTextCharFormat,
)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
    _HAS_WEBENGINE = True
except ImportError:
    _HAS_WEBENGINE = False

from agent_dashboard import AgentTable
from dashboard import DashboardServer, local_ip
from neural import NeuralBus, write_client

DASHBOARD_PORT = 8765
GITHUB_RAW_URL = "https://raw.githubusercontent.com/gitayg/aide/main/AIDE.py"

# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS & THEME
# ═════════════════════════════════════════════════════════════════════════════

VERSION      = "4.3.1"
APP_NAME     = "AIDE"

# ── Tab-switch ping pong sound ─────────────────────────────────────────────────
def _ping_pong_sound(tab_index: int = 0):
    """Play a short ping-pong 'tick' sound. Each tab gets a slightly different
    pitch so the user can distinguish tabs by ear."""
    import io, wave, math, array
    SAMPLE_RATE = 44100
    DURATION = 0.04          # 40 ms — short, snappy
    VOLUME = 0.15
    # Base freq ~2200 Hz, each tab shifts ±80 Hz (wraps after 8)
    freq = 2200 + (tab_index % 8) * 80
    n_samples = int(SAMPLE_RATE * DURATION)
    samples = array.array("h")
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        # Exponential decay envelope for that "tick" character
        env = math.exp(-t * 120) * VOLUME
        val = int(env * 32767 * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32767, min(32767, val)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    tmp = Path.home() / ".aide" / "tab_tick.wav"
    tmp.write_bytes(buf.getvalue())
    subprocess.Popen(["afplay", str(tmp)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)

def _smash_sound():
    """Ping-pong smash: sharp crack + noise burst, louder and punchier than a tick."""
    import io, wave, math, array, random
    SAMPLE_RATE = 44100
    DURATION    = 0.09   # 90 ms — enough for the crack to ring out
    VOLUME      = 0.70
    n = int(SAMPLE_RATE * DURATION)
    samples = array.array("h")
    rng = random.Random(3)
    for i in range(n):
        t = i / SAMPLE_RATE
        # Fast-decay tone (fundamental + 2nd harmonic)
        env  = math.exp(-t * 55) * VOLUME
        tone = (math.sin(2*math.pi*950*t)
              + 0.55*math.sin(2*math.pi*1900*t)
              + 0.20*math.sin(2*math.pi*2850*t))
        # Heavy transient noise at the very start (crack character)
        crack = (rng.random()*2-1) * math.exp(-t * 300) * 1.8
        val = int(env * 32767 * (tone + crack) / 2.5)
        samples.append(max(-32767, min(32767, val)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    tmp = Path.home() / ".aide" / "smash.wav"
    tmp.write_bytes(buf.getvalue())
    subprocess.Popen(["afplay", str(tmp)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _tennis_serve_sound():
    """Tennis serve: deep racket-string thwock for entering split mode."""
    import io, wave, math, array, random
    SAMPLE_RATE = 44100
    DURATION    = 0.13
    VOLUME      = 0.65
    n = int(SAMPLE_RATE * DURATION)
    samples = array.array("h")
    rng = random.Random(11)
    for i in range(n):
        t = i / SAMPLE_RATE
        # Lower fundamental (~520 Hz) + slight downward pitch sweep for the "thwock" feel
        freq = 520 - 120 * t
        env  = math.exp(-t * 28) * VOLUME
        tone = math.sin(2*math.pi*freq*t) + 0.45*math.sin(2*math.pi*freq*2*t)
        # Short string-buzz transient at the strike point
        buzz = (rng.random()*2-1) * math.exp(-t * 220) * 1.4
        val = int(env * 32767 * (tone + buzz) / 2.6)
        samples.append(max(-32767, min(32767, val)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    tmp = Path.home() / ".aide" / "tennis_serve.wav"
    tmp.write_bytes(buf.getvalue())
    subprocess.Popen(["afplay", str(tmp)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _tennis_point_sound():
    """Point ends: light high ping with a quick fade for exiting split mode."""
    import io, wave, math, array
    SAMPLE_RATE = 44100
    DURATION    = 0.18
    VOLUME      = 0.55
    n = int(SAMPLE_RATE * DURATION)
    samples = array.array("h")
    for i in range(n):
        t = i / SAMPLE_RATE
        # Two-tone descending chime (1500 → 1100 Hz) — "point ended" cue
        f = 1500 - 400 * (t / DURATION)
        env = math.exp(-t * 18) * VOLUME
        tone = math.sin(2*math.pi*f*t) + 0.35*math.sin(2*math.pi*f*1.5*t)
        val = int(env * 32767 * tone / 1.6)
        samples.append(max(-32767, min(32767, val)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    tmp = Path.home() / ".aide" / "tennis_point.wav"
    tmp.write_bytes(buf.getvalue())
    subprocess.Popen(["afplay", str(tmp)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _blop_sound():
    """Soft round 'blop' — played when focus auto-moves to a new waiting question."""
    import io, wave, math, array
    SAMPLE_RATE = 44100
    DURATION    = 0.18
    VOLUME      = 0.45
    n = int(SAMPLE_RATE * DURATION)
    samples = array.array("h")
    for i in range(n):
        t = i / SAMPLE_RATE
        # Pitch drops quickly (320 → 160 Hz) giving the round "blop" character
        freq = 320 * math.exp(-t * 9)
        # Fast attack (first 5 ms), then smooth exponential decay
        attack = min(t / 0.005, 1.0)
        env    = attack * math.exp(-t * 14) * VOLUME
        tone   = math.sin(2 * math.pi * freq * t)
        val    = int(env * 32767 * tone)
        samples.append(max(-32767, min(32767, val)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    tmp = Path.home() / ".aide" / "blop.wav"
    tmp.write_bytes(buf.getvalue())
    subprocess.Popen(["afplay", str(tmp)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _macos_notify(title: str, msg: str):
    """Post a macOS Notification Center alert via osascript."""
    try:
        safe_title = title.replace('"', "'")[:120]
        safe_msg   = msg.replace('"', "'")[:240]
        script = f'display notification "{safe_msg}" with title "AIDE" subtitle "{safe_title}"'
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class SplitBallOverlay(QWidget):
    """Animates a ping-pong ball flying from one split pane to the other.
    With an optional label (used for neural messages), renders text next
    to the ball and extends the duration."""
    _RADIUS = 7
    _ARC    = 35   # pixels — how high the ball arcs above the straight line

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._frame = 0
        self._total = 0
        self._start = QPointF(0, 0)
        self._end   = QPointF(0, 0)
        self._label = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def launch(self, start: QPointF, end: QPointF,
               label: str = "", duration_ms: int = 308):
        self._start = start
        self._end   = end
        self._label = label
        interval    = 25  # 40 fps
        self._total = max(1, duration_ms // interval)
        self._frame = 0
        self.setGeometry(self.parent().rect())
        self.show(); self.raise_()
        self._timer.start(interval)

    def _step(self):
        self._frame += 1
        if self._frame >= self._total:
            self._timer.stop(); self.hide(); return
        self.update()

    def paintEvent(self, ev):
        import math
        if self._frame >= self._total: return
        t  = self._frame / self._total
        x  = self._start.x() + (self._end.x() - self._start.x()) * t
        y  = (self._start.y() + (self._end.y() - self._start.y()) * t
              - self._ARC * math.sin(math.pi * t))
        # Fade in over first 10% and fade out over last 15%
        fade = 1.0
        if t < 0.1:       fade = t / 0.1
        elif t > 0.85:    fade = (1.0 - t) / 0.15
        alpha = int(255 * fade)
        r = self._RADIUS
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Shadow
        p.setBrush(QColor(0, 0, 0, int(60 * fade)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(x - r + 2), int(y - r + 2), r * 2, r * 2)
        # Ball body — accent color for neural, yellow for plain paste
        if self._label:
            p.setBrush(QColor(C_ACCENT.red(), C_ACCENT.green(), C_ACCENT.blue(), alpha))
        else:
            p.setBrush(QColor(255, 220, 50, alpha))
        p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)
        # Highlight
        p.setBrush(QColor(255, 255, 255, alpha // 2))
        p.drawEllipse(int(x - r // 2), int(y - r), r, r)
        # Label bubble
        if self._label:
            f = QFont(FONT_FAMILY); f.setPointSize(10); f.setBold(True)
            p.setFont(f)
            fm = p.fontMetrics()
            text = self._label
            pad_x, pad_y = 8, 4
            tw = fm.horizontalAdvance(text)
            th = fm.height()
            bw = tw + pad_x * 2
            bh = th + pad_y * 2
            # Place the bubble above the ball
            bx = int(x - bw / 2)
            by = int(y - r - bh - 4)
            # Keep on-screen
            bx = max(4, min(bx, self.width() - bw - 4))
            by = max(4, by)
            # Bubble background
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(17, 17, 27, int(230 * fade)))  # C_BG with alpha
            p.drawRoundedRect(bx, by, bw, bh, 6, 6)
            # Border
            p.setPen(QPen(QColor(C_ACCENT.red(), C_ACCENT.green(), C_ACCENT.blue(),
                                 int(200 * fade)), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(bx, by, bw, bh, 6, 6)
            # Text
            p.setPen(QPen(QColor(C_FG.red(), C_FG.green(), C_FG.blue(), alpha)))
            p.drawText(bx + pad_x, by + pad_y + fm.ascent(), text)
        p.end()


class NeuralRailOverlay(QWidget):
    """Transparent overlay on the TabBar widget.
    Draws a vertical rail connecting bus agents down to the Neural Brain card."""
    _X  = 5   # x-center of the rail within the TabBar
    _DR = 3   # station-dot radius
    _PR = 4   # packet radius

    def __init__(self, tabbar: QWidget):
        super().__init__(tabbar)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self._cards: list = []
        self._brain_card = None   # set by TabBar after construction
        self._anim_from_y = 0
        self._anim_to_y   = 0
        self._anim_prog   = -1.0   # -1 = idle
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._tick)
        self.resize(tabbar.size())
        self.raise_()

    def set_cards(self, cards: list):
        self._cards = cards
        self.update()

    def start_animation(self, from_y: int, to_y: int):
        self._anim_from_y = from_y
        self._anim_to_y   = to_y
        self._anim_prog   = 0.0
        if not self._timer.isActive():
            self._timer.start(25)

    def _tick(self):
        if self._anim_prog < 0:
            self._timer.stop(); return
        self._anim_prog = min(1.0, self._anim_prog + 0.033)  # ~30 steps ≈ 750ms
        if self._anim_prog >= 1.0:
            self._anim_prog = -1.0
            self._timer.stop()
        self.update()

    def paintEvent(self, ev):
        tb = self.parent()
        bus_cards = [c for c in self._cards if c.session.neural_on_bus and c.isVisible()]

        # Brain card y-position in TabBar coordinates
        brain_y = None
        if self._brain_card and self._brain_card.isVisible():
            brain_y = self._brain_card.mapTo(tb, QPoint(0, self._brain_card.height() // 2)).y()

        if not bus_cards and self._anim_prog < 0:
            return

        ys = []
        for c in bus_cards:
            cy = c.mapTo(tb, QPoint(0, c.height() // 2)).y()
            if 0 <= cy < tb.height():
                ys.append(cy)
        ys.sort()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = self._X
        rail_col = C_ACCENT

        if ys:
            spine_top = max(0, ys[0] - 10) if len(ys) == 1 else ys[0]
            # Extend spine all the way down to the brain card
            spine_bot = brain_y if brain_y is not None else (ys[-1] + 10)
            p.setPen(QPen(rail_col, 2))
            p.drawLine(x, spine_top, x, spine_bot)

        # Horizontal tap lines from rail into the card's icon area
        tap_col = QColor(rail_col); tap_col.setAlpha(160)
        for y in ys:
            p.setPen(QPen(tap_col, 1))
            p.drawLine(x, y, x + 14, y)

        # Station dots on top of tap lines
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rail_col)
        for y in ys:
            r = self._DR
            p.drawEllipse(x - r, y - r, r * 2, r * 2)

        # Brain card terminus dot
        if ys and brain_y is not None:
            p.setBrush(rail_col)
            r = self._DR + 1
            p.drawEllipse(x - r, brain_y - r, r * 2, r * 2)

        if self._anim_prog >= 0:
            t  = self._anim_prog
            t2 = t * t * (3 - 2 * t)  # smoothstep ease
            py = int(self._anim_from_y + t2 * (self._anim_to_y - self._anim_from_y))
            p.setBrush(QColor(255, 255, 255, 230))
            r = self._PR
            p.drawEllipse(x - r, py - r, r * 2, r * 2)

        p.end()


# ── What's New ──────────────────────────────────────────────────────────────
# Each entry: (emoji, short title, one-line description)
# Add new bullets at the TOP of this list each time you ship a change.
# Release notes keyed by version string (semver, newest first).
# Only entries for versions newer than the user's previous install are shown.
WHATS_NEW: Dict[str, list] = {
    "4.3.1": [
        ("🏷️", "Tag filter — single-select by default", "Clicking a tag filter chip now selects only that tag; hold Shift to add/remove additional tags for multi-select."),
    ],
    "4.3.0": [
        ("◀", "Back-to-dashboard button in terminal view", "A '◀ Dashboard' button is now always visible at the top of the terminal view — click it to return to the agent table without using the hotkey bar."),
        ("⛔", "Close guard when agents are working", "If any agent is actively working, AIDE prompts before closing so you can't accidentally kill a running task."),
        ("🏷️", "Session ID tooltip on Command column", "Hover over the Command cell in the agent table to see the full Claude session ID extracted from the resume command."),
        ("🔴", "No auto-launch on AIDE startup", "Agents are no longer auto-started when AIDE restarts — they are dispatched on-demand via -p when a task is sent."),
        ("🔢", "Dock badge shows pending items", "macOS dock icon badge shows the count of agents waiting for approval + agents pending validation review."),
    ],
    "4.2.0": [
        ("🤖", "Per-agent model selector", "Choose Opus 4.7, Sonnet 4.6, or Haiku 4.5 for each agent from the notes panel. The --model flag is injected into every task dispatch and autostart command."),
        ("🔢", "Cumulative token counter", "The agent table shows running total of input+output tokens consumed by each agent, updated live from terminal output."),
    ],
    "4.0.0": [
        ("⊞", "Agent dashboard — new primary view", "Replaces the terminal as the main view. All agents shown in a dense sortable table grouped by status (Pending Answer, Working, Idle). Search by name, filter by tag, double-click to open terminal."),
        ("⚡", "Agents auto-launch on tab creation", "AIDE now automatically executes each tab's autostart command when the tab is created or AIDE restarts — no more manual start. The NewTerminalDialog is removed."),
        ("🛡️", "claude wrapper auto-injects --permission-prompt-tool", "A claude wrapper in _neural_bin/ silently adds --permission-prompt-tool $AIDE_PERMISSION_TOOL to every claude invocation so MCP permissions work without manual flags."),
        ("123", "Permission dialog keyboard shortcuts", "1 = Allow  ·  2 = Always allow  ·  3 = Deny — faster approvals without reaching for the mouse."),
        ("🟡", "Pending validation state per agent", "Right-click any agent → Set Pending Validation to attach a note and mark the card red. Use it to flag output that needs human review."),
        ("💬", "Chat bar per agent", "Right-click → Chat opens an inline input bar that injects messages directly into that agent's terminal PTY."),
        ("↪", "Redirect output to another agent", "Right-click → Redirect output to… forwards a message to any other agent via the Neural Bus."),
    ],
    "3.0.0": [
        ("🛡️", "MCP permission-prompt server", "AIDE now serves an MCP SSE endpoint on the Neural Bus. Claude Code sessions launched with --permission-prompt-tool mcp__aide__permission_prompt route every tool-permission request to a native AIDE dialog — you see the tool name and arguments and click Allow or Deny. AIDE auto-writes the MCP server entry to ~/.claude/settings.json on startup."),
        ("↩", "Double-click terminal sends Enter", "Double-clicking anywhere in a terminal area sends an Enter keystroke — useful for quickly confirming Claude Code prompts without reaching for the keyboard."),
        ("💾", "Auto-backup on upgrade", "When AIDE detects a version change it writes versioned snapshots of session.json and neural_brain.md to ~/.aide/ (e.g. session.backup-2-23-0.json) before applying the update — so all terminal names, tags, autostart params, tasks, and notes are preserved."),
    ],
    "2.23.0": [
        ("🚀", "Agent startup prompt on new terminal", "Opening a new terminal shows a dialog with a ready-to-paste agent onboarding prompt: session ID, Neural Bus URL, shared brain file, workspace directory, Claude account, and neural bus operating instructions. One click copies it all."),
    ],
    "2.22.2": [
        ("⊞", "Click a split terminal to swap panes", "If a terminal is already open in a split pane, clicking it in the sidebar swaps that pane's session with the focused pane — so you can bring any visible terminal to your current focus without rearranging panes manually."),
    ],
    "2.22.1": [
        ("🐙", "GitHub token scoped to focused terminal only", "Vault unlock now exports the GitHub token only to the focused terminal. Other terminals receive their vault variables but not the token; they pick it up when explicitly selected from the per-terminal combo."),
    ],
    "2.22.0": [
        ("🧠", "Neural Brain — shared memory for all agents", "A pinned '🧠 Neural Brain' entry appears at the bottom of the terminal list. Click it to open an editor for a shared Markdown file (~/.aide/neural_brain.md) that all agents can read. Available in every AIDE terminal as $AIDE_NEURAL_BRAIN_FILE and via 'neural brain'."),
    ],
    "2.21.0": [
        ("⏐", "Separators for sidebar organization", "Click '⏐ Separator' at the bottom of the terminal list to add a labeled section divider. Double-click to rename; drag-and-drop to reorder separators and terminals together. Tag grouping/deduplication removed in favor of explicit separators."),
        ("🛤️", "Neural rail replaces side panel", "Bus agents shown on a live rail on the left of the card list. Messages animate as packets along the rail. Double-click any terminal to open Terminal Settings (rename + neural bus config in one unified dialog)."),
    ],
    "2.20.0": [
        ("🛤️", "Neural rail replaces side panel", "Bus agents are shown on a live vertical rail drawn on the left of each terminal card. Messages travel as animated packets along the rail. Double-clicking any terminal name now opens Terminal Settings — rename and neural bus config in one place. Separate Neural panel removed."),
    ],
    "2.19.0": [
        ("🔐", "Per-tab Claude account / login", "Sidebar now has a 🤖 Claude account section. Set a profile name (e.g. 'work', 'personal') — each profile gets its own CLAUDE_CONFIG_DIR at ~/.aide/claude-profiles/<name>/. 'claude /login' button runs interactive OAuth in that terminal using the chosen profile. Also a free-text Extra args field (e.g. --model sonnet) stored per tab."),
    ],
    "2.18.2": [
        ("⚡", "Further CPU reductions", "Terminal tick 50ms→100ms (halves paint-poll wakeups). mark_visible / mark_kbd_focus guarded against no-op calls so setStyleSheet doesn't run every 500ms per card. _update_waiting_badge guarded against unchanged counts so setWindowTitle/setBadgeNumber only run when the count actually changes."),
    ],
    "2.18.1": [
        ("💬", "Neural animation shows the message blurb", "The ball flying between panes now carries a speech-bubble with the first line of the message (up to 60 chars). Animation lasts 1.6s with fade-in/out so you can read what's being sent."),
    ],
    "2.18.0": [
        ("🚀", "Neural messages delivered immediately", "No more AIDE approval queue. Messages sent with `neural send` are delivered instantly and appear in the target terminal as '# 🤖 neural from [sender]: …'. Any human approval is handled by Claude Code's own tool-permission prompts in-context."),
        ("🏓", "Neural split-pane animation", "When the sender and receiver are both visible in split panes, the ping-pong ball animation flies between them on every neural message."),
        ("📜", "Neural panel shows message history", "The 'Pending approval' queue is replaced with 'Recent messages' — a live list of the last 20 neural messages with sender → target and content."),
    ],
    "2.17.6": [
        ("🤖", "Neural bus state persists across restarts", "Agent registrations (name, tag, app, role, task) are saved with the session and automatically restored when AIDE restarts — no need to rejoin the bus manually."),
    ],
    "2.17.5": [
        ("🔔", "Detect Claude tool-permission prompts", "Claude Code's numbered choice prompts ('Do you want to proceed? 1. Yes…  Esc to cancel') now trigger the waiting notification — they have no ╰─ border so were previously missed entirely."),
    ],
    "2.17.4": [
        ("📋", "Neural agent prompt", "Neural panel now has a 'Copy agent prompt' button that copies a full Claude operating-instructions prompt to clipboard — paste it at the start of any agent session to onboard it to the Neural Bus."),
    ],
    "2.17.3": [
        ("🤖", "Neural registration with full agent profile", "Join Neural Bus dialog now collects tag, app, role, and current task. Agent cards in the Neural panel show all fields. Borg-ship 🤖 icon used throughout."),
    ],
    "2.17.2": [
        ("🧠", "Right-click to join/leave Neural Bus", "Right-clicking a terminal card in the sidebar shows 'Join Neural Bus' or 'Leave Neural Bus'. Joining prompts for agent name and task; the ⬡ icon appears on the card while connected. Neural panel opens automatically on join."),
    ],
    "2.17.1": [
        ("📖", "Neural help button", "Added 📖 Neural? button to ribbon — shows all neural commands and the bus URL at a glance."),
    ],
    "2.17.0": [
        ("🧠", "Neural message bus", "Agents (Claude Code sessions) can register with the Neural bus, announce their current task, and send messages to each other. All inter-agent communication requires human approval. Toggle panel via the 🧠 Neural button or ^B-n. Use the `neural` command in any terminal: neural register, neural agents, neural send, neural inbox."),
    ],
    "2.16.4": [
        ("🔔", "Fix false waiting notifications from shell prompts", "╭─/╰─ box detection now requires ≥3 box-drawing chars. Powerline/Starship shell prompts that draw '╭─ user@host' and '╰─ $' no longer trigger false waiting alerts — Claude Code's real box borders are always full-width lines of dashes."),
    ],
    "2.16.3": [
        ("🔔", "Reliable waiting detection", "╭─ box-open now tracked as a signal that Claude is active — ╰─ box-close triggers the waiting notification even when no braille spinner was detected (e.g. different Claude Code builds). Spinner detection still used when available."),
    ],
    "2.16.2": [
        ("⚡", "Further CPU reduction", "Card refresh state guard now correctly skips idle sessions: gear_tick/blink_phase only update and count toward state when the session is actually animating; idle cards produce a stable state tuple and skip Qt refresh entirely. PTY select timeout 50ms→100ms; event poll 50ms→100ms."),
    ],
    "2.16.1": [
        ("⚡", "CPU usage reduction", "Terminal tick 33ms→50ms; hidden terminals skip repaint; split-pane header setText/setStyleSheet guarded against no-op calls; status bar setText guarded; all cut idle CPU significantly"),
    ],
    "2.16.0": [
        ("⚡", "Performance improvements", "Removed per-chunk os.path.exists() debug syscall; scrollback buffer reduced from 10,000 to 2,000 rows (80% less memory per session); card refreshes now skipped when nothing changed, cutting Qt repaints by ~90% in idle sessions"),
    ],
    "2.15.9": [
        ("🔍", "Fix waiting detection for fast responses", "When Claude's spinner and the ╰─ box-close arrive in the same PTY chunk, the waiting transition was incorrectly skipped. Fixed by starting the 300 ms debounce timer whenever ╰─ fires after any activity. Added a 3 s idle fallback: if no spinner arrives for 3 s while Claude was working, automatically transition to waiting"),
    ],
    "2.15.8": [
        ("🔍", "Fixed working + waiting detection", "Working: any braille spinner char now triggers the working state (previous regex required following text that cursor-movement codes prevented matching). Waiting: detected via the ╰─ response-box closing border with a 300 ms debounce so intermediate tool-result boxes don't fire false alerts — 'Human:' pattern removed since Claude Code CLI never outputs it"),
    ],
    "2.15.7": [
        ("⚙", "Simplified card icons", "Sidebar cards now show only two icons: a spinning ◐⚙ gear while Claude is working/thinking, and a blinking ? while waiting for your input — all other icons removed"),
    ],
    "2.15.6": [
        ("🏷", "Tag deduplication toggle in Cards settings", "Open Cards (^B-c) and toggle 'Hide repeated tag names' — when on, consecutive cards sharing a tag show the tag only on the first card; when off, every card shows its tags"),
    ],
    "2.15.5": [
        ("▌", "Cursor block always visible", "The text cursor now renders as a solid blue block even when positioned on an empty cell, so you can always see where you are while typing or navigating"),
    ],
    "2.15.4": [
        ("📍", "Cursor position in status bar", "The bottom status bar now shows the terminal cursor row:col (e.g. 5:32) so you can see exactly where the text cursor is while editing"),
    ],
    "2.15.3": [
        ("🔍", "Fixed waiting detection", "ANSI escape codes are now stripped before pattern matching, so 'Human:' and confirmation prompts are reliably detected even when colour codes are interleaved"),
    ],
    "2.15.2": [
        ("🔔", "Blop sound on auto-focus", "A soft 'blop' plays whenever focus automatically moves to a terminal with a new question from Claude"),
    ],
    "2.15.1": [
        ("🚀", "Auto-advance settles on working terminal", "After replying to all waiting terminals, focus automatically moves to the next terminal where Claude is actively working — so you're already there when it finishes"),
    ],
    "2.15.0": [
        ("🚀", "Uber mode", "Click 🚀 Uber in the toolbar to enable auto-focus: whenever Claude asks you a question in any terminal, AIDE immediately jumps to that pane or tab — no manual switching needed"),
    ],
    "2.14.4": [
        ("📋", "Tab-paste picker for 3+ panes", "With 2 panes Tab still auto-sends to the other; with 3 or more a popup menu appears so you can click which pane receives the command"),
    ],
    "2.14.3": [
        ("⌨", "Ctrl+↑/↓ cycles panes when in split mode", "While any split pane is focused, Ctrl+Up/Down moves focus between panes only — sidebar navigation resumes when back to a single terminal"),
    ],
    "2.14.2": [
        ("⊞", "6-panel grid is now 3×2", "Split panels fill left-to-right then top-to-bottom: panes 1–3 across the top row, panes 4–6 across the bottom row"),
    ],
    "2.14.1": [
        ("🤖", "Improved agent working detection", "Spinner and done signals are now evaluated independently — a chunk containing both a braille spinner and the ╰─ response border correctly ends the working state instead of getting stuck; ╭─ opening border transitions thinking→working immediately; spinner regex is tighter to skip stray ANSI sequences"),
    ],
    "2.14.0": [
        ("↔", "Resizable sidebar", "Drag the handle between the sidebar and the terminal area to resize the left panel to your preferred width"),
        ("⊞", "Up to 6 split panels", "Shift+click up to 6 times to open a 3-row 2×3 grid; Cmd+1–6 focus each pane; each extra pane header has an × close button"),
        ("🏷", "Sidebar sort by tag or recent question", "Click A-Z to sort terminals alphabetically by tag; click ⏱ to sort by when Claude last asked you a question — click again to restore insertion order"),
        ("🧹", "Groups removed from sidebar", "Terminals are no longer grouped under collapsible headers — tags still filter the list via the pill buttons in the filter bar"),
    ],
    "2.13.6": [
        ("🧹", "Cleaner info bar in single-pane mode", "Terminal name is no longer shown in the top info bar when only one pane is open — the sidebar already identifies it"),
    ],
    "2.13.5": [
        ("🎨", "Paste image as ASCII art", "Right-click in any terminal → 'Paste image as ASCII art' converts a clipboard screenshot to ASCII characters and pastes the result as text"),
    ],
    "2.13.4": [
        ("💬", "Waiting indicator visible in sidebar", "Cards where Claude is waiting now show a blue accent bar + blue-tinted background — same visual as an active card but distinct, so waiting terminals stand out in the list even when not focused"),
    ],
    "2.13.3": [
        ("💬", "Bold waiting title finally works", "Tab card title is now bolded via inline HTML (<b>) rather than relying on Qt stylesheet font-weight, which Qt's HTML renderer silently ignores — works with or without tags"),
    ],
    "2.13.2": [
        ("⌨", "Ctrl+↑/↓ follows sidebar visual order", "Next/prev-tab navigation (Ctrl+↑/↓ and Ctrl+Tab) now steps through terminals in the exact order displayed in the sidebar, respecting drag-and-drop reordering and group sorting"),
    ],
    "2.13.1": [
        ("⌨", "Ctrl+↑/↓ navigates the sidebar", "Press Ctrl+Up / Ctrl+Down in any terminal pane to jump to the previous/next terminal in the left sidebar"),
    ],
    "2.13.0": [
        ("⊞", "Up to 4 split panels", "Shift+click a tab card to add it as a new split pane — repeat up to 4 (2×2 grid); Cmd+1–4 focus each pane; the right sidebar always shows the focused pane's notes/vars/token"),
    ],
    "2.12.5": [
        ("🔗", "Ctrl+click opens URLs", "Hold Ctrl (⌘ on macOS) and click any URL in the terminal to open it in the browser; cursor changes to a pointing hand when hovering a link with Ctrl held"),
    ],
    "2.12.4": [
        ("𝐁", "Bold terminal title actually works", "Put font-weight in the QLabel stylesheet — Qt CSS beats setFont() + rich-text mode swallows <b>, so only the stylesheet reliably bolds the card title when Claude is waiting"),
    ],
    "2.12.3": [
        ("💾", "Persistent GitHub token selection", "Token choice now saves to disk immediately on change; locked vault no longer clobbers saved selections during tab switch"),
    ],
    "2.12.2": [
        ("🐙", "GitHub token exported before autostart", "Token is silently exported into the shell before the autostart command runs, so claude and other autostart tools see GITHUB_TOKEN set; changing the token in the panel re-exports into the live shell"),
    ],
    "2.12.1": [
        ("💬", "Status in split headers & cards", "Split pane header now shows spinner while Claude works and 💬 while waiting, with a highlighted style when waiting; tab card icon slot also shows 💬 for waiting"),
    ],
    "2.12.0": [
        ("🐙", "Per-terminal GitHub token", "GitHub token selector moved to the right-side Autostart section; each tab picks its own token, injected as GITHUB_TOKEN and GH_TOKEN before the autostart command runs"),
    ],
    "2.11.0": [
        ("🐙", "GitHub token manager", "Ribbon 🐙 → manage named GitHub PATs; sidebar combo selects which is active; injected as GITHUB_TOKEN and GH_TOKEN in terminals"),
    ],
    "2.10.1": [
        ("⌘", "Cmd+1 / Cmd+2 pane focus", "Split headers show ⌘1 and ⌘2 shortcuts; pressing them focuses that pane"),
    ],
    "2.10.0": [
        ("⚖", "AGPL-3.0 license", "AIDE is now licensed under the GNU Affero GPL v3.0 or later — see LICENSE in the repo root"),
    ],
    "2.9.10": [
        ("📎", "Paste files as paths", "Right-click → Paste files as paths inserts shell-quoted paths from any Finder file copy; Cmd+V now prefers file paths over the filename text"),
    ],
    "2.9.9": [
        ("🔔", "macOS notification when Claude asks", "System Notification Center alert when Claude is waiting for input and AIDE isn't focused"),
        ("🎾", "Tennis sounds for split", "Deep racket-thwock when entering split mode; descending chime when exiting"),
        ("⇧", "Shift+click to split/swap", "Shift+click any tab to split current with it; if already split, swaps the secondary pane to that tab"),
    ],
    "2.9.8": [
        ("⌨", "Tab only smashes with selection", "In split mode, Tab without a text selection passes through as normal shell autocomplete; smash only fires when text is selected"),
    ],
    "2.9.7": [
        ("🌳", "Tree view sidebar", "Tabs are now grouped by their first tag in collapsible sections; click a group header to collapse/expand"),
    ],
    "2.9.6": [
        ("⊟", "Focused pane header", "Active split pane header glows blue; clicking any tab card replaces that pane's session"),
    ],
    "2.9.5": [
        ("🏓", "Tab smash sound", "Pressing Tab in split mode plays a ping-pong smash — with selection sends text to the other pane, without selection swaps focus"),
    ],
    "2.9.4": [
        ("↻", "GitHub update check", "Update detection now fetches version from GitHub directly; clicking Update downloads and applies the latest AIDE.py automatically"),
        ("A±", "Font buttons moved", "A- and A+ buttons are now next to the main ribbon on the left"),
    ],
    "2.9.3": [
        ("⣾", "Spinner icon", "Animated braille spinner replaces the static icon when Claude is working or thinking"),
    ],
    "2.9.0": [
        ("📱", "Mobile dashboard", "Open http://<your-mac-ip>:8765 on your phone — live session cards, status dots, last output, and quick-reply for waiting agents. URL shown in the sidebar footer."),
        ("🏷", "Tag accent color", "Tags on tab cards are now shown in accent blue"),
        ("↕",  "Drag-and-drop reorder", "Drag tab cards to reorder them in the sidebar"),
        ("✦",  "Bold when waiting", "Terminal title is bold when Claude is waiting for input"),
        ("▲",  "No-command indicator", "Orange triangle on cards with no startup command configured"),
    ],
    "2.8.0": [
        ("🏷", "Tags replace groups", "Right-click tab → Edit Tags…; tags shown as [tag] before title; click tag pill in sidebar to filter"),
    ],
    "2.7.0": [
        ("📁", "Tab groups",  "Right-click any tab → Move to Group; click group header to collapse/expand"),
    ],
    "2.6.0": [
        ("⊟",  "Shift+click to split",       "Shift+click any tab card to instantly split the view with that terminal"),
        ("📨", "Sender label on Tab-paste",   "Pasted text is prefixed with '# incoming from [tab name]' in the target pane"),
    ],
    "2.5.0": [
        ("🏓", "Split-paste ball animation", "Tab-paste in split view animates a yellow ball flying between panes"),
        ("🎾", "Racket-hit sound",           "Split-pane Tab-paste plays a sharp thwack distinct from tab-switch ticks"),
    ],
    "2.4.0": [
        ("🔴", "Mark as Unread",   "Right-click any tab → Mark as Unread; orange dot + border until you return"),
        ("🔢", "Task count badge", "Blue pill on tab card shows number of tasks in that tab's notes panel"),
    ],
    "2.3.0": [
        ("🔒", "Config directory hardened",    "~/.aide/ permissions set to 0o700 — no other users can read your config"),
        ("📋", "Clipboard file restricted",    "clipboard.json permissions set to 0o600 after each write"),
        ("🧹", "Temp image cleanup",           "Pasted image temp files are deleted when the app closes"),
    ],
    "2.2.0": [
        ("🎵", "Tab switch sounds",             "Each tab plays a unique ping-pong tick sound when selected"),
        ("🔄", "AIDE menu: Check for Updates",  "AIDE → Check for Updates manually triggers a git fetch + compare"),
        ("🖼", "Custom AIDE icon",              "New dark terminal icon replaces the Python rocket in Dock and Finder"),
        ("🔁", "Auto git pull on restart",      "↻ Update button now pulls latest code before restarting"),
        ("🔒", "Git-only update detection",     "Update checks now use git remote only — no more local file watching"),
        ("🏷", "Full rebrand to AIDE",          "Config moved to ~/.aide/; legacy paths cleaned up"),
    ],
    "2.1.2": [
        ("🔑", "API Keys in ribbon",          "One-click 🔑 API Keys button added to the toolbar"),
        ("📝", "SideBar button renamed",       "Notes button in ribbon renamed to SideBar"),
        ("🎨", "Cleaner tab card highlights",  "Single left-border encodes all state; no more 4-edge border noise"),
        ("🐛", "Scrollback repetition fixed",  "Topmost history line no longer repeats when scrolling up"),
    ],
    "2.1.1": [
        ("🔐", "Security hardening",           "13 injection / path-traversal / vault issues patched"),
        ("⊟",  "Split-view tip popup",         "One-time guide explaining Tab-to-paste when you first split"),
        ("🤖", "Better bot detection",         "Braille-spinner detection eliminates false positives from npm/git/pip"),
        ("🔔", "Notifications reworked",       "Sound + banner fires correctly even on background tabs"),
    ],
    "2.1.0": [
        ("🗂", "Tab-paste in split view",      "Select text, press Tab → pasted into the other split pane"),
        ("🖼", "Image paste",                  "Right-click → Paste image as file path (clipboard screenshots → temp PNG)"),
        ("📁", "Drag-and-drop files",          "Drag any file from Finder onto the terminal to insert its quoted path"),
        ("🤖", "Bot detection in tab card",    "🤖 icon + Working/Thinking status row in the sidebar card while Claude runs"),
        ("🖱", "Click no longer interrupts",   "Fixed: clicking the terminal no longer sends spurious arrow-key sequences"),
        ("A±", "Font size ± buttons",          "Replaced the slider with compact A− / A+ buttons in the ribbon"),
        ("🔐", "SSH host detection",           "Improved: parses more terminal-title formats and OSC 7 remote hostnames"),
    ],
}
CONFIG_DIR        = Path.home() / ".aide"
SESSION_FILE      = CONFIG_DIR / "session.json"
NEURAL_BRAIN_FILE = CONFIG_DIR / "neural_brain.md"
CONFIG_FILE  = CONFIG_DIR / "config.json"
CLIP_FILE       = CONFIG_DIR / "clipboard.json"
VAULT_FILE      = CONFIG_DIR / "vault.enc"
SCREENSHOTS_DIR = CONFIG_DIR / "screenshots"
CONFIG_DIR.mkdir(exist_ok=True)
try:
    import os as _os
    _os.chmod(CONFIG_DIR, 0o700)
except OSError:
    pass

DEFAULT_SHELL = os.environ.get("COMSPEC", "cmd.exe") if IS_WINDOWS else \
                os.environ.get("SHELL", "/bin/bash")

FONT_FAMILY = "Menlo" if IS_MAC else ("Consolas" if IS_WINDOWS else "Monospace")
FONT_SIZE   = 13

C_BG      = QColor("#0d1117")
C_FG      = QColor("#e6edf3")
C_CURSOR  = QColor("#58a6ff")
C_ACCENT  = QColor("#58a6ff")
C_WARN    = QColor("#d29922")
C_PANEL   = QColor("#161b22")
C_SURFACE = QColor("#21262d")
C_MUTED   = QColor("#7d8590")

# ── 256-color palette ─────────────────────────────────────────────────────────
_256: Dict[int, QColor] = {}
_ANSI16 = [
    (0,0,0),(170,0,0),(0,170,0),(170,85,0),(0,0,170),(170,0,170),
    (0,170,170),(170,170,170),(85,85,85),(255,85,85),(85,255,85),
    (255,255,85),(85,85,255),(255,85,255),(85,255,255),(255,255,255),
]
for _i,(_r,_g,_b) in enumerate(_ANSI16): _256[_i]=QColor(_r,_g,_b)
for _n in range(16,232):
    _idx=_n-16; _bb=_idx%6; _gg=(_idx//6)%6; _rr=_idx//36
    _256[_n]=QColor(_rr*51,_gg*51,_bb*51)
for _n in range(232,256):
    _v=(_n-232)*10+8; _256[_n]=QColor(_v,_v,_v)

_NAMED: Dict[str,QColor] = {
    "black":QColor(0,0,0),"red":QColor(170,0,0),"green":QColor(0,170,0),
    "brown":QColor(170,85,0),"blue":QColor(0,0,170),"magenta":QColor(170,0,170),
    "cyan":QColor(0,170,170),"white":QColor(170,170,170),
    "brightblack":QColor(85,85,85),"brightred":QColor(255,85,85),
    "brightgreen":QColor(85,255,85),"brightbrown":QColor(255,255,85),
    "brightblue":QColor(85,85,255),"brightmagenta":QColor(255,85,255),
    "brightcyan":QColor(85,255,255),"brightwhite":QColor(255,255,255),
}

def pyte_color(c, is_bg:bool, reverse:bool=False) -> QColor:
    if reverse: is_bg = not is_bg
    if c == "default": return C_BG if is_bg else C_FG
    if isinstance(c, int): return _256.get(c, C_FG)
    if isinstance(c, (list,tuple)) and len(c)==3: return QColor(c[0],c[1],c[2])
    if hasattr(c,"red"): return QColor(c.red,c.green,c.blue)
    return _NAMED.get(str(c), C_BG if is_bg else C_FG)

# ── Qt key → PTY bytes ────────────────────────────────────────────────────────
_SPECIALS: dict = {}

def _build_keymap():
    K = Qt.Key
    global _SPECIALS
    _SPECIALS = {
        K.Key_Return:b"\r", K.Key_Enter:b"\r", K.Key_Tab:b"\t",
        K.Key_Backspace:b"\x7f", K.Key_Escape:b"\x1b",
        K.Key_Up:b"\x1b[A", K.Key_Down:b"\x1b[B",
        K.Key_Right:b"\x1b[C", K.Key_Left:b"\x1b[D",
        K.Key_Home:b"\x1b[H", K.Key_End:b"\x1b[F",
        K.Key_Delete:b"\x1b[3~", K.Key_PageUp:b"\x1b[5~",
        K.Key_PageDown:b"\x1b[6~",
        K.Key_F1:b"\x1bOP",  K.Key_F2:b"\x1bOQ",
        K.Key_F3:b"\x1bOR",  K.Key_F4:b"\x1bOS",
        K.Key_F5:b"\x1b[15~", K.Key_F6:b"\x1b[17~",
        K.Key_F7:b"\x1b[18~", K.Key_F8:b"\x1b[19~",
        K.Key_F9:b"\x1b[20~", K.Key_F10:b"\x1b[21~",
        K.Key_F11:b"\x1b[23~",K.Key_F12:b"\x1b[24~",
    }

def qt_key_to_bytes(event:QKeyEvent) -> bytes:
    key  = event.key()
    mods = event.modifiers()
    text = event.text()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    alt  = bool(mods & Qt.KeyboardModifier.AltModifier)
    K    = Qt.Key
    if ctrl:
        if K.Key_A <= key <= K.Key_Z:
            return bytes([key - K.Key_A + 1])
        if key == K.Key_At:           return b"\x00"
        if key == K.Key_BracketLeft:  return b"\x1b"
        if key == K.Key_Backslash:    return b"\x1c"
        if key == K.Key_BracketRight: return b"\x1d"
    if key in _SPECIALS:
        b = _SPECIALS[key]
        return b"\x1b" + b if alt else b
    if text:
        enc = text.encode("utf-8")
        return b"\x1b" + enc if alt else enc
    return b""

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class NotifConfig:
    enabled:          bool      = True
    style:            str       = "banner"
    sound:            bool      = True
    sound_command:    str       = ""
    sound_device:     str       = ""
    sound_volume:     float     = 1.0    # 0.0–2.0 (1.0 = system default)
    sound_duration:   float     = 1.5    # seconds — sound loops to fill this
    auto_dismiss_sec: int       = 6
    idle_trigger_sec: float     = 2.5
    patterns:         List[str] = field(default_factory=lambda: [
        r"Human:\s*$", r"\[y/n\]", r"\[Y/n\]", r"\[yes/no\]",
        r"Press any key", r">>>\s*$", r"waiting for input",
    ])
    def to_dict(self):  return asdict(self)
    @classmethod
    def from_dict(cls, d):
        return cls(**{k:v for k,v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class CardConfig:
    fields:     List[str] = field(default_factory=lambda: ["title","cwd","cmd"])
    show_tags:  bool      = True
    dedup_tags: bool      = True
    def to_dict(self):  return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(fields=d.get("fields",["title","cwd","cmd"]),
                                      show_tags=d.get("show_tags",True),
                                      dedup_tags=d.get("dedup_tags",True))

@dataclass
class AppConfig:
    notif:          NotifConfig      = field(default_factory=NotifConfig)
    card:           CardConfig       = field(default_factory=CardConfig)
    shell:          str              = ""
    auto_restart:   bool             = False
    env_overrides:  Dict[str,str]    = field(default_factory=dict)
    last_seen_mtime:float            = 0.0   # mtime of AIDE.py at last run
    last_seen_version:str            = ""    # version string at last run, e.g. "2.1.1"
    split_tip_shown:bool             = False  # one-time split-view tip shown
    uber_mode:      bool             = False  # auto-focus terminal when Claude asks a question
    def to_dict(self):
        return {"notif":self.notif.to_dict(),"card":self.card.to_dict(),
                "shell":self.shell,"auto_restart":self.auto_restart,
                "env_overrides":self.env_overrides,
                "last_seen_mtime":self.last_seen_mtime,
                "last_seen_version":self.last_seen_version,
                "split_tip_shown":self.split_tip_shown,
                "uber_mode":self.uber_mode}
    @classmethod
    def from_dict(cls, d):
        return cls(notif=NotifConfig.from_dict(d.get("notif",{})),
                   card=CardConfig.from_dict(d.get("card",{})),
                   shell=d.get("shell",""),auto_restart=d.get("auto_restart",False),
                   env_overrides=d.get("env_overrides",{}),
                   last_seen_mtime=float(d.get("last_seen_mtime",0.0)),
                   last_seen_version=d.get("last_seen_version",""),
                   split_tip_shown=bool(d.get("split_tip_shown",False)),
                   uber_mode=bool(d.get("uber_mode",False)))
    def save(self):
        try: CONFIG_FILE.write_text(json.dumps(self.to_dict(),indent=2))
        except: pass
    @classmethod
    def load(cls):
        try: return cls.from_dict(json.loads(CONFIG_FILE.read_text()))
        except: return cls()

# ═════════════════════════════════════════════════════════════════════════════
# AI PROVIDER DETECTION
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class AIProvider:
    name:str; model:str; account:str; color:str

def detect_ai_providers() -> List[AIProvider]:
    env=os.environ; out=[]
    def mask(k): return f"...{k[-4:]}" if len(k)>=4 else "****"
    for kv,name,mv,default,color in [
        ("ANTHROPIC_API_KEY","Claude","ANTHROPIC_MODEL","claude-sonnet-4-6","#3fb950"),
        ("OPENAI_API_KEY","OpenAI","OPENAI_MODEL","gpt-4o","#00b4d8"),
        ("COHERE_API_KEY","Cohere","COHERE_MODEL","command-r","#c77dff"),
        ("MISTRAL_API_KEY","Mistral","MISTRAL_MODEL","mistral-large","#4895ef"),
    ]:
        if k:=env.get(kv,""):
            out.append(AIProvider(name,env.get(mv,default),mask(k),color))
    gem=env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY") or ""
    if gem: out.append(AIProvider("Gemini",env.get("GEMINI_MODEL","gemini-1.5-pro"),mask(gem),"#ffd60a"))
    if env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY"):
        out.append(AIProvider("Bedrock",env.get("AWS_BEDROCK_MODEL_ID","claude-3-sonnet"),
                              env.get("AWS_PROFILE") or mask(env.get("AWS_ACCESS_KEY_ID","??")),"#e63946"))
    return out

# ═════════════════════════════════════════════════════════════════════════════
# SOUND
# ═════════════════════════════════════════════════════════════════════════════

def _auto_sound_cmd():
    if IS_MAC: return "afplay /System/Library/Sounds/Glass.aiff"
    if IS_LINUX:
        for prog,cmd in [("paplay","paplay /usr/share/sounds/freedesktop/stereo/bell.oga"),
                         ("aplay","aplay -q /usr/share/sounds/alsa/Front_Center.wav")]:
            if subprocess.run(["which",prog],capture_output=True).returncode==0: return cmd
    return ""

def _list_sound_devices() -> List[str]:
    """Enumerate available audio output device names."""
    if IS_MAC:
        try:
            r = subprocess.run(["system_profiler","SPAudioDataType","-json"],
                               capture_output=True, text=True, timeout=8)
            data = json.loads(r.stdout)
            devices: List[str] = []
            for section in data.get("SPAudioDataType", []):
                for item in section.get("_items", []):
                    if isinstance(item, dict) and "_name" in item:
                        if ("coreaudio_output_source" in item or
                                "coreaudio_default_output_device" in item):
                            devices.append(item["_name"])
            if not devices:  # fallback: all named items
                for section in data.get("SPAudioDataType", []):
                    for item in section.get("_items", []):
                        if isinstance(item, dict) and "_name" in item:
                            devices.append(item["_name"])
            return list(dict.fromkeys(devices))
        except: pass
    elif IS_LINUX:
        try:
            r = subprocess.run(["pactl","list","sinks","short"],
                               capture_output=True, text=True, timeout=3)
            devs = [l.split()[1] for l in r.stdout.strip().split("\n") if len(l.split())>=2]
            if devs: return devs
        except: pass
        try:
            r = subprocess.run(["aplay","-L"], capture_output=True, text=True, timeout=3)
            return [l.strip() for l in r.stdout.split("\n")
                    if l and not l.startswith(" ")][:20]
        except: pass
    return []

def play_sound(cfg:NotifConfig):
    if not cfg.sound: return
    duration = max(0.1, float(getattr(cfg, "sound_duration", 1.5)))
    volume   = max(0.0, min(2.0, float(getattr(cfg, "sound_volume", 1.0))))

    if IS_WINDOWS:
        try:
            import winsound
            # Windows Beep supports duration but not volume
            chunks = max(1, int(duration / 0.3))
            for _ in range(chunks):
                winsound.Beep(880, 300)
        except Exception as e:
            sys.stderr.write(f"[AIDE] winsound failed: {e}\n")
        return

    cmd_str = cfg.sound_command or _auto_sound_cmd()
    if not cmd_str:
        # terminal bell fallback
        end = time.time() + duration
        while time.time() < end:
            sys.stdout.write("\a"); sys.stdout.flush()
            time.sleep(0.4)
        return

    parts = shlex.split(cmd_str)
    if not parts: return
    prog = parts[0]

    # Apply volume per-platform
    if "afplay" in prog and "-v" not in parts:
        parts = [parts[0], "-v", f"{volume:.2f}"] + parts[1:]
    elif "paplay" in prog and not any(p.startswith("--volume") for p in parts):
        vol_int = min(131072, int(volume * 65536))
        parts.insert(1, f"--volume={vol_int}")

    # Apply device
    if cfg.sound_device:
        if   "afplay" in prog: parts += ["-a", cfg.sound_device]
        elif "paplay" in prog: parts += ["--device", cfg.sound_device]
        elif "aplay"  in prog: parts += ["-D", cfg.sound_device]

    # Loop for the requested duration
    end = time.time() + duration
    first_err = None
    try:
        while time.time() < end:
            try:
                p = subprocess.Popen(parts,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    remaining = max(0.1, end - time.time() + 3)
                    _, err = p.communicate(timeout=remaining)
                    if p.returncode != 0 and err:
                        first_err = err.decode("utf-8", errors="replace").strip()
                        break
                except subprocess.TimeoutExpired:
                    p.terminate(); break
            except FileNotFoundError as e:
                first_err = f"command not found: {prog}"; break
            except Exception as e:
                first_err = str(e); break
    finally:
        if first_err:
            sys.stderr.write(f"[AIDE] sound failed: {first_err}\n")
            sys.stderr.write(f"[AIDE] cmd: {' '.join(parts)}\n")

# ═════════════════════════════════════════════════════════════════════════════
# SHARED CLIPBOARD
# ═════════════════════════════════════════════════════════════════════════════

class SharedClipboard:
    def __init__(self): self._e=[]; self._load()
    def push(self,t):
        self._e=[t]+[x for x in self._e if x!=t]; self._e=self._e[:20]; self._save()
    def all(self): return list(self._e)
    def get(self,i=0): return self._e[i] if i<len(self._e) else ""
    def _save(self):
        try:
            CLIP_FILE.write_text(json.dumps(self._e))
            CLIP_FILE.chmod(0o600)
        except: pass
    def _load(self):
        try: self._e=json.loads(CLIP_FILE.read_text())
        except: self._e=[]

# ═════════════════════════════════════════════════════════════════════════════
# SECURE VAULT (encrypted variables store, key lives in macOS login Keychain)
# ═════════════════════════════════════════════════════════════════════════════

import keyring
from cryptography.fernet import Fernet, InvalidToken

class VaultError(Exception): pass
class VaultKeyUnavailable(VaultError):
    """Raised when the Keychain key can't be read — user cancelled the macOS
    auth prompt, or no key exists and we couldn't create one."""

class SecureVault:
    """Encrypted store for per-tab variables.

    Design:
      • A random Fernet key lives in the **macOS login Keychain** under
        service/account (KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT). The Keychain
        itself is protected by the user's login password, so reading the
        key triggers macOS's native auth dialog (first time; "Always Allow"
        skips future prompts, Touch ID is supported).
      • The encrypted vault file at VAULT_FILE holds all per-tab variables,
        encrypted with that Fernet key. Nothing is written there in cleartext.
      • `unlock()` fetches the key (may block on the macOS prompt) and
        decrypts the file. `lock()` drops the in-memory key; next unlock
        re-fetches it (possibly re-prompting).

    File format (JSON at VAULT_FILE):
        {
          "version":  2,
          "key_source": "macos-keychain",
          "verifier": <fernet-encrypted token of a fixed marker>,
          "data":     <fernet-encrypted JSON of {tab_id_str: {k: v, ...}}>
        }
    """
    _VERIFIER_PLAINTEXT = b"aide-vault-v2"
    KEYCHAIN_SERVICE = "com.itayglick.aide"
    KEYCHAIN_ACCOUNT = "vault-key"

    def __init__(self, path:Path=VAULT_FILE):
        self._path = path
        self._fernet: Optional[Fernet] = None
        self._data: Dict[str,Dict[str,str]] = {}
        self._raw: dict = {}
        self._load_raw()

    # ── state ──────────────────────────────────────────────────────────────────
    def exists(self)->bool:
        return self._path.exists() and bool(self._raw)
    def is_unlocked(self)->bool:
        return self._fernet is not None
    def lock(self):
        self._fernet = None; self._data = {}

    # ── Keychain glue ──────────────────────────────────────────────────────────
    @classmethod
    def _get_key_from_keychain(cls)->Optional[bytes]:
        """Return the stored Fernet key, or None if it is not present.
        May raise VaultKeyUnavailable if the user cancels the auth prompt."""
        try:
            val = keyring.get_password(cls.KEYCHAIN_SERVICE, cls.KEYCHAIN_ACCOUNT)
        except keyring.errors.KeyringError as e:
            raise VaultKeyUnavailable(f"Keychain read failed: {e}") from e
        if val is None: return None
        key = val.encode()
        # Validate the retrieved value is actually a usable Fernet key
        # before handing it to the caller.
        try:
            Fernet(key)
        except Exception as e:
            raise VaultKeyUnavailable(f"Keychain contains invalid key: {e}") from e
        return key

    @classmethod
    def _create_key_in_keychain(cls)->bytes:
        """Generate a new Fernet key and store it in the Keychain."""
        key = Fernet.generate_key()
        try:
            keyring.set_password(cls.KEYCHAIN_SERVICE, cls.KEYCHAIN_ACCOUNT, key.decode())
        except keyring.errors.KeyringError as e:
            raise VaultKeyUnavailable(f"Keychain write failed: {e}") from e
        return key

    @classmethod
    def _delete_key_from_keychain(cls):
        try: keyring.delete_password(cls.KEYCHAIN_SERVICE, cls.KEYCHAIN_ACCOUNT)
        except Exception: pass

    # ── file I/O ───────────────────────────────────────────────────────────────
    def _load_raw(self):
        try:
            self._raw = json.loads(self._path.read_text())
        except FileNotFoundError:
            self._raw = {}   # First run — no file yet, that's fine.
        except (json.JSONDecodeError, OSError) as e:
            # Corrupted or unreadable vault — log and start empty rather than
            # silently dropping data with no indication something went wrong.
            import sys
            print(f"[AIDE] WARNING: vault file unreadable ({e}), starting empty.", file=sys.stderr)
            self._raw = {}

    def _write_raw(self):
        try:
            self._path.write_text(json.dumps(self._raw, indent=2))
        except OSError as e:
            import sys
            print(f"[AIDE] ERROR: cannot write vault file: {e}", file=sys.stderr)
            return
        # Enforce restrictive permissions; warn if they can't be set.
        try:
            os.chmod(self._path, 0o600)
            # Verify the chmod actually took effect.
            if self._path.stat().st_mode & 0o077:
                import sys
                print("[AIDE] WARNING: vault file has insecure permissions!", file=sys.stderr)
        except OSError as e:
            import sys
            print(f"[AIDE] WARNING: could not restrict vault permissions: {e}", file=sys.stderr)

    def _init_empty_file(self, f:Fernet):
        """Write a fresh, empty encrypted vault file using the given Fernet."""
        self._raw = {
            "version": 2,
            "key_source": "macos-keychain",
            "verifier": f.encrypt(self._VERIFIER_PLAINTEXT).decode(),
            "data":     f.encrypt(json.dumps({}).encode()).decode(),
        }
        self._data = {}
        self._write_raw()

    # ── unlock ─────────────────────────────────────────────────────────────────
    def unlock(self)->bool:
        """Fetch the key from Keychain and decrypt the vault file.

        Returns True on success. May raise VaultKeyUnavailable if the user
        cancels the Keychain prompt. On first run (no key, no file) this
        provisions a fresh key + empty vault.
        """
        key = self._get_key_from_keychain()
        file_is_v2 = bool(self._raw) and self._raw.get("version") == 2

        # First run or key rotation: no key yet.
        if key is None:
            # If there is a stale v1 file (from the old password-based vault),
            # throw it away — we can't decrypt it anyway and we don't want to
            # leave orphaned ciphertext on disk.
            if self._raw and self._raw.get("version") != 2:
                try: self._path.unlink()
                except Exception: pass
                self._raw = {}
            key = self._create_key_in_keychain()
            f = Fernet(key)
            self._init_empty_file(f)
            self._fernet = f
            return True

        # We have a key. If the file is missing/old, create a fresh empty one.
        f = Fernet(key)
        if not file_is_v2:
            self._init_empty_file(f)
            self._fernet = f
            return True

        # Normal path: decrypt existing v2 file.
        try:
            if f.decrypt(self._raw["verifier"].encode()) != self._VERIFIER_PLAINTEXT:
                return False
            raw = f.decrypt(self._raw["data"].encode())
            self._data = json.loads(raw.decode() or "{}")
            if not isinstance(self._data, dict): self._data = {}
            self._fernet = f
            return True
        except (InvalidToken, KeyError, ValueError):
            # Key doesn't match file — file was encrypted under a different key.
            # Surface this rather than silently destroying data.
            return False

    # ── variable accessors ─────────────────────────────────────────────────────
    def get_vars(self, tab_id:int)->Dict[str,str]:
        if not self.is_unlocked(): return {}
        return dict(self._data.get(str(tab_id), {}))

    def set_vars(self, tab_id:int, vars_map:Dict[str,str]):
        if not self.is_unlocked(): return
        if vars_map: self._data[str(tab_id)] = dict(vars_map)
        else: self._data.pop(str(tab_id), None)

    def drop_tab(self, tab_id:int):
        self._data.pop(str(tab_id), None)
        if self.is_unlocked(): self.flush()

    # ── GitHub tokens (stored alongside tab vars) ─────────────────────────────
    _GH_KEY = "_github_tokens"

    def get_github_tokens(self) -> Dict[str, str]:
        if not self.is_unlocked(): return {}
        return dict(self._data.get(self._GH_KEY, {}))

    def set_github_tokens(self, tokens: Dict[str, str]):
        if not self.is_unlocked(): return
        if tokens:
            self._data[self._GH_KEY] = dict(tokens)
        else:
            self._data.pop(self._GH_KEY, None)

    def flush(self):
        """Re-encrypt in-memory data and persist to disk. No-op if locked."""
        if not self.is_unlocked(): return
        assert self._fernet is not None
        self._raw["data"] = self._fernet.encrypt(json.dumps(self._data).encode()).decode()
        self._write_raw()


# ═════════════════════════════════════════════════════════════════════════════
# TERMINAL SESSION
# ═════════════════════════════════════════════════════════════════════════════

from collections import deque as _deque

class _ScrollScreen(pyte.Screen):
    """pyte.Screen subclass that captures lines scrolling off the top into a deque."""
    MAX_SCROLLBACK = 2_000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scrollback: _deque = _deque(maxlen=self.MAX_SCROLLBACK)

    def index(self):
        top = getattr(self, 'margins', None)
        top_row = top.top if top else 0
        row = dict(self.buffer[top_row])
        # Deduplicate: skip consecutive identical lines (e.g. spinner frames that
        # happen to trigger index() — prevents scrollback filling with hundreds of
        # nearly-identical "⠸ Thinking…" rows that look repetitive when scrolling.
        if not self.scrollback or row != self.scrollback[-1]:
            self.scrollback.append(row)
        super().index()

    def erase_in_display(self, how=0, *args, **kwargs):
        # When the screen is cleared (how=2 or 3), save all non-empty visible
        # lines into scrollback so they aren't lost forever.
        if how in (2, 3):
            for y in range(self.lines):
                row = self.buffer[y]
                if any((row.get(x) or type("_",(),{"data":" "})()).data.strip() for x in range(self.columns)):
                    self.scrollback.append(dict(row))
        super().erase_in_display(how, *args, **kwargs)

def _shorten_path(path:str)->str:
    home=str(Path.home())
    if path.startswith(home): path="~"+path[len(home):]
    parts=path.split("/")
    return "/".join(["…"]+parts[-2:]) if len(parts)>4 else path

@dataclass
class TermInfo:
    cwd:str="~"; last_cmd:str=""; ssh_host:str=""
    process:str=""; title:str=""; local_url:str=""; cwd_full:str=""

_EVENT_Q: queue.Queue = queue.Queue()
_ANSI_RE = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]|\x1b[@-_]|\x1b[NOP]')

class TermSession:
    _AI_PATS = [
        (re.compile(r"\[y/n\]|\[Y/n\]|\[yes/no\]",re.I), "Waiting for confirmation"),
        (re.compile(r"Press any key",re.I),               "Waiting for keypress"),
        (re.compile(r">>>\s*$",re.M),                     "Python REPL waiting"),
        (re.compile(r"Esc to cancel"),                    "Claude is waiting for your choice"),
    ]
    # Claude CLI spinner detection.
    # _THINKING_RE: braille char + anything on same line + "Thinking" — handles all ANSI variants.
    # _WORKING_RE:  any braille char at all — presence alone is unambiguous (Claude Code is the
    #               only common tool using Braille Pattern chars as a UI spinner).
    # _DONE_RE:     closing ╰─ box border signals Claude finished writing its response.
    # _START_RE:    opening ╭─ border — transitions thinking→working.
    _SPINNER_CHARS = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    _THINKING_RE = re.compile(rf'[{_SPINNER_CHARS}][^\n]*[Tt]hinking')
    _WORKING_RE  = re.compile(rf'[{_SPINNER_CHARS}]')
    _DONE_RE     = re.compile(r'╰[─━]{3,}')
    _START_RE    = re.compile(r'╭[─━]{3,}')
    # Safety-net decay: if no spinner arrives within this window, assume Claude finished.
    # 3 s is long enough to survive slow tool calls but short enough to feel responsive.
    _AI_IDLE_SECS = 3.0
    _URL_RE   = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d+\S*)")
    _TAIL_LEN = 3000

    def __init__(self, tab_id:int, cols:int=80, rows:int=24):
        self.tab_id=tab_id; self.cols=cols; self.rows=rows
        self.custom_title=""; self.notes=""; self.tasks=""; self.tags:list=[]; self.variables:Dict[str,str]={}
        self.autostart_dir:str=""; self.autostart_cmd:str=""
        self.github_token_name:str=""   # name of the token in vault to inject as GITHUB_TOKEN/GH_TOKEN
        self.claude_profile:str=""      # name used for CLAUDE_CONFIG_DIR; empty = shared ~/.claude/
        self.claude_model:str=""        # --model flag; empty = claude default
        self.claude_args:str=""         # extra CLI args appended when launching `claude`
        self.tokens_used:int=0          # cumulative token count parsed from claude output
        self.browser_url:str=""; self.watching=False
        self.info=TermInfo()
        self.screen=_ScrollScreen(cols,rows)
        try:    self.stream=pyte.ByteStream(self.screen); self._sf=False
        except: self.stream=pyte.Stream(self.screen);    self._sf=True
        self.master_fd=-1; self.pid=-1; self.alive=False; self.dirty=False
        self.last_out_time=0.0; self._notif_armed=False
        self._input_buf=bytearray(); self._output_tail=""
        self.waiting_input=False; self.scroll_offset=0; self.last_ping_time:float=0.0; self.last_waiting_at:float=0.0
        self.pending_validation:bool=False; self.validation_note:str=""
        self.claude_resume_cmd:str=""; self.claude_working:bool=False; self.claude_thinking:bool=False
        self._ai_active_time:float=0.0   # last time working/thinking was detected
        self._in_response_box:bool=False  # True between ╭─ and ╰─
        self.neural_on_bus:bool=False      # registered with Neural bus
        self._neural_profile:Optional[dict]=None  # {name,tag,app,role,task}
        self._thread:Optional[threading.Thread]=None

    def start(self, shell:str, env_overrides:Optional[Dict[str,str]]=None)->None:
        self._env_overrides=env_overrides or {}
        if IS_WINDOWS: self._start_windows(shell)
        else:          self._start_unix(shell)
        self._thread=threading.Thread(target=self._read_loop,daemon=True)
        self._thread.start()

    def _start_unix(self, shell:str)->None:
        master_fd,slave_fd=pty.openpty()
        self._set_ws(slave_fd)
        pid=os.fork()
        if pid==0:
            os.close(master_fd); os.setsid()
            try: fcntl.ioctl(slave_fd,termios.TIOCSCTTY,0)
            except OSError: pass  # Not all platforms support TIOCSCTTY
            for fd in range(3): os.dup2(slave_fd,fd)
            if slave_fd>2: os.close(slave_fd)
            if self.info.cwd_full:
                try: os.chdir(self.info.cwd_full)
                except OSError: pass
            env=dict(os.environ,TERM="xterm-256color",COLORTERM="truecolor",**self._env_overrides)
            os.execvpe(shell,[shell],env); os._exit(1)  # noqa
        else:
            os.close(slave_fd); self.master_fd=master_fd; self.pid=pid; self.alive=True

    def _start_windows(self,shell:str)->None:
        env=dict(os.environ,**self._env_overrides)
        cwd=self.info.cwd_full if self.info.cwd_full and os.path.isdir(self.info.cwd_full) else None
        self._proc=subprocess.Popen([shell],stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                    bufsize=0,env=env,cwd=cwd)
        self.pid=self._proc.pid; self.alive=True

    def _set_ws(self,fd:int)->None:
        try: fcntl.ioctl(fd,termios.TIOCSWINSZ,struct.pack("HHHH",self.rows,self.cols,0,0))
        except: pass

    def _read_loop(self)->None:
        while self.alive:
            try:
                if IS_WINDOWS:
                    data=self._proc.stdout.read(4096)
                    if not data: break
                else:
                    r,_,_=select.select([self.master_fd],[],[],0.1)
                    if not r: continue
                    data=os.read(self.master_fd,16384)
                    if not data: break
                self._handle(data)
            except OSError: break
        self.alive=False

    def _handle(self,data:bytes)->None:
        text=data.decode("utf-8",errors="replace")
        self._output_tail=(self._output_tail+text)[-self._TAIL_LEN:]
        if cwd:=self._osc7(data): self.info.cwd=_shorten_path(cwd); self.info.cwd_full=cwd
        if m:=self._URL_RE.search(text): self.info.local_url=m.group(0)
        # Only accept resume tokens that are strictly alphanumeric + hyphens/underscores.
        # This prevents malicious terminal output from injecting shell metacharacters.
        if m:=re.search(r"claude --resume ([a-zA-Z0-9_-]+)",text):
            self.claude_resume_cmd=f"claude --resume {m.group(1)}"
        # Accumulate token counts from Claude Code's session-end summary line.
        # Matches both "Tokens: 1,234 input · 567 output" and the ≈ variant.
        if m:=re.search(r"(\d[\d,]+)\s+input\s+[·•]\s*(\d[\d,]+)\s+output",text):
            try:
                inp=int(m.group(1).replace(",",""))
                out=int(m.group(2).replace(",",""))
                self.tokens_used+=inp+out
            except ValueError:
                pass
        # Spinner detection — braille char alone is sufficient; _THINKING_RE is checked first
        # so the thinking/working distinction is preserved when text follows on the same line.
        _had_spinner = False
        if self._THINKING_RE.search(text):
            self.claude_thinking=True; self.claude_working=False
            self.waiting_input=False
            self._ai_active_time=time.time(); _had_spinner=True
        elif self._WORKING_RE.search(text):
            self.claude_working=True; self.claude_thinking=False
            self.waiting_input=False
            self._ai_active_time=time.time(); _had_spinner=True
        # ╭─ box opening: transition thinking→working; also marks entry into a response box
        if self._START_RE.search(text):
            self._in_response_box=True
            self._ai_active_time=time.time()
            if not _had_spinner and self.claude_thinking:
                self.claude_thinking=False; self.claude_working=True
        # ╰─ box closing: Claude finished its response.
        # _was_active covers all signals: spinner, thinking→working transition, or ╭─ box-open.
        # The 300 ms debounce only fires notification if waiting_input is still True at that
        # point — intermediate tool-result boxes get a new spinner within 300 ms which clears it.
        _was_active = self.claude_working or self.claude_thinking or self._in_response_box
        if self._DONE_RE.search(text):
            self._in_response_box=False
            self.claude_working=False; self.claude_thinking=False
            if _was_active or _had_spinner:
                self.waiting_input=True
                self.last_ping_time=time.time(); self.last_waiting_at=self.last_ping_time
                threading.Timer(0.3, self._fire_wait_events).start()
        if self._sf: self.stream.feed(text)
        else:        self.stream.feed(data)
        self.dirty=True; self.last_out_time=time.time(); self._notif_armed=True
        if self.screen.title and self.screen.title!=self.info.title:
            self.info.title=self.screen.title; self._parse_ssh(self.screen.title)
        # Explicit prompts ([y/n], Python REPL, etc.) — fire immediately, no debounce needed
        clean = _ANSI_RE.sub('', text)
        was_waiting=self.waiting_input
        for pat,msg in self._AI_PATS:
            if pat.search(clean):
                self.waiting_input=True; self.claude_working=False; self.claude_thinking=False
                self.last_ping_time=time.time(); self.last_waiting_at=self.last_ping_time
                if not was_waiting:
                    _EVENT_Q.put(("blink",self.tab_id,msg))
                    _EVENT_Q.put(("notif",self.tab_id,msg,self._output_tail))
                break

    def _fire_wait_events(self):
        """Called 300 ms after ╰─ is detected. Only fires if still waiting (not cleared by a new spinner)."""
        if self.waiting_input and not self.claude_working and not self.claude_thinking:
            _EVENT_Q.put(("blink",self.tab_id,"Claude is waiting"))
            _EVENT_Q.put(("notif",self.tab_id,"Claude is waiting",self._output_tail))

    # Valid hostname: letters, digits, hyphens, dots — no shell metacharacters.
    _HOSTNAME_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9\-.]*[a-zA-Z0-9])?$')

    def _osc7(self, data:bytes)->Optional[str]:
        """Parse OSC 7 (working-directory notification).
        Also extracts the remote hostname when the file:// URL has a non-local host."""
        m=re.search(rb"\x1b]7;file://([^/\x07\x1b]*)(\/[^\x07\x1b]*?)(?:\x07|\x1b\\)",data)
        if not m: return None
        host=m.group(1).decode("utf-8",errors="replace")
        path=unquote(m.group(2).decode("utf-8",errors="replace"))
        # Validate and sanitize the path — reject traversal sequences and non-absolute paths.
        if not path.startswith("/") or "\x00" in path:
            return None
        # Normalize and reject any remaining traversal after normalization.
        import posixpath
        clean = posixpath.normpath(path)
        if ".." in clean.split("/"):
            return None
        if host and host not in ("","localhost","127.0.0.1"):
            # Validate hostname format before storing
            if self._HOSTNAME_RE.match(host):
                self.info.ssh_host=host
        return clean

    def _parse_ssh(self,title:str)->None:
        """Detect SSH host from terminal title.

        Common formats emitted by remote shells:
          user@host: ~/path          (Ubuntu default)
          user@host ~/path $         (no colon)
          host: ~/path
          [user@host path]$
        """
        _LOCAL = {"localhost","127.0.0.1",""}
        for pat in [
            r"(?:[\w.-]+@)?([\w][\w.-]+)\s*:",        # user@host: path
            r"(?:[\w.-]+@)?([\w][\w.-]+)\s+[\~/]",    # user@host ~/path
            r"\[(?:[\w.-]+@)?([\w][\w.-]+)\s",        # [user@host path]$
        ]:
            m=re.search(pat, title)
            if m:
                candidate=m.group(1)
                if candidate not in _LOCAL:
                    self.info.ssh_host=candidate; return

    def write(self,data:bytes)->None:
        if not self.alive: return
        # If the user just replied to Claude (was waiting → now sending Enter),
        # flip to "working" immediately so the card stays 🤖 without a blank gap
        # between sending the prompt and when Claude starts its spinner output.
        if self.waiting_input and any(b in (0x0D,0x0A) for b in data):
            self.waiting_input=False
            self.claude_working=True; self.claude_thinking=False
            self._ai_active_time=time.time()
        else:
            self.waiting_input=False
        actual_data=data
        for b in data:
            if b in (0x0D,0x0A):
                try:
                    cmd=bytes(self._input_buf).decode("utf-8",errors="replace").strip()
                    if cmd:
                        if cmd=="claude" and self.claude_resume_cmd:
                            erase=b'\x7f'*len(self._input_buf)
                            actual_data=erase+self.claude_resume_cmd.encode("utf-8")+bytes([b])
                        self.info.last_cmd=cmd
                        if cmd.startswith("ssh "):
                            for p in cmd.split()[1:]:
                                if not p.startswith("-"):
                                    candidate = p.split("@")[-1]
                                    # Validate hostname: only allow safe characters
                                    if self._HOSTNAME_RE.match(candidate):
                                        self.info.ssh_host = candidate
                                    break
                        elif cmd.strip() in ("exit","logout"): self.info.ssh_host=""
                except (UnicodeDecodeError, ValueError):
                    pass  # Malformed input bytes — skip silently
                self._input_buf.clear()
            elif b in (0x7F,0x08):
                if self._input_buf: self._input_buf.pop()
            elif 0x20<=b<0x7F: self._input_buf.append(b)
        try:
            if IS_WINDOWS:
                written = self._proc.stdin.write(actual_data)
                self._proc.stdin.flush()
                if written != len(actual_data):
                    self.alive = False  # Partial write — process likely dying
            else:
                os.write(self.master_fd, actual_data)
        except (OSError, BrokenPipeError):
            self.alive = False

    def resize(self,cols:int,rows:int)->None:
        if cols==self.cols and rows==self.rows: return
        self.cols,self.rows=cols,rows; self.screen.resize(rows,cols)
        if not IS_WINDOWS and self.master_fd>=0: self._set_ws(self.master_fd)

    def kill(self)->None:
        self.alive=False
        try:
            if not IS_WINDOWS: os.kill(self.pid,signal.SIGTERM)
        except: pass

    def effective_title(self)->str:
        return self.custom_title or self.info.title or f"Terminal {self.tab_id+1}"

    def screen_text(self)->str:
        lines=[]
        for y in range(self.screen.lines):
            row=self.screen.buffer[y]
            lines.append("".join(row[x].data or " " for x in range(self.screen.columns)).rstrip())
        return "\n".join(lines)

    def to_dict(self)->dict:
        # NB: variables are NEVER persisted here — they live encrypted in the
        # SecureVault (VAULT_FILE). Only keep them in memory on this object.
        d = dict(custom_title=self.custom_title,notes=self.notes,tasks=self.tasks,tags=self.tags,
                 autostart_dir=self.autostart_dir,autostart_cmd=self.autostart_cmd,
                 github_token_name=self.github_token_name,
                 claude_profile=self.claude_profile, claude_model=self.claude_model,
                 claude_args=self.claude_args, tokens_used=self.tokens_used,
                 browser_url=self.browser_url,watching=self.watching,
                 cwd=self.info.cwd_full or self.info.cwd,
                 ssh_host=self.info.ssh_host,last_cmd=self.info.last_cmd)
        if self.neural_on_bus and self._neural_profile:
            d["neural"] = self._neural_profile
        return d

    @classmethod
    def from_dict(cls,d:dict,tab_id:int)->"TermSession":
        s=cls(tab_id); s.custom_title=d.get("custom_title",""); s.notes=d.get("notes","")
        s.tasks=d.get("tasks",""); s.tags=d.get("tags",[]); s.variables={}  # populated from vault on unlock
        s.autostart_dir=d.get("autostart_dir",""); s.autostart_cmd=d.get("autostart_cmd","")
        s.github_token_name=d.get("github_token_name","")
        s.claude_profile=d.get("claude_profile",""); s.claude_model=d.get("claude_model","")
        s.claude_args=d.get("claude_args",""); s.tokens_used=d.get("tokens_used",0)
        s.browser_url=d.get("browser_url",""); s.watching=d.get("watching",False)
        stored_cwd=d.get("cwd","")
        s.info.cwd_full=stored_cwd
        s.info.cwd=_shorten_path(stored_cwd) if stored_cwd else "~"
        s.info.ssh_host=d.get("ssh_host",""); s.info.last_cmd=d.get("last_cmd","")
        s._neural_profile = d.get("neural", None)  # restored in MainWindow after bus starts
        # ── v4 migration: extract resume token and working dir from autostart_cmd ──
        cmd = s.autostart_cmd or ""
        if not s.claude_resume_cmd:
            if m := re.search(r"--resume\s+([a-zA-Z0-9_-]+)", cmd):
                s.claude_resume_cmd = f"claude --resume {m.group(1)}"
        if not s.autostart_dir:
            if m := re.match(r"(?:cd\s+)?([~/][^\s;&|]+)(?:\s*[;&|]|$)", cmd):
                candidate = m.group(1).strip()
                if candidate.startswith(("~", "/")):
                    s.autostart_dir = candidate
        return s

# ═════════════════════════════════════════════════════════════════════════════
# SCREENSHOT OVERLAY
# ═════════════════════════════════════════════════════════════════════════════

class ScreenshotOverlay(QWidget):
    """Translucent overlay showing the last screenshot of a terminal session."""
    dismissed = pyqtSignal()

    def __init__(self, pixmap: QPixmap, parent: QWidget):
        super().__init__(parent)
        self._pixmap = pixmap
        self.setGeometry(parent.rect())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.raise_()
        self.show()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._pixmap)
        # dim overlay
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        # bottom bar
        bar = QRect(0, self.height() - 30, self.width(), 30)
        p.fillRect(bar, QColor(13, 17, 23, 210))
        p.setPen(QColor(88, 166, 255, 220))
        p.setFont(QFont(FONT_FAMILY, 11))
        p.drawText(bar, Qt.AlignmentFlag.AlignCenter,
                   "↩  Last session view  —  click or press any key to continue")
        p.end()

    def mousePressEvent(self, _): self.dismissed.emit()

# ═════════════════════════════════════════════════════════════════════════════
# TERMINAL WIDGET
# ═════════════════════════════════════════════════════════════════════════════

class TerminalWidget(QWidget):
    prefix_action   = pyqtSignal(str)
    split_tab_paste = pyqtSignal(str)

    sent_to_waiting = pyqtSignal()

    in_split: bool = False  # set by AIDEWindow when split mode is active

    _PREFIX_MAP = {
        Qt.Key.Key_N:"new_tab",         Qt.Key.Key_W:"close_tab",
        Qt.Key.Key_R:"rename_tab",      Qt.Key.Key_Comma:"rename_tab",
        Qt.Key.Key_Right:"next_tab",    Qt.Key.Key_Left:"prev_tab",
        Qt.Key.Key_P:"toggle_notes",    Qt.Key.Key_Y:"copy_screen",
        Qt.Key.Key_V:"clipboard_menu",  Qt.Key.Key_X:"toggle_watch",
        Qt.Key.Key_Bar:"split_term",    Qt.Key.Key_B:"split_browse",
        Qt.Key.Key_C:"configure_cards",
        Qt.Key.Key_S:"configure_notifs",Qt.Key.Key_D:"show_notif_detail",
        Qt.Key.Key_K:"open_settings",  Qt.Key.Key_G:"github_tokens",
    }

    def __init__(self, session:Optional[TermSession]=None, parent=None):
        super().__init__(parent)
        self.session  = session
        self._prefix  = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,True)
        self.setAcceptDrops(True)

        self._font_n = QFont(FONT_FAMILY, FONT_SIZE)
        self._font_n.setFixedPitch(True)
        self._font_b = QFont(self._font_n); self._font_b.setBold(True)

        fm = QFontMetrics(self._font_n)
        self._cw = fm.horizontalAdvance("M")
        self._ch = fm.height()

        # scrollbar overlay (right edge)
        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.setFixedWidth(12)
        self._scrollbar.setStyleSheet("QScrollBar:vertical{background:#0d1117;width:12px;border:none;}QScrollBar::handle:vertical{background:#444;border-radius:5px;min-height:20px;}QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self._scrollbar.setRange(0,0); self._scrollbar.setValue(0)
        self._scrollbar.valueChanged.connect(self._on_scroll)
        self._scrollbar.setVisible(False)

        self._overlay: Optional[ScreenshotOverlay] = None
        self._scroll_accum = 0.0  # accumulated fractional wheel delta for smooth trackpad scrolling
        # text selection state: (col,row) in screen coords, None when no selection
        self._sel_start: Optional[tuple] = None
        self._sel_end:   Optional[tuple] = None
        self._selecting = False

        t = QTimer(self); t.timeout.connect(self._tick); t.start(100)

    def set_session(self, s:Optional[TermSession]):
        self.session=s; self._sel_start=None; self._sel_end=None
        if s: self._update_scrollbar()
        self.update()

    def show_screenshot(self, pixmap: QPixmap):
        self._dismiss_overlay()
        self._overlay = ScreenshotOverlay(pixmap, self)
        self._overlay.setGeometry(self.rect())
        self._overlay.dismissed.connect(self._dismiss_overlay)

    def _dismiss_overlay(self):
        ov = self._overlay
        self._overlay = None   # clear the ref FIRST so re-entry is safe
        if ov is None: return
        try:
            ov.hide()
            ov.deleteLater()
        except RuntimeError:
            pass  # C++ object already gone

    def _sel_norm(self):
        """Return (start, end) in reading order."""
        a, b = self._sel_start, self._sel_end
        if a is None or b is None: return None, None
        return (a, b) if (a[1], a[0]) <= (b[1], b[0]) else (b, a)

    def _sel_text(self) -> str:
        """Extract selected text from the screen buffer."""
        if not self.session: return ""
        s, e = self._sel_norm()
        if s is None: return ""
        lines = []
        for row in range(s[1], e[1]+1):
            r = self._get_row(row)
            c0 = s[0] if row == s[1] else 0
            c1 = e[0] if row == e[1] else self.session.screen.columns
            line = "".join(
                (r.get(c) or type("_",(),{"data":" "})()).data
                if isinstance(r, dict) else r[c].data
                for c in range(c0, c1+1) if r
            ).rstrip()
            lines.append(line)
        return "\n".join(lines)

    def _tick(self):
        if self.session and self.session.dirty and self.isVisible():
            self.session.dirty=False
            self._update_scrollbar()
            self.update()

    def _update_scrollbar(self):
        if not self.session: return
        sb_len=len(self.session.screen.scrollback)
        self._scrollbar.blockSignals(True)
        self._scrollbar.setRange(0,sb_len)
        # value = how far scrolled back from current; 0 at bottom means current view
        # We store scroll_offset on the session (0=current).
        # Map: scrollbar.value = sb_len - scroll_offset  (higher=newer)
        if self.session.scroll_offset==0:
            self._scrollbar.setValue(sb_len)
        else:
            self._scrollbar.setValue(sb_len - self.session.scroll_offset)
        self._scrollbar.blockSignals(False)
        self._scrollbar.setVisible(sb_len>0)
        self._scrollbar.setGeometry(self.width()-12,0,12,self.height())

    def _on_scroll(self,value:int):
        if not self.session: return
        sb_len=len(self.session.screen.scrollback)
        self.session.scroll_offset=max(0, sb_len-value)
        self.update()

    def _get_row(self,y:int):
        """Return the char row for display line y, accounting for scroll offset."""
        s=self.session
        if not s: return {}
        off=s.scroll_offset
        if off==0:
            return s.screen.buffer[y]
        sb=s.screen.scrollback; sb_len=len(sb)
        off=min(off, sb_len)   # clamp: can't scroll past what's in the buffer
        if y < off:
            idx=sb_len - off + y
            return sb[idx] if 0<=idx<sb_len else {}
        else:
            screen_y=y-off
            return s.screen.buffer[screen_y] if screen_y<s.screen.lines else {}

    def resizeEvent(self,event:QResizeEvent):
        super().resizeEvent(event)
        if self.session:
            cols=max(1,event.size().width()//self._cw)
            rows=max(1,event.size().height()//self._ch)
            self.session.resize(cols,rows)
        self._scrollbar.setGeometry(self.width()-12,0,12,self.height())
        if self._overlay: self._overlay.setGeometry(self.rect())

    def wheelEvent(self,event):
        if not self.session: return
        delta=event.angleDelta().y()
        # Accumulate fractional delta so Mac trackpad micro-swipes add up
        # smoothly instead of clamping every tick to 1 line.
        self._scroll_accum += delta / 40.0
        lines = int(self._scroll_accum)
        if lines == 0: return
        self._scroll_accum -= lines
        sb_len=len(self.session.screen.scrollback)
        if lines>0:
            self.session.scroll_offset=min(self.session.scroll_offset+lines,sb_len)
        else:
            self.session.scroll_offset=max(0,self.session.scroll_offset+lines)
        self._update_scrollbar(); self.update()

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.fillRect(self.rect(),C_BG)
        if not self.session:
            painter.setFont(self._font_n); painter.setPen(C_MUTED)
            painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,f"{APP_NAME}  —  starting…")
            painter.end(); return

        screen  = self.session.screen
        scrolled = self.session.scroll_offset > 0
        focused = self.hasFocus() and not scrolled
        cur_y, cur_x = screen.cursor.y, screen.cursor.x

        for y in range(screen.lines):
            row = self._get_row(y); py = y*self._ch

            # background pass
            x=0
            while x < screen.columns:
                ch=row.get(x) if isinstance(row,dict) else row[x]
                if ch is None: x+=1; continue
                is_cur=focused and y==cur_y and x==cur_x
                rv=ch.reverse^is_cur
                bg=pyte_color(ch.bg,True,rv)
                x2=x+1
                while x2 < screen.columns:
                    ch2=row.get(x2) if isinstance(row,dict) else row[x2]
                    if ch2 is None: break
                    is_cur2=focused and y==cur_y and x2==cur_x
                    rv2=ch2.reverse^is_cur2
                    if pyte_color(ch2.bg,True,rv2)!=bg: break
                    x2+=1
                if bg!=C_BG:
                    painter.fillRect(x*self._cw,py,(x2-x)*self._cw,self._ch,bg)
                x=x2

            # text pass
            x=0
            while x < screen.columns:
                ch=row.get(x) if isinstance(row,dict) else row[x]
                if ch is None: x+=1; continue
                char=ch.data or " "
                if char==" ": x+=1; continue
                is_cur=focused and y==cur_y and x==cur_x
                rv=ch.reverse^is_cur
                fg=pyte_color(ch.fg,False,rv); bold=ch.bold
                run=[char]; x2=x+1
                while x2 < screen.columns:
                    ch2=row.get(x2) if isinstance(row,dict) else row[x2]
                    if ch2 is None: break
                    char2=ch2.data or " "
                    if char2==" ": break
                    is_cur2=focused and y==cur_y and x2==cur_x
                    rv2=ch2.reverse^is_cur2
                    if pyte_color(ch2.fg,False,rv2)!=fg or ch2.bold!=bold: break
                    run.append(char2); x2+=1
                painter.setFont(self._font_b if bold else self._font_n)
                painter.setPen(fg)
                for i,c in enumerate(run):
                    painter.drawText(
                        QRect((x+i)*self._cw,py,self._cw,self._ch),
                        Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter, c)
                x=x2

        # cursor (only when not scrolled back)
        if not scrolled and cur_y<screen.lines and cur_x<screen.columns:
            if focused:
                # filled block cursor — draw explicitly so it shows on empty cells too
                painter.fillRect(cur_x*self._cw,cur_y*self._ch,self._cw,self._ch,C_CURSOR)
                row=self._get_row(cur_y)
                ch=row.get(cur_x) if isinstance(row,dict) else row[cur_x]
                if ch and ch.data and ch.data!=" ":
                    painter.setFont(self._font_b if ch.bold else self._font_n)
                    painter.setPen(C_BG)
                    painter.drawText(QRect(cur_x*self._cw,cur_y*self._ch,self._cw,self._ch),
                                     Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,ch.data)
            else:
                painter.setPen(QPen(C_CURSOR,1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(cur_x*self._cw,cur_y*self._ch,self._cw-1,self._ch-1)

        # selection highlight
        sel_s, sel_e = self._sel_norm()
        if sel_s is not None:
            sel_color = QColor(88, 166, 255, 70)
            for row in range(sel_s[1], sel_e[1]+1):
                c0 = sel_s[0] if row==sel_s[1] else 0
                c1 = (sel_e[0]+1) if row==sel_e[1] else screen.columns
                painter.fillRect(c0*self._cw, row*self._ch,
                                 (c1-c0)*self._cw, self._ch, sel_color)

        painter.end()

    def keyPressEvent(self,event:QKeyEvent):
        key=event.key(); mods=event.modifiers()
        ctrl=bool(mods&Qt.KeyboardModifier.ControlModifier)
        shift=bool(mods&Qt.KeyboardModifier.ShiftModifier)
        K=Qt.Key
        if ctrl and key==K.Key_B:
            if self._prefix:
                if self.session: self.session.write(b"\x02")
                self._prefix=False
            else:
                self._prefix=True
            return
        if self._prefix:
            self._prefix=False
            if K.Key_1<=key<=K.Key_9:
                self.prefix_action.emit(f"goto_{key-K.Key_0}"); return
            if action:=self._PREFIX_MAP.get(key):
                self.prefix_action.emit(action)
            return
        # Windows-style shortcuts (direct, no Ctrl+B prefix needed)
        if ctrl and not shift and key==K.Key_T:
            self.prefix_action.emit("new_tab"); return
        if ctrl and not shift and key==K.Key_W:
            self.prefix_action.emit("close_tab"); return
        if ctrl and key==K.Key_Tab:
            self.prefix_action.emit("next_tab" if not shift else "prev_tab"); return
        if ctrl and not shift and key==K.Key_Up:
            self.prefix_action.emit("prev_tab"); return
        if ctrl and not shift and key==K.Key_Down:
            self.prefix_action.emit("next_tab"); return
        if ctrl and not shift and K.Key_1<=key<=K.Key_9:
            n = key - K.Key_0
            if self.in_split and 1 <= n <= 6:
                self.prefix_action.emit(f"focus_pane_{n}"); return
            self.prefix_action.emit(f"goto_{n}"); return
        if key==K.Key_plusminus or event.text()=="±":
            self.prefix_action.emit("focus_notes"); return
        if self._overlay: self._dismiss_overlay(); return
        # With an active selection: Cmd/Ctrl+C or Enter/Return copies it to
        # the clipboard and clears the selection (not forwarded to the shell).
        # On macOS Qt swaps Ctrl and Meta, so `ctrl` here is actually Cmd.
        if self._sel_start and (
            (ctrl and key==K.Key_C) or key in (K.Key_Return, K.Key_Enter)
        ):
            txt=self._sel_text()
            if txt: QApplication.clipboard().setText(txt)
            self._sel_start=None; self._sel_end=None; self.update(); return
        # Tab in split-terminal mode with an active selection → paste to other pane + smash
        if key==K.Key_Tab and self.in_split and self._sel_start:
            txt=self._sel_text()
            if txt:
                self._sel_start=None; self._sel_end=None; self.update()
                self.split_tab_paste.emit(txt)
                return
        # Cmd/Ctrl+V → paste from clipboard into the running shell. (On Mac
        # this is Cmd+V; on Linux/Windows it is Ctrl+V — same key path because
        # of the Qt Ctrl/Meta swap.)
        if ctrl and not shift and key==K.Key_V and self.session:
            cb = QApplication.clipboard()
            mime = cb.mimeData()
            # Prefer file URLs over text — Finder file copies set both the
            # filename as text and the full path as a URL; users want the path.
            if mime and mime.hasUrls():
                paths = " ".join(
                    shlex.quote(u.toLocalFile())
                    for u in mime.urls()
                    if u.isLocalFile() and "\x00" not in u.toLocalFile()
                )
                if paths:
                    self.session.scroll_offset = 0
                    self.session.write(paths.encode("utf-8"))
                    return
            text = cb.text()
            if text:
                self.session.scroll_offset=0
                self.session.write(text.encode("utf-8"))
            return
        if self._sel_start:
            self._sel_start=None; self._sel_end=None; self.update()
        data=qt_key_to_bytes(event)
        if data and self.session:
            was_waiting = self.session.waiting_input
            self.session.scroll_offset=0  # scroll to bottom on any keypress
            self.session.write(data)
            if was_waiting and (b'\r' in data or b'\n' in data):
                self.sent_to_waiting.emit()

    def _pos_to_cell(self, pos) -> tuple:
        return (max(0, min(int(pos.x()/self._cw), (self.session.screen.columns if self.session else 80)-1)),
                max(0, min(int(pos.y()/self._ch), (self.session.screen.lines if self.session else 24)-1)))

    _URL_RE = re.compile(r'https?://[^\s<>\'")\]]+')

    def _url_at(self, col: int, row: int) -> Optional[str]:
        """Extract the URL surrounding (col, row) on the terminal screen, if any."""
        if not self.session: return None
        r = self._get_row(row)
        if not r: return None
        cols = self.session.screen.columns
        line = "".join(
            (r.get(c) or type("_",(),{"data":" "})()).data
            if isinstance(r, dict) else r[c].data
            for c in range(cols)
        ).rstrip()
        for m in self._URL_RE.finditer(line):
            if m.start() <= col <= m.end():
                return m.group(0).rstrip(".,;:!?")
        return None

    def mousePressEvent(self,event):
        self.setFocus()
        if self._overlay: self._dismiss_overlay(); return
        if event.button()!=Qt.MouseButton.LeftButton or not self.session:
            super().mousePressEvent(event); return
        # Ctrl+click (Cmd+click on macOS) → open URL under cursor
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            col, row = self._pos_to_cell(event.position())
            url = self._url_at(col, row)
            if url:
                webbrowser.open(url)
                return
        # Start a selection at the click position.
        self._sel_start=self._pos_to_cell(event.position())
        self._sel_end=self._sel_start; self._selecting=True
        self.update()

    def mouseMoveEvent(self, event):
        if self._selecting and self.session:
            self._sel_end=self._pos_to_cell(event.position()); self.update()
            return
        # Ctrl held → show pointing hand if hovering a URL
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self.session:
            col, row = self._pos_to_cell(event.position())
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._url_at(col, row)
                           else Qt.CursorShape.IBeamCursor)
        else:
            self.setCursor(Qt.CursorShape.IBeamCursor)

    def mouseReleaseEvent(self, event):
        if not self._selecting: return
        self._selecting=False
        s, e = self._sel_norm()
        if s is None or s==e:
            # Plain click (no drag) — just clear any stale selection.
            # Do NOT send cursor-movement sequences to the shell; standard
            # terminals only do that when the running program enables mouse
            # reporting, and sending unsolicited arrow keys interrupts Claude
            # and other interactive programs.
            self._sel_start=None; self._sel_end=None; self.update()
        else:
            # Drag — copy selected text automatically.
            txt=self._sel_text()
            if txt: QApplication.clipboard().setText(txt)

    def mouseDoubleClickEvent(self, event):
        if self.session and self.session.alive:
            self.session.write(b"\r")

    def set_font_size(self,size:int):
        self._font_n=QFont(FONT_FAMILY,size); self._font_n.setFixedPitch(True)
        self._font_b=QFont(self._font_n); self._font_b.setBold(True)
        fm=QFontMetrics(self._font_n)
        self._cw=fm.horizontalAdvance("M"); self._ch=fm.height()
        self.update()

    @staticmethod
    @staticmethod
    def _image_to_ascii(img, width: int = 160) -> str:
        """Convert a QImage to an ASCII-art string.

        Characters are roughly 2× taller than wide, so the rendered height is
        halved to maintain the original image aspect ratio.
        """
        # Palette: 10 characters ordered light→dark (space = brightest)
        _CHARS = " .:-=+*#%@"
        if img.isNull():
            return ""
        # Scale to target width; account for character aspect ratio (~1:2 w:h)
        src_w = img.width(); src_h = img.height()
        if src_w == 0 or src_h == 0:
            return ""
        height = max(1, int(width * src_h / src_w * 0.45))
        scaled = img.scaled(width, height)
        lines = []
        for row in range(scaled.height()):
            row_chars = []
            for col in range(scaled.width()):
                px = scaled.pixel(col, row)
                r = (px >> 16) & 0xFF
                g = (px >> 8)  & 0xFF
                b =  px        & 0xFF
                brightness = int(0.299*r + 0.587*g + 0.114*b)  # luminance
                idx = int(brightness / 255 * (len(_CHARS) - 1))
                row_chars.append(_CHARS[idx])
            lines.append("".join(row_chars))
        return "\n".join(lines)

    @staticmethod
    def _clipboard_image_path() -> Optional[str]:
        """If clipboard holds image data, save to a temp PNG and return its path."""
        cb = QApplication.clipboard()
        img = cb.image()
        if img.isNull():
            return None
        import tempfile
        tmp_dir = Path(tempfile.gettempdir()) / "aide_images"
        # Create with restrictive permissions — only the current user can read.
        tmp_dir.mkdir(exist_ok=True, mode=0o700)
        try:
            os.chmod(str(tmp_dir), 0o700)
        except OSError:
            pass
        # NamedTemporaryFile guarantees a unique, unpredictable filename.
        try:
            with tempfile.NamedTemporaryFile(
                dir=str(tmp_dir), suffix=".png", delete=False
            ) as f:
                path = f.name
            os.chmod(path, 0o600)
        except OSError:
            return None
        img.save(path, "PNG")
        return path

    def contextMenuEvent(self,event):
        # If there is an active selection, right-click copies it directly
        # (and clears the selection) instead of opening the context menu.
        if self._sel_start is not None:
            txt=self._sel_text()
            if txt:
                QApplication.clipboard().setText(txt)
                self._sel_start=None; self._sel_end=None; self.update()
                return
        cb = QApplication.clipboard()
        has_text  = bool(cb.text())
        has_image = not cb.image().isNull()
        has_files = cb.mimeData() is not None and cb.mimeData().hasUrls()

        menu=QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{C_SURFACE.name()};color:{C_FG.name()};"
            f"border:1px solid {C_MUTED.name()};padding:4px;}}"
            f"QMenu::item{{padding:4px 20px;}}"
            f"QMenu::item:selected{{background:{C_ACCENT.name()}44;color:{C_ACCENT.name()};}}"
            f"QMenu::item:disabled{{color:{C_MUTED.name()};}}")
        paste_act  = menu.addAction("Paste text")
        paste_act.setEnabled(has_text)
        file_act   = menu.addAction("📎  Paste files as paths")
        file_act.setEnabled(has_files)
        img_act    = menu.addAction("🖼  Paste image as file path")
        img_act.setEnabled(has_image)
        ascii_act  = menu.addAction("🎨  Paste image as ASCII art")
        ascii_act.setEnabled(has_image)
        menu.addSeparator()
        copy_act   = menu.addAction("Copy screen")
        act=menu.exec(event.globalPos())
        if act==paste_act:
            text = cb.text()
            if text and self.session:
                self.session.scroll_offset=0
                self.session.write(text.encode("utf-8"))
        elif act==file_act:
            paths = " ".join(
                shlex.quote(u.toLocalFile())
                for u in cb.mimeData().urls()
                if u.isLocalFile() and "\x00" not in u.toLocalFile()
            )
            if paths and self.session:
                self.session.scroll_offset=0
                self.session.write(paths.encode("utf-8"))
        elif act==img_act:
            path = self._clipboard_image_path()
            if path and self.session:
                self.session.scroll_offset=0
                self.session.write(shlex.quote(path).encode("utf-8"))
        elif act==ascii_act:
            art = self._image_to_ascii(cb.image())
            if art and self.session:
                self.session.scroll_offset=0
                self.session.write(art.encode("utf-8"))
        elif act==copy_act:
            if self.session: QApplication.clipboard().setText(self.session.screen_text())

    def focusNextPrevChild(self,next:bool)->bool: return False  # keep Tab inside terminal
    def focusInEvent(self,e): super().focusInEvent(e); self.update()
    def focusOutEvent(self,e): super().focusOutEvent(e); self.update()
    def sizeHint(self): return QSize(80*self._cw,24*self._ch)

    # ── file drag-and-drop ─────────────────────────────────────────────────────
    def dragEnterEvent(self, ev):
        """Accept any URL/file drag (e.g. images from Finder)."""
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        """Type the dropped file path(s) into the active shell, shell-quoted."""
        urls = ev.mimeData().urls()
        if not urls or not self.session:
            ev.ignore(); return
        paths = " ".join(
            shlex.quote(u.toLocalFile())
            for u in urls
            if u.isLocalFile() and "\x00" not in u.toLocalFile()
        )
        if paths:
            self.session.scroll_offset = 0
            self.session.write(paths.encode("utf-8"))
            self.setFocus()
            ev.acceptProposedAction()
        else:
            ev.ignore()

# ═════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════

_APP_ICONS={
    "claude":"🤖","claude-code":"🤖","anthropic":"🤖",
    "python":"🐍","python3":"🐍","ipython":"🐍",
    "node":"📦","npm":"📦","npx":"📦","yarn":"📦","bun":"📦",
    "git":"🌿","gh":"🌿",
    "docker":"🐳","docker-compose":"🐳","kubectl":"🐳",
    "vim":"✏️","nvim":"✏️","nano":"✏️","emacs":"✏️","hx":"✏️",
    "ssh":"🔐","sftp":"🔐","scp":"🔐",
    "cargo":"🦀","rustc":"🦀",
    "go":"🐹","gofmt":"🐹",
    "java":"☕","mvn":"☕","gradle":"☕",
    "psql":"🐘","mysql":"🐬","redis-cli":"🔴","mongo":"🍃",
    "htop":"📊","top":"📊","btop":"📊",
    "make":"⚙️","cmake":"⚙️",
    "aws":"☁️","gcloud":"☁️","az":"☁️",
}

def _app_icon(cmd:str)->str:
    if not cmd: return ""
    base=os.path.basename(cmd.split()[0]) if cmd.split() else ""
    return _APP_ICONS.get(base.lower(),"")


class TabCard(QFrame):
    clicked_signal=pyqtSignal(int)
    shift_clicked_signal=pyqtSignal(int)
    rename_requested=pyqtSignal(int)
    close_requested=pyqtSignal(int)
    reorder_requested=pyqtSignal(str,str,bool)  # src_encoded, target_encoded, place_before
    neural_toggle_requested=pyqtSignal(int)     # tab_id

    _MIME_TYPE="application/x-aide-tab"

    def __init__(self,session:TermSession,cfg:CardConfig,parent=None):
        super().__init__(parent)
        self.session=session; self.cfg=cfg; self._active=False
        self._unread=False; self._left_color=QColor("transparent")
        self._press_pos=None; self._drop_indicator=0  # -1 above, 0 none, 1 below
        self.setAcceptDrops(True)
        self._show_tag = True  # set False by TabBar when previous card has same tag
        self.setFixedHeight(62); self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Left 3 px is reserved for the status bar drawn in paintEvent; content starts at 5px
        lay=QVBoxLayout(self); lay.setContentsMargins(8,4,4,4); lay.setSpacing(1)
        title_row=QWidget(); title_row.setStyleSheet("background:transparent;")
        tr=QHBoxLayout(title_row); tr.setContentsMargins(0,0,0,0); tr.setSpacing(4)
        # Fixed-width icon slot so title text always starts at the same column
        self._icon_lbl=QLabel(); self._icon_lbl.setFixedWidth(18)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet("color:#8b949e;font-size:11px;background:transparent;")
        tr.addWidget(self._icon_lbl)
        self._lbl0=QLabel(); self._lbl0.setStyleSheet(f"color:{C_FG.name()};font-size:12px;")
        self._lbl0.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        self._lbl0.setMinimumWidth(0); self._lbl0.setWordWrap(False); tr.addWidget(self._lbl0,1)
        # Unread dot — orange ● shown when tab is marked unread
        self._unread_dot=QLabel("●"); self._unread_dot.setFixedSize(12,12)
        self._unread_dot.setStyleSheet("color:#f0a500;font-size:8px;background:transparent;")
        self._unread_dot.setVisible(False); tr.addWidget(self._unread_dot)
        # No-command warning triangle — red ▲ when no autostart_cmd is set
        self._no_cmd_tri=QLabel("▲"); self._no_cmd_tri.setFixedSize(10,10)
        self._no_cmd_tri.setStyleSheet("color:#e05c00;font-size:7px;background:transparent;")
        self._no_cmd_tri.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_cmd_tri.setToolTip("No startup command configured")
        self._no_cmd_tri.setVisible(False); tr.addWidget(self._no_cmd_tri)
        # Task count badge — shown when the notes panel has tasks
        self._task_badge=QLabel(); self._task_badge.setFixedHeight(14)
        self._task_badge.setStyleSheet("color:#e6edf3;background:#1f6feb;border-radius:6px;font-size:9px;padding:0 4px;font-weight:bold;")
        self._task_badge.setVisible(False); tr.addWidget(self._task_badge)
        close_btn=QPushButton("✕"); close_btn.setFixedSize(16,16)
        close_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{C_MUTED.name()};border:none;font-size:10px;padding:0;}}QPushButton:hover{{color:#ff6b6b;background:{C_SURFACE.name()};border-radius:3px;}}")
        close_btn.setToolTip("Close terminal"); close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(lambda: self.close_requested.emit(self.session.tab_id))
        tr.addWidget(close_btn); lay.addWidget(title_row)
        self._lbl1=QLabel(); self._lbl1.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        self._lbl2=QLabel(); self._lbl2.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        self._lbl3=QLabel(); self._lbl3.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        for lbl in (self._lbl1,self._lbl2,self._lbl3):
            lbl.setMaximumWidth(190); lbl.setWordWrap(False); lay.addWidget(lbl)
        self.refresh()

    _GEAR_FRAMES=("◐","◓","◑","◒")  # quarter-circle rotation gives spinning-gear illusion

    def refresh(self):
        s=self.session; i=s.info
        thinking = getattr(s,"claude_thinking",False)
        working  = getattr(s,"claude_working",False)
        waiting  = getattr(s,"waiting_input",False)
        # No-command warning triangle
        self._no_cmd_tri.setVisible(not bool(s.autostart_cmd))
        # Task count badge
        task_count = len([l for l in s.tasks.splitlines() if l.strip()]) if s.tasks else 0
        if task_count>0:
            self._task_badge.setText(str(task_count)); self._task_badge.setVisible(True)
        else:
            self._task_badge.setVisible(False)
        # Unread dot
        self._unread_dot.setVisible(self._unread)
        # Icon: spinning gear while working/thinking, blinking ? while waiting, blank otherwise
        if thinking or working:
            frame = self._GEAR_FRAMES[getattr(self,"_gear_tick",0) % len(self._GEAR_FRAMES)]
            self._icon_lbl.setText(f"{frame}⚙")
            self._icon_lbl.setStyleSheet(f"color:{C_ACCENT.name()};font-size:11px;background:transparent;")
        elif waiting:
            self._icon_lbl.setText("?" if getattr(self,"_blink_phase",False) else " ")
            self._icon_lbl.setStyleSheet(f"color:{C_ACCENT.name()};font-size:14px;font-weight:bold;background:transparent;")
        else:
            self._icon_lbl.setText("")
            self._icon_lbl.setStyleSheet("background:transparent;")
        # Title label: tags (accent, optional) + title text
        _acc = C_ACCENT.name()
        show_tags = getattr(self.cfg, "show_tags", True)
        tags_html = ""
        if show_tags and s.tags:
            if self._show_tag:
                tags_html = "".join(f'<span style="color:{_acc};font-size:10px">[{t}]</span>' for t in s.tags) + " "
            else:
                # Render tag as transparent placeholder to preserve alignment
                tags_html = "".join(f'<span style="color:transparent;font-size:10px">[{t}]</span>' for t in s.tags) + " "
        plain = s.effective_title()
        fg_col = C_FG.name() if waiting else C_MUTED.name()
        if tags_html or waiting:
            # Use rich-text so we can bold the title inline — stylesheet
            # font-weight is not reliably applied by Qt's HTML renderer.
            title_part = (f'<b><span style="color:{fg_col}">{plain}</span></b>'
                          if waiting else f'<span style="color:{fg_col}">{plain}</span>')
            self._lbl0.setText(f"{tags_html}{title_part}")
        else:
            self._lbl0.setText(plain)
        # Format last-ping time as relative string
        _ping_str=""
        if s.last_ping_time>0:
            import datetime
            delta=int(time.time()-s.last_ping_time)
            if delta<60:     _ping_str=f"🕐 {delta}s ago"
            elif delta<3600: _ping_str=f"🕐 {delta//60}m ago"
            else:            _ping_str=f"🕐 {datetime.datetime.fromtimestamp(s.last_ping_time).strftime('%H:%M')}"
        _map={"cwd":("📁",i.cwd),"cmd":("$",i.last_cmd[:24] if i.last_cmd else ""),
              "ssh":("⬡",i.ssh_host),"process":("⚙",i.process),"ping":("",_ping_str)}
        extra=[f for f in self.cfg.fields if f!="title"]
        # When agent is active, override the first info row with a status line
        if thinking or working:
            blink_on=getattr(self,"_blink_phase",False)
            status_text="💭 Agent thinking…" if thinking else "⚙ Agent working…"
            status_color="#a5d6ff" if thinking else "#f0a500"
            visible_text=status_text if blink_on else status_text.replace("…","   ")
            self._lbl1.setText(visible_text)
            self._lbl1.setStyleSheet(f"color:{status_color};font-size:11px;font-weight:bold;")
            self._lbl1.setVisible(True); self._lbl1.setToolTip("")
            for n,lbl in enumerate((self._lbl2,self._lbl3)):
                if n<len(extra):
                    field=extra[n]; icon2,val=_map.get(field,("",""))
                    lbl.setText(f"{icon2} {val}" if val else ""); lbl.setVisible(bool(val))
                    lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
                else: lbl.setVisible(False)
        else:
            for n,lbl in enumerate((self._lbl1,self._lbl2,self._lbl3)):
                lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
                if n<len(extra):
                    field=extra[n]; icon2,val=_map.get(field,("",""))
                    lbl.setText(f"{icon2} {val}" if val else ""); lbl.setVisible(bool(val))
                    if field=="cwd":
                        full=i.cwd_full or i.cwd
                        lbl.setToolTip(full.replace("~",str(Path.home())) if full.startswith("~") else full)
                    else: lbl.setToolTip("")
                else: lbl.setVisible(False); lbl.setToolTip("")
        # Shrink card to fit visible rows
        visible_rows = sum(1 for lbl in (self._lbl1,self._lbl2,self._lbl3) if lbl.isVisible())
        self.setFixedHeight(32 + visible_rows * 15)
        self._apply_style()

    def mark_active(self, a: bool):
        self._active = a; self._apply_style()

    def mark_visible(self, v: bool):
        """Mark this card as visible in a split pane (secondary focus)."""
        if getattr(self, "_visible", False) == v: return
        self._visible = v; self._apply_style()

    def mark_kbd_focus(self, focused: bool):
        if getattr(self, "_kbd_focus", False) == focused: return
        self._kbd_focus = focused; self._apply_style()

    def _apply_style(self):
        kbd      = getattr(self, "_kbd_focus", False)
        visible  = getattr(self, "_visible",   False)   # shown in secondary split pane
        waiting  = getattr(self.session, "waiting_input",   False)
        blink_on = getattr(self, "_blink_phase", False)
        # Title label: color + font-weight via stylesheet (plain-text mode).
        # Rich-text mode (tags or waiting) is bolded via inline HTML in refresh().
        fg = C_FG.name() if waiting else C_MUTED.name()
        self._lbl0.setStyleSheet(
            f"QLabel{{color:{fg};font-size:12px;font-weight:{'700' if waiting else '400'};"
            f"background:transparent;}}")
        # Left accent bar — drawn in paintEvent to avoid QFrame CSS border artifacts
        if waiting:
            self._left_color = C_ACCENT          # blue bar for any waiting card
        elif self._active or visible:
            self._left_color = C_ACCENT
        elif self._unread:
            self._left_color = QColor("#e05c00")
        elif kbd:
            self._left_color = C_MUTED
        else:
            self._left_color = QColor("transparent")
        if waiting and not self._active:
            bg = "#1a2533"                        # blue-tinted bg when waiting but not focused
        elif self._active:
            bg = "#1f2d3d"
        elif visible or kbd:
            bg = C_SURFACE.name()
        else:
            bg = C_PANEL.name()
        hover = "" if (self._active or visible or kbd or waiting) else f"QFrame:hover{{background:{C_SURFACE.name()};}}"
        self.setStyleSheet(f"QFrame{{background:{bg};border:none;}}{hover}")
        self.update()

    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton:
            self._press_pos=e.pos()
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.shift_clicked_signal.emit(self.session.tab_id)
            else:
                self.clicked_signal.emit(self.session.tab_id)

    def contextMenuEvent(self,e):
        menu=QMenu(self)
        if self._unread:
            act=menu.addAction("Clear Unread")
            act.triggered.connect(self._clear_unread)
        else:
            act=menu.addAction("Mark as Unread")
            act.triggered.connect(self._mark_unread)
        menu.addSeparator()
        menu.addAction("Edit Tags…").triggered.connect(self._edit_tags)
        menu.addSeparator()
        neural_label = "🧠 Leave Neural Bus" if self.session.neural_on_bus else "🧠 Join Neural Bus"
        menu.addAction(neural_label).triggered.connect(
            lambda: self.neural_toggle_requested.emit(self.session.tab_id))
        menu.exec(e.globalPos())

    def _mark_unread(self):
        self._unread=True
        self._apply_style()
        self._unread_dot.setVisible(True)

    def _clear_unread(self):
        self._unread=False
        self._apply_style()
        self._unread_dot.setVisible(False)

    def _edit_tags(self):
        from PyQt6.QtWidgets import QInputDialog
        current = ", ".join(self.session.tags)
        text, ok = QInputDialog.getText(self, "Edit Tags", "Tags (comma-separated):", text=current)
        if not ok: return
        self.session.tags = [t.strip() for t in text.split(",") if t.strip()]
        self.refresh()

    def mouseDoubleClickEvent(self,e): self.rename_requested.emit(self.session.tab_id)

    def mouseMoveEvent(self,e):
        if not (e.buttons() & Qt.MouseButton.LeftButton): return
        if self._press_pos is None: return
        if (e.pos()-self._press_pos).manhattanLength()<max(QApplication.startDragDistance(),4): return
        pos=self._press_pos; self._press_pos=None  # clear before exec to avoid re-entry
        drag=QDrag(self); mime=QMimeData()
        mime.setData(self._MIME_TYPE,f"t:{self.session.tab_id}".encode())
        drag.setMimeData(mime)
        pm=self.grab(); drag.setPixmap(pm); drag.setHotSpot(pos)
        drag.exec(Qt.DropAction.MoveAction|Qt.DropAction.CopyAction)

    def dragEnterEvent(self,ev):
        md=ev.mimeData()
        if md.hasFormat(self._MIME_TYPE):
            src=bytes(md.data(self._MIME_TYPE)).decode()
            if src != f"t:{self.session.tab_id}":
                ev.acceptProposedAction(); return
        ev.ignore()

    def dragMoveEvent(self,ev):
        if not ev.mimeData().hasFormat(self._MIME_TYPE):
            ev.ignore(); return
        above=ev.position().y()<self.height()/2
        new=-1 if above else 1
        if new!=self._drop_indicator:
            self._drop_indicator=new; self.update()
        ev.acceptProposedAction()

    def dragLeaveEvent(self,ev):
        if self._drop_indicator!=0:
            self._drop_indicator=0; self.update()

    def dropEvent(self,ev):
        md=ev.mimeData()
        if not md.hasFormat(self._MIME_TYPE):
            ev.ignore(); return
        src=bytes(md.data(self._MIME_TYPE)).decode()
        above=ev.position().y()<self.height()/2
        self._drop_indicator=0; self.update()
        tgt=f"t:{self.session.tab_id}"
        if src and src != tgt:
            self.reorder_requested.emit(src, tgt, above)
        ev.acceptProposedAction()

    def paintEvent(self,ev):
        super().paintEvent(ev)
        p=QPainter(self)
        # Left status bar (3 px)
        if self._left_color.alpha()>0:
            p.fillRect(0, 0, 3, self.height(), self._left_color)
        # Drop indicator line
        if self._drop_indicator!=0:
            p.setPen(QPen(C_ACCENT,2))
            y=0 if self._drop_indicator<0 else self.height()-1
            p.drawLine(0,y,self.width(),y)
        p.end()





class NeuralBrainCard(QFrame):
    """Pinned card at the bottom of the terminal list — opens the shared brain editor."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame{{background:{C_BG.name()};"
            f"border-top:2px solid {C_ACCENT.name()}44;}}"
            f"QFrame:hover{{background:{C_ACCENT.name()}18;}}")
        lay = QVBoxLayout(self); lay.setContentsMargins(10, 6, 8, 6); lay.setSpacing(1)
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        icon = QLabel("🧠"); icon.setStyleSheet("font-size:14px;background:transparent;")
        title = QLabel("Neural Brain")
        title.setStyleSheet(
            f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;background:transparent;")
        hdr.addWidget(icon); hdr.addWidget(title); hdr.addStretch()
        lay.addLayout(hdr)
        self._sub = QLabel("Shared memory & instructions for all agents")
        self._sub.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;")
        lay.addWidget(self._sub)

    def update_preview(self, content: str):
        chars = len(content)
        lines = content.count("\n") + 1 if content.strip() else 0
        self._sub.setText(
            f"{lines} line{'s' if lines != 1 else ''}  ·  {chars} chars"
            if chars else "Click to add shared memory & instructions")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class NeuralBrainDialog(QDialog):
    """Editor for the shared neural brain file."""
    def __init__(self, content: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🧠  Neural Brain — Shared Memory")
        self.setStyleSheet(_dlg_ss())
        self.setMinimumSize(640, 520)
        self._saved_content = content
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        hdr = QLabel("  🧠  Neural Brain — Shared Memory & Instructions")
        hdr.setFixedHeight(42)
        hdr.setStyleSheet(
            f"background:{C_ACCENT.name()};color:#000;"
            f"font-weight:bold;font-size:14px;padding:0 16px;")
        lay.addWidget(hdr)

        info_txt = (
            f"  Available to all agents as  $AIDE_NEURAL_BRAIN_FILE  "
            f"and via  neural brain\n"
            f"  Read it with:  cat \"$AIDE_NEURAL_BRAIN_FILE\"")
        info = QLabel(info_txt)
        info.setStyleSheet(
            f"color:{C_MUTED.name()};font-size:10px;"
            f"background:{C_BG.name()};padding:5px 12px;")
        lay.addWidget(info)

        self._editor = QTextEdit()
        self._editor.setPlainText(content)
        self._editor.setStyleSheet(
            f"QTextEdit{{background:{C_BG.name()};color:{C_FG.name()};"
            f"font-family:{FONT_FAMILY};font-size:12px;border:none;padding:12px;}}")
        lay.addWidget(self._editor, 1)

        foot = QWidget(); fl = QHBoxLayout(foot); fl.setContentsMargins(12, 8, 12, 12)
        cancel_btn = QPushButton("Cancel"); cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save"); _primary_btn(save_btn); save_btn.clicked.connect(self._save)
        fl.addStretch(); fl.addWidget(cancel_btn); fl.addWidget(save_btn)
        lay.addWidget(foot)

    def _save(self):
        content = self._editor.toPlainText()
        try: NEURAL_BRAIN_FILE.write_text(content, encoding="utf-8")
        except Exception: pass
        self._saved_content = content
        self.accept()

    def get_content(self) -> str:
        return self._saved_content


class TabBar(QWidget):
    tab_selected            = pyqtSignal(int)
    shift_tab_selected      = pyqtSignal(int)
    new_tab_clicked         = pyqtSignal()
    rename_requested        = pyqtSignal(int)
    close_requested         = pyqtSignal(int)
    tabs_reordered          = pyqtSignal(list)
    neural_toggle_requested = pyqtSignal(int)
    brain_clicked           = pyqtSignal()

    SORT_RECENT = "recent"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(140)
        self.setStyleSheet(f"background:{C_PANEL.name()};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        ml = QVBoxLayout(self); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{border:none;}QScrollBar:vertical{width:4px;}QScrollBar::handle:vertical{background:#444;border-radius:2px;}")
        # Filter bar (unread filter + recent sort only; tag pills removed)
        self._filter_bar = QWidget(); self._filter_bar.setFixedHeight(26)
        self._filter_bar.setStyleSheet(f"background:{C_SURFACE.name()};")
        fb_lay = QHBoxLayout(self._filter_bar); fb_lay.setContentsMargins(6,0,4,0); fb_lay.setSpacing(0)
        self._unread_filter_btn = QPushButton("● Unread"); self._unread_filter_btn.setCheckable(True)
        self._unread_filter_btn.setFixedHeight(20)
        _pill_css = (
            f"QPushButton{{background:transparent;color:{C_MUTED.name()};border:1px solid transparent;"
            f"border-radius:3px;font-size:10px;padding:0 5px;}}"
            f"QPushButton:hover{{color:{C_FG.name()};}}"
            f"QPushButton:checked{{background:{C_ACCENT.name()}33;color:{C_ACCENT.name()};border-color:{C_ACCENT.name()};}}"
        )
        self._unread_filter_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_MUTED.name()};border:1px solid transparent;"
            f"border-radius:3px;font-size:10px;padding:0 6px;}}"
            f"QPushButton:hover{{color:{C_FG.name()};}}"
            f"QPushButton:checked{{background:#e05c0033;color:#e05c00;border-color:#e05c00;}}"
        )
        self._unread_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unread_filter_btn.toggled.connect(self._on_unread_filter_toggled)
        fb_lay.addWidget(self._unread_filter_btn)
        fb_lay.addStretch()
        self._sort_recent_btn = QPushButton("⏱"); self._sort_recent_btn.setCheckable(True)
        self._sort_recent_btn.setFixedHeight(20); self._sort_recent_btn.setStyleSheet(_pill_css)
        self._sort_recent_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sort_recent_btn.setToolTip("Sort by last question from Claude")
        self._sort_recent_btn.clicked.connect(lambda: self._set_sort(self.SORT_RECENT))
        fb_lay.addWidget(self._sort_recent_btn)
        ml.addWidget(self._filter_bar)
        self._cw = QWidget(); self._cw.setStyleSheet(f"background:{C_PANEL.name()};")
        self._cw.setAcceptDrops(True)
        self._cl = QVBoxLayout(self._cw); self._cl.setContentsMargins(0,0,0,0); self._cl.setSpacing(0); self._cl.addStretch()
        self._scroll.setWidget(self._cw); ml.addWidget(self._scroll, 1)
        # Neural Brain card — always visible, pinned above the action buttons
        self._brain_card = NeuralBrainCard()
        self._brain_card.clicked.connect(self.brain_clicked)
        ml.addWidget(self._brain_card)
        # New Terminal button
        _new_btn_ss = (f"QPushButton{{background:{C_SURFACE.name()};color:{C_MUTED.name()};"
                       f"border:none;font-size:12px;text-align:left;padding-left:12px;}}"
                       f"QPushButton:hover{{background:{C_ACCENT.name()}22;color:{C_ACCENT.name()};}}")
        btn_row = QWidget(); btn_row.setFixedHeight(34)
        btn_row.setStyleSheet(f"background:{C_SURFACE.name()};")
        br = QHBoxLayout(btn_row); br.setContentsMargins(0,0,0,0); br.setSpacing(0)
        self._btn_new_term = QPushButton("  +  Terminal"); self._btn_new_term.setStyleSheet(_new_btn_ss)
        self._btn_new_term.clicked.connect(self.new_tab_clicked)
        br.addWidget(self._btn_new_term, 1)
        ml.addWidget(btn_row)
        # Dashboard footer
        self._dash_footer = QWidget(); self._dash_footer.setFixedHeight(28)
        self._dash_footer.setStyleSheet(f"background:{C_SURFACE.name()};border-top:1px solid #21262d;")
        df = QHBoxLayout(self._dash_footer); df.setContentsMargins(8,0,6,0); df.setSpacing(4)
        self._dash_lbl = QLabel("📱"); self._dash_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;")
        self._dash_url = QLabel("—"); self._dash_url.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;")
        self._dash_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._dash_copy = QPushButton("Copy"); self._dash_copy.setFixedHeight(18)
        self._dash_copy.setStyleSheet(f"QPushButton{{background:transparent;color:{C_MUTED.name()};border:1px solid #30363d;border-radius:3px;font-size:9px;padding:0 5px;}}QPushButton:hover{{color:{C_FG.name()};border-color:{C_ACCENT.name()};}}")
        self._dash_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dash_copy.clicked.connect(self._copy_dash_url)
        df.addWidget(self._dash_lbl); df.addWidget(self._dash_url, 1); df.addWidget(self._dash_copy)
        ml.addWidget(self._dash_footer)
        self._card_map: Dict[int, TabCard] = {}
        self._sessions: dict = {}
        self._order: list = []     # [tab_id, ...]
        self._kbd_idx: int = -1
        self._unread_filter: bool = False
        self._sort_mode: str = ""  # "" = manual order, SORT_RECENT
        # Neural rail overlay — covers the full TabBar (viewport + brain card)
        self._rail = NeuralRailOverlay(self)
        self._rail._brain_card = self._brain_card
        self.installEventFilter(self)

    def update_brain_preview(self, content: str):
        self._brain_card.update_preview(content)

    def eventFilter(self, obj, event):
        if obj is self and event.type() == QEvent.Type.Resize:
            self._rail.resize(self.size())
            self._rail.raise_()
        return super().eventFilter(obj, event)

    def animate_neural_rail(self, from_sid: int, to_sid: int):
        """Animate a packet on the neural rail from sender card to receiver card."""
        fc = self._card_map.get(from_sid)
        tc = self._card_map.get(to_sid)
        if not fc or not tc: return
        fy = fc.mapTo(self, QPoint(0, fc.height() // 2)).y()
        ty = tc.mapTo(self, QPoint(0, tc.height() // 2)).y()
        self._rail.start_animation(fy, ty)

    def set_dashboard_url(self, url: str):
        self._dash_url.setText(url)
        self._dash_url.setToolTip(url)

    def _copy_dash_url(self):
        QApplication.clipboard().setText(self._dash_url.text())
        self._dash_copy.setText("✓")
        QTimer.singleShot(1500, lambda: self._dash_copy.setText("Copy"))

    def _on_unread_filter_toggled(self, checked: bool):
        self._unread_filter = checked
        self.rebuild_layout(self._sessions)

    def _set_sort(self, mode: str):
        if self._sort_mode == mode:
            mode = ""
        self._sort_mode = mode
        self._sort_recent_btn.setChecked(mode == self.SORT_RECENT)
        self.rebuild_layout(self._sessions)

    def rebuild_layout(self, sessions: dict):
        self._sessions = sessions

        # Detach all widgets from layout (keep alive)
        while self._cl.count() > 1:
            item = self._cl.takeAt(0)
            w = item.widget()
            if w: w.setParent(None)

        # Determine ordered items
        if self._sort_mode == self.SORT_RECENT:
            ordered = sorted(self._order, key=lambda tid: getattr(
                self._sessions.get(tid), 'last_waiting_at', 0.0), reverse=True)
        else:
            ordered = self._order

        for tid in ordered:
            card = self._card_map.get(tid)
            if not card: continue
            passes_unread = not self._unread_filter or card._unread
            self._cl.insertWidget(self._cl.count()-1, card)
            card.setVisible(passes_unread)
            if passes_unread: card.refresh()

        self._rail.set_cards(list(self._card_map.values()))

    # ── card management ────────────────────────────────────────────────────────
    def add_card(self, s: TermSession, cfg: CardConfig) -> "TabCard":
        card = TabCard(s, cfg)
        card.clicked_signal.connect(self._on_card_clicked)
        card.shift_clicked_signal.connect(self.shift_tab_selected)
        card.rename_requested.connect(self.rename_requested)
        card.close_requested.connect(self.close_requested)
        card.reorder_requested.connect(self._handle_reorder)
        card.neural_toggle_requested.connect(self.neural_toggle_requested)
        self._card_map[s.tab_id] = card
        self._sessions[s.tab_id] = s
        if s.tab_id not in self._order:
            self._order.append(s.tab_id)
        self.rebuild_layout(self._sessions)
        return card

    def remove_card(self, tid: int):
        if card := self._card_map.pop(tid, None):
            card.deleteLater()
        self._sessions.pop(tid, None)
        self._order = [t for t in self._order if t != tid]
        self.rebuild_layout(self._sessions)

    def get_full_order(self) -> list:
        return [f"t:{tid}" for tid in self._order]

    def set_full_order(self, encoded: list):
        new_order = []
        for s in encoded:
            parts = s.split(":", 1)
            if len(parts) != 2: continue
            typ, oid_str = parts
            if typ != "t": continue   # skip old separator entries
            try: oid = int(oid_str)
            except ValueError: continue
            if oid in self._card_map and oid not in new_order:
                new_order.append(oid)
        for tid in self._order:
            if tid not in new_order:
                new_order.append(tid)
        self._order = new_order

    def set_active(self, tid: int, secondary_tid: int = -1):
        for t, c in self._card_map.items():
            c.mark_active(t == tid)
            c.mark_visible(t == secondary_tid)

    # ── keyboard navigation ────────────────────────────────────────────────────
    def _cards(self) -> list:
        out = []
        for i in range(self._cl.count()):
            item = self._cl.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, TabCard): out.append(w)
        return out

    def _set_kbd_focus(self, idx: int):
        cards = self._cards()
        if not cards: return
        idx = max(0, min(idx, len(cards)-1))
        if 0 <= self._kbd_idx < len(cards):
            cards[self._kbd_idx].mark_kbd_focus(False)
        self._kbd_idx = idx
        cards[idx].mark_kbd_focus(True)
        self._scroll.ensureWidgetVisible(cards[idx])

    def _clear_kbd_focus(self):
        for c in self._cards(): c.mark_kbd_focus(False)
        self._kbd_idx = -1

    def _on_card_clicked(self, tid: int):
        cards = self._cards()
        for i, c in enumerate(cards):
            if c.session.tab_id == tid:
                self._kbd_idx = i; break
        self.tab_selected.emit(tid)

    def mousePressEvent(self, e):
        self.setFocus(); super().mousePressEvent(e)

    def focusOutEvent(self, e):
        self._clear_kbd_focus(); super().focusOutEvent(e)

    def keyPressEvent(self, e):
        K = Qt.Key; cards = self._cards()
        if not cards: super().keyPressEvent(e); return
        if e.key() == K.Key_Down:
            self._set_kbd_focus(0 if self._kbd_idx < 0 else self._kbd_idx + 1)
        elif e.key() == K.Key_Up:
            self._set_kbd_focus(len(cards)-1 if self._kbd_idx < 0 else self._kbd_idx - 1)
        elif e.key() in (K.Key_Return, K.Key_Enter):
            if 0 <= self._kbd_idx < len(cards):
                self.tab_selected.emit(cards[self._kbd_idx].session.tab_id)
        elif e.key() == K.Key_Escape:
            self._clear_kbd_focus()
        else:
            super().keyPressEvent(e)

    def refresh_card(self, tid: int, force: bool = False):
        c = self._card_map.get(tid)
        if not c: return
        s = c.session
        state = (s.claude_thinking, s.claude_working, s.waiting_input,
                 c._blink_phase if s.waiting_input else False,
                 c._gear_tick if (s.claude_thinking or s.claude_working) else 0,
                 s.info.cwd, s.info.last_cmd,
                 s.custom_title, c._unread, c._active, s.neural_on_bus)
        if not force and getattr(c, "_last_state", None) == state: return
        c._last_state = state
        c.refresh()

    def _handle_reorder(self, src_enc: str, tgt_enc: str, place_before: bool):
        def _parse(s):
            parts = s.split(":", 1)
            if len(parts) != 2: return None
            typ, oid_str = parts
            if typ != "t": return None
            try: return int(oid_str)
            except ValueError: return None
        src = _parse(src_enc); tgt = _parse(tgt_enc)
        if src is None or tgt is None or src == tgt: return
        if src not in self._order or tgt not in self._order: return
        self._order.remove(src)
        idx = self._order.index(tgt)
        self._order.insert(idx if place_before else idx + 1, src)
        QTimer.singleShot(0, lambda: self.rebuild_layout(self._sessions))
        self.tabs_reordered.emit(list(self._order))

# ═════════════════════════════════════════════════════════════════════════════
# TOP BAR / NOTIFICATION BANNER / HOTKEY BAR
# ═════════════════════════════════════════════════════════════════════════════

class AIInfoBar(QLabel):
    def __init__(self,parent=None):
        super().__init__(parent); self.setFixedHeight(28)
        self.setStyleSheet(f"background:{C_BG.name()};color:{C_FG.name()};padding:0 10px;font-size:12px;")
        self._refresh(); t=QTimer(self); t.timeout.connect(self._refresh); t.start(30000)

    def _refresh(self):
        providers=detect_ai_providers()
        # No providers: hide the bar entirely so we don't waste a line on a
        # plain "AIDE" label (the title bar already shows the app name).
        if not providers:
            self.setText(""); self.setVisible(False); return
        parts=["  ⚡  "]
        for i,p in enumerate(providers):
            if i: parts.append("  <span style=\"color:#444\">│</span>  ")
            parts.append(f"<b style=\"color:{p.color}\">{p.name}</b> <span style=\"color:#555\">{p.model}</span> <span style=\"color:#444\">[{p.account}]</span>")
        self.setText("".join(parts)); self.setVisible(True)


class NotifBanner(QLabel):
    def __init__(self,parent=None):
        super().__init__(parent); self.setFixedHeight(28)
        self.setStyleSheet(f"QLabel{{background:{C_WARN.name()};color:#0d1117;font-weight:bold;font-size:12px;padding:0 12px;}}")
        self.setVisible(False)
        self._t=QTimer(self); self._t.setSingleShot(True); self._t.timeout.connect(lambda: self.setVisible(False))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._click_cb=None

    def show_msg(self,msg:str,secs:int=6,on_click=None):
        self._click_cb=on_click
        label="click to restart" if on_click else "click to dismiss"
        self.setText(f"⚠  {msg}  [{label}]"); self.setVisible(True)
        if secs>0: self._t.start(secs*1000)

    def mousePressEvent(self,e):
        if self._click_cb: self._click_cb(); return
        self.setVisible(False); self._t.stop()


class _HotBtn(QWidget):
    """Two-line ribbon button: action name on top, shortcut below."""
    def __init__(self,icon:str,label:str,action:str,shortcut:str,on_click,parent=None):
        super().__init__(parent)
        self._on_click=on_click; self._active=False; self._hovered=False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay=QVBoxLayout(self); lay.setContentsMargins(8,4,8,4); lay.setSpacing(1)
        self._top=QLabel(f"{icon} {label}"); self._top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub=QLabel(shortcut); self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub.setStyleSheet(f"color:{C_MUTED.name()};font-size:9px;background:transparent;")
        lay.addWidget(self._top); lay.addWidget(self._sub)
        self._repaint()
    def set_active(self,on:bool):
        self._active=on; self._repaint()
    def _repaint(self):
        if self._active:
            bg=C_ACCENT.name()+"66" if self._hovered else C_ACCENT.name()+"33"
            self.setStyleSheet(f"background:{bg};border-radius:3px;border-bottom:2px solid {C_ACCENT.name()};")
            self._top.setStyleSheet(f"color:{C_ACCENT.name()};font-size:11px;font-weight:bold;background:transparent;")
        elif self._hovered:
            self.setStyleSheet(f"background:{C_ACCENT.name()}44;border-radius:3px;")
            self._top.setStyleSheet(f"color:{C_ACCENT.name()};font-size:11px;background:transparent;")
        else:
            self.setStyleSheet(f"background:{C_SURFACE.name()};border-radius:3px;")
            self._top.setStyleSheet(f"color:{C_FG.name()};font-size:11px;background:transparent;")
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton: self._on_click()
    def enterEvent(self,e): self._hovered=True; self._repaint()
    def leaveEvent(self,e): self._hovered=False; self._repaint()


class HotkeyBar(QWidget):
    """Ribbon toolbar shown at the top of the window."""
    action_triggered  = pyqtSignal(str)
    font_size_changed = pyqtSignal(int)
    restart_requested = pyqtSignal()

    _BUTTONS=[
        ("◀","Prev","prev_tab","Ctrl+Shift+Tab"),
        ("▶","Next","next_tab","Ctrl+Tab"),
        ("⊟","Split","split_term","^B-|"),
        ("⊞","Dashboard","toggle_dashboard","^B-d"),
        ("📝","SideBar","toggle_notes","^B-p"),
        ("🔑","API Keys","open_settings","^B-k"),
        ("🐙","GitHub","github_tokens","^B-g"),
        ("🔔","Notifs","configure_notifs","^B-s"),
        ("🚀","Uber","toggle_uber","^B-u"),
    ]

    def __init__(self,parent=None):
        super().__init__(parent); self.setFixedHeight(50)
        self.setStyleSheet(f"background:{C_PANEL.name()};border-bottom:1px solid {C_SURFACE.name()};")
        lay=QHBoxLayout(self); lay.setContentsMargins(6,4,6,4); lay.setSpacing(2)
        self._btn_map: Dict[str,_HotBtn] = {}
        for icon,label,action,shortcut in self._BUTTONS:
            btn=_HotBtn(icon,label,action,shortcut,lambda a=action: self.action_triggered.emit(a))
            btn.setToolTip(f"{label}  ({shortcut})")
            self._btn_map[action]=btn
            lay.addWidget(btn)
        # ── font-size ± buttons — left side, next to the ribbon ──────────────
        self._cur_font=FONT_SIZE
        _fs=f"QPushButton{{background:{C_SURFACE.name()};color:{C_FG.name()};font-weight:bold;"\
            f"font-size:13px;border:none;border-radius:3px;padding:0 6px;min-width:28px;}}"\
            f"QPushButton:hover{{background:{C_ACCENT.name()}44;color:{C_ACCENT.name()};}}"
        self._btn_font_dec=QPushButton("A-"); self._btn_font_dec.setFixedHeight(32)
        self._btn_font_dec.setStyleSheet(_fs); self._btn_font_dec.setToolTip("Decrease font size")
        self._btn_font_dec.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_font_dec.clicked.connect(lambda: self._bump_font(-1))
        self._btn_font_inc=QPushButton("A+"); self._btn_font_inc.setFixedHeight(32)
        self._btn_font_inc.setStyleSheet(_fs); self._btn_font_inc.setToolTip("Increase font size")
        self._btn_font_inc.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_font_inc.clicked.connect(lambda: self._bump_font(+1))
        lay.addWidget(self._btn_font_dec); lay.addWidget(self._btn_font_inc)
        lay.addStretch()
        self._info=QLabel(f"  {APP_NAME} v{VERSION}")
        self._info.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;background:transparent;border:none;")
        lay.addWidget(self._info)
        # ── update button (hidden until GitHub has a newer version) ──────────
        self._update_btn=QPushButton("↻  Update")
        self._update_btn.setFixedHeight(32)
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.setVisible(False)
        self._update_btn.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;font-weight:bold;font-size:11px;"
            "border:none;border-radius:3px;padding:0 10px;}"
            "QPushButton:hover{background:#388bfd;color:#fff;}")
        self._update_btn.setToolTip("New version available on GitHub — click to download & restart")
        self._update_btn.clicked.connect(self.restart_requested)
        lay.addWidget(self._update_btn)

    def _bump_font(self,delta:int):
        self._cur_font=max(8,min(24,self._cur_font+delta))
        self.font_size_changed.emit(self._cur_font)

    def update_info(self,text:str): self._info.setText(f"  {text}")

    def set_btn_active(self,action:str,on:bool):
        if btn:=self._btn_map.get(action): btn.set_active(on)

    def mark_update_available(self, on: bool, remote_ver: str = ""):
        self._update_btn.setVisible(on)
        if on and remote_ver:
            self._update_btn.setText(f"↻  v{remote_ver}")
            self._update_btn.setToolTip(f"AIDE v{remote_ver} available on GitHub — click to download & restart")
            self._info.setText(f"  v{VERSION} → v{remote_ver}")
            self._info.setStyleSheet(f"color:{C_ACCENT.name()};font-size:11px;background:transparent;border:none;font-weight:bold;")
        elif not on:
            self._info.setText(f"  {APP_NAME} v{VERSION}")
            self._info.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;background:transparent;border:none;")

# ═════════════════════════════════════════════════════════════════════════════
# NOTES PANEL  &  BROWSE PANE
# ═════════════════════════════════════════════════════════════════════════════

class _ColoredTextEdit(QTextEdit):
    """QTextEdit that locks every character to a single foreground color.

    QTextEdit's stylesheet `color` is only the *default*; once any operation
    injects a character format (paste, hitting Return after a styled span,
    etc.) Qt will keep using that format and the text turns black. This
    subclass forces our color back into the cursor's char format on every
    keystroke and on paste, and disables rich-text input entirely so the
    palette default is used for plain typing.
    """
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._text_color = color
        self.setAcceptRichText(False)
        # Palette: drives the default character format used by typed text.
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Text, color)
        muted = QColor(color); muted.setAlpha(110)
        pal.setColor(QPalette.ColorRole.PlaceholderText, muted)
        self.setPalette(pal)
        # Initial cursor format
        self.setTextColor(color)

    def _enforce_color(self):
        fmt = self.currentCharFormat()
        if fmt.foreground().color() != self._text_color:
            fmt.setForeground(self._text_color)
            self.setCurrentCharFormat(fmt)

    def keyPressEvent(self, e):
        self._enforce_color()
        super().keyPressEvent(e)

    def insertFromMimeData(self, source):
        if source.hasText():
            cursor = self.textCursor()
            fmt = cursor.charFormat()
            fmt.setForeground(self._text_color)
            cursor.insertText(source.text(), fmt)
        else:
            super().insertFromMimeData(source)

    def setPlainText(self, text: str):
        super().setPlainText(text)
        # Re-paint everything in our color (the document loses formats on
        # setPlainText, but the next typed char will pick up whatever the
        # cursor's char format is — make sure that's our color).
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setForeground(self._text_color)
        cursor.mergeCharFormat(fmt)
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.setTextColor(self._text_color)


class NotesPanel(QWidget):
    vault_unlock_requested = pyqtSignal()
    vault_lock_requested   = pyqtSignal()
    github_token_changed   = pyqtSignal(str)   # emits new token name ("" = none)
    claude_login_requested = pyqtSignal()      # user clicked Login button for current tab

    def __init__(self,parent=None):
        super().__init__(parent); self.setMinimumWidth(180); self.resize(240,self.height())
        self.setStyleSheet(f"background:{C_PANEL.name()};border-left:1px solid {C_SURFACE.name()};")
        lay=QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(4)
        splitter=QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(5)
        splitter.setStyleSheet(f"QSplitter::handle{{background:{C_SURFACE.name()};margin:2px 0;}}")

        notes_w=QWidget(); notes_w.setStyleSheet("background:transparent;")
        nl=QVBoxLayout(notes_w); nl.setContentsMargins(0,0,0,2); nl.setSpacing(2)
        nl.addWidget(QLabel("📝  Notes",styleSheet=f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;background:transparent;"))
        self._notes_edit=_ColoredTextEdit(QColor("#ffd60a"))
        self._notes_edit.setStyleSheet(f"QTextEdit{{background:{C_BG.name()};border:none;font-family:{FONT_FAMILY};font-size:12px;}}")
        self._notes_edit.setPlaceholderText("Type notes here…"); nl.addWidget(self._notes_edit)

        tasks_w=QWidget(); tasks_w.setStyleSheet("background:transparent;")
        tl=QVBoxLayout(tasks_w); tl.setContentsMargins(0,2,0,0); tl.setSpacing(2)

        tasks_hdr=QWidget(); tasks_hdr.setStyleSheet("background:transparent;")
        tasks_hdr_lay=QHBoxLayout(tasks_hdr); tasks_hdr_lay.setContentsMargins(0,0,0,0); tasks_hdr_lay.setSpacing(6)
        tasks_hdr_lay.addWidget(QLabel("✅  Tasks",styleSheet=f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;background:transparent;"))
        self._task_badge=QLabel("0")
        self._task_badge.setStyleSheet(f"background:{C_ACCENT.name()};color:#000;font-size:10px;font-weight:bold;border-radius:7px;padding:1px 6px;min-width:14px;")
        self._task_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._task_badge.setVisible(False)
        tasks_hdr_lay.addWidget(self._task_badge); tasks_hdr_lay.addStretch()
        tl.addWidget(tasks_hdr)

        self._tasks_edit=_ColoredTextEdit(QColor("#a5d6ff"))
        self._tasks_edit.setStyleSheet(f"QTextEdit{{background:{C_BG.name()};border:none;font-family:{FONT_FAMILY};font-size:12px;}}")
        self._tasks_edit.setPlaceholderText("Type tasks here…"); tl.addWidget(self._tasks_edit)
        self._numbering=False
        self._tasks_edit.textChanged.connect(self._on_tasks_changed)

        # ── Variables section (encrypted vault) ────────────────────────────────
        vars_w=QWidget(); vars_w.setStyleSheet("background:transparent;")
        vl=QVBoxLayout(vars_w); vl.setContentsMargins(0,2,0,0); vl.setSpacing(2)
        vars_hdr=QWidget(); vars_hdr.setStyleSheet("background:transparent;")
        vh_lay=QHBoxLayout(vars_hdr); vh_lay.setContentsMargins(0,0,0,0); vh_lay.setSpacing(4)
        vh_lay.addWidget(QLabel("🔒  Variables  (Encrypted)",styleSheet=f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;background:transparent;"))
        vh_lay.addStretch()
        self._add_btn=QPushButton("+"); self._add_btn.setFixedSize(18,18)
        _btn_css=f"QPushButton{{background:{C_SURFACE.name()};color:{C_FG.name()};border:none;font-size:12px;font-weight:bold;border-radius:3px;}}QPushButton:hover{{background:{C_ACCENT.name()}44;color:{C_ACCENT.name()};}}QPushButton:disabled{{color:{C_MUTED.name()};}}"
        self._add_btn.setStyleSheet(_btn_css)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setToolTip("Add variable")
        self._add_btn.clicked.connect(self._add_var_row); vh_lay.addWidget(self._add_btn)
        self._del_btn=QPushButton("−"); self._del_btn.setFixedSize(18,18)
        self._del_btn.setStyleSheet(_btn_css)
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setToolTip("Remove selected variable")
        self._del_btn.clicked.connect(self._del_var_row); vh_lay.addWidget(self._del_btn)
        self._copy_val_btn=QPushButton("⎘"); self._copy_val_btn.setFixedSize(18,18)
        self._copy_val_btn.setStyleSheet(_btn_css)
        self._copy_val_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_val_btn.setToolTip("Copy value of selected variable")
        self._copy_val_btn.clicked.connect(self._copy_var_value); vh_lay.addWidget(self._copy_val_btn)
        self._lock_btn=QPushButton("🔓"); self._lock_btn.setFixedSize(22,18)
        self._lock_btn.setStyleSheet(_btn_css)
        self._lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_btn.setToolTip("Lock vault")
        self._lock_btn.clicked.connect(lambda: self.vault_lock_requested.emit())
        vh_lay.addWidget(self._lock_btn)
        vl.addWidget(vars_hdr)

        # Stacked: 0 = locked pane, 1 = unlocked table
        self._vars_stack=QStackedWidget()
        # locked pane
        locked_w=QWidget(); locked_w.setStyleSheet("background:transparent;")
        ll=QVBoxLayout(locked_w); ll.setContentsMargins(6,18,6,18); ll.setSpacing(8)
        ll.addStretch()
        locked_icon=QLabel("🔒"); locked_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        locked_icon.setStyleSheet("font-size:32px;background:transparent;")
        ll.addWidget(locked_icon)
        locked_text=QLabel("Variables are encrypted.\nUnlock to view and edit.")
        locked_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        locked_text.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;background:transparent;")
        ll.addWidget(locked_text)
        unlock_btn=QPushButton("🔓  Unlock Vault"); unlock_btn.setFixedHeight(28)
        unlock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        unlock_btn.setStyleSheet(f"QPushButton{{background:{C_SURFACE.name()};color:{C_ACCENT.name()};border:1px solid {C_ACCENT.name()}66;border-radius:4px;font-size:11px;font-weight:bold;padding:0 10px;}}QPushButton:hover{{background:{C_ACCENT.name()}33;}}")
        unlock_btn.clicked.connect(lambda: self.vault_unlock_requested.emit())
        ll.addWidget(unlock_btn)
        ll.addStretch()
        self._vars_stack.addWidget(locked_w)

        # unlocked table
        self._vars_table=QTableWidget(0,2)
        self._vars_table.setHorizontalHeaderLabels(["Key","Value"])
        self._vars_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self._vars_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self._vars_table.verticalHeader().setVisible(False)
        self._vars_table.setStyleSheet(f"QTableWidget{{background:{C_BG.name()};color:{C_FG.name()};border:none;font-family:{FONT_FAMILY};font-size:11px;gridline-color:{C_SURFACE.name()};}}QHeaderView::section{{background:{C_PANEL.name()};color:{C_MUTED.name()};border:none;font-size:10px;padding:2px;}}QTableWidget::item:selected{{background:{C_ACCENT.name()}44;color:{C_ACCENT.name()};}}")
        self._vars_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked|QTableWidget.EditTrigger.SelectedClicked)
        self._vars_stack.addWidget(self._vars_table)
        vl.addWidget(self._vars_stack)

        # Default to locked until AppWindow flips state
        self._vault_unlocked=False
        self._apply_vault_state()

        # ── Autostart section ──────────────────────────────────────────────────
        # Records a working dir + command to re-run for this tab on next launch.
        auto_w=QWidget(); auto_w.setStyleSheet("background:transparent;")
        al=QVBoxLayout(auto_w); al.setContentsMargins(0,2,0,0); al.setSpacing(3)
        al.addWidget(QLabel("🚀  Autostart",
            styleSheet=f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;background:transparent;"))
        _auto_lbl_css=f"color:{C_MUTED.name()};font-size:10px;background:transparent;"
        _auto_edit_css=(f"QLineEdit{{background:{C_BG.name()};color:{C_FG.name()};border:1px solid {C_SURFACE.name()};"
                       f"border-radius:3px;font-family:{FONT_FAMILY};font-size:11px;padding:3px 6px;}}"
                       f"QLineEdit:focus{{border-color:{C_ACCENT.name()};}}")
        dir_lbl=QLabel("Working directory"); dir_lbl.setStyleSheet(_auto_lbl_css); al.addWidget(dir_lbl)
        self._auto_dir=QLineEdit()
        self._auto_dir.setPlaceholderText("e.g. ~/projects/myapp")
        self._auto_dir.setStyleSheet(_auto_edit_css)
        al.addWidget(self._auto_dir)
        cmd_lbl=QLabel("Command"); cmd_lbl.setStyleSheet(_auto_lbl_css); al.addWidget(cmd_lbl)
        self._auto_cmd=QLineEdit()
        self._auto_cmd.setPlaceholderText("e.g. npm run dev")
        self._auto_cmd.setStyleSheet(_auto_edit_css)
        al.addWidget(self._auto_cmd)
        hint=QLabel("Runs on next launch of AIDE for this tab.")
        hint.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;font-style:italic;")
        hint.setWordWrap(True); al.addWidget(hint)

        # ── GitHub token (per-tab, injected as env before autostart) ──────────
        gh_lbl_hdr = QLabel("🐙  GitHub token")
        gh_lbl_hdr.setStyleSheet(f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;background:transparent;padding-top:10px;")
        al.addWidget(gh_lbl_hdr)
        gh_hint = QLabel("Exports GITHUB_TOKEN &amp; GH_TOKEN for this terminal.")
        gh_hint.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;")
        gh_hint.setWordWrap(True); al.addWidget(gh_hint)
        self._gh_token_combo = QComboBox()
        self._gh_token_combo.setStyleSheet(
            f"QComboBox{{background:{C_BG.name()};color:{C_FG.name()};border:1px solid {C_SURFACE.name()};"
            f"border-radius:3px;font-family:{FONT_FAMILY};font-size:11px;padding:2px 6px;}}"
            f"QComboBox:focus{{border-color:{C_ACCENT.name()};}}"
            f"QComboBox::drop-down{{border:none;width:14px;}}"
            f"QComboBox QAbstractItemView{{background:{C_SURFACE.name()};color:{C_FG.name()};"
            f"border:1px solid {C_SURFACE.name()};selection-background-color:{C_ACCENT.name()}44;}}")
        self._gh_token_combo.currentTextChanged.connect(
            lambda t: self.github_token_changed.emit("" if t == "(none)" else t))
        al.addWidget(self._gh_token_combo)

        # ── Claude account (per-tab CLAUDE_CONFIG_DIR) ────────────────────────
        claude_hdr = QLabel("🤖  Claude account")
        claude_hdr.setStyleSheet(f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;"
                                 f"background:transparent;padding-top:10px;")
        al.addWidget(claude_hdr)
        claude_hint = QLabel("Per-tab profile → separate CLAUDE_CONFIG_DIR. "
                             "Each profile logs in once; blank = shared ~/.claude/.")
        claude_hint.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;")
        claude_hint.setWordWrap(True); al.addWidget(claude_hint)
        prof_lbl = QLabel("Profile name"); prof_lbl.setStyleSheet(_auto_lbl_css)
        al.addWidget(prof_lbl)
        self._claude_profile = QLineEdit()
        self._claude_profile.setPlaceholderText("e.g. work, personal  (blank = default)")
        self._claude_profile.setStyleSheet(_auto_edit_css)
        al.addWidget(self._claude_profile)
        model_lbl = QLabel("Model"); model_lbl.setStyleSheet(_auto_lbl_css)
        al.addWidget(model_lbl)
        _combo_ss = (f"QComboBox{{background:{C_BG.name()};color:{C_FG.name()};"
                     f"border:1px solid {C_SURFACE.name()};border-radius:3px;"
                     f"font-family:{FONT_FAMILY};font-size:11px;padding:2px 6px;}}"
                     f"QComboBox:focus{{border-color:{C_ACCENT.name()};}}"
                     f"QComboBox::drop-down{{border:none;width:14px;}}"
                     f"QComboBox QAbstractItemView{{background:{C_SURFACE.name()};"
                     f"color:{C_FG.name()};border:1px solid {C_SURFACE.name()};"
                     f"selection-background-color:{C_ACCENT.name()}44;}}")
        self._claude_model = QComboBox()
        self._claude_model.setStyleSheet(_combo_ss)
        for m_label, m_id in [
            ("(default)", ""),
            ("Opus 4.7  — most capable", "claude-opus-4-7"),
            ("Sonnet 4.6  — balanced", "claude-sonnet-4-6"),
            ("Haiku 4.5  — fastest", "claude-haiku-4-5-20251001"),
        ]:
            self._claude_model.addItem(m_label, m_id)
        al.addWidget(self._claude_model)
        args_lbl = QLabel("Extra `claude` args"); args_lbl.setStyleSheet(_auto_lbl_css)
        al.addWidget(args_lbl)
        self._claude_args = QLineEdit()
        self._claude_args.setPlaceholderText("e.g. --no-auto-compact  (optional)")
        self._claude_args.setStyleSheet(_auto_edit_css)
        al.addWidget(self._claude_args)
        login_btn = QPushButton("🔐  claude /login")
        login_btn.setStyleSheet(
            f"QPushButton{{background:{C_SURFACE.name()};color:{C_FG.name()};"
            f"border:none;border-radius:3px;font-size:11px;padding:4px 8px;}}"
            f"QPushButton:hover{{background:{C_ACCENT.name()}44;color:{C_ACCENT.name()};}}")
        login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        login_btn.clicked.connect(lambda: self.claude_login_requested.emit())
        al.addWidget(login_btn)

        al.addStretch()

        splitter.addWidget(notes_w); splitter.addWidget(tasks_w)
        splitter.addWidget(vars_w);  splitter.addWidget(auto_w)
        lay.addWidget(splitter)

    def _apply_vault_state(self):
        unlocked=self._vault_unlocked
        self._vars_stack.setCurrentIndex(1 if unlocked else 0)
        self._add_btn.setEnabled(unlocked)
        self._del_btn.setEnabled(unlocked)
        self._lock_btn.setText("🔓" if unlocked else "🔒")
        self._lock_btn.setToolTip("Lock vault" if unlocked else "Vault is locked")
        self._lock_btn.setEnabled(unlocked)

    def set_vault_unlocked(self,unlocked:bool):
        self._vault_unlocked=unlocked
        if not unlocked: self._vars_table.setRowCount(0)
        self._apply_vault_state()

    def apply_variables(self,variables:Dict[str,str]):
        self._vars_table.setRowCount(0)
        for k,v in (variables or {}).items():
            r=self._vars_table.rowCount(); self._vars_table.insertRow(r)
            self._vars_table.setItem(r,0,QTableWidgetItem(k))
            self._vars_table.setItem(r,1,QTableWidgetItem(v))

    def _add_var_row(self):
        if not self._vault_unlocked: return
        r=self._vars_table.rowCount(); self._vars_table.insertRow(r)
        self._vars_table.setItem(r,0,QTableWidgetItem("")); self._vars_table.setItem(r,1,QTableWidgetItem(""))
        self._vars_table.editItem(self._vars_table.item(r,0))

    def _del_var_row(self):
        if not self._vault_unlocked: return
        rows={i.row() for i in self._vars_table.selectedItems()}
        for r in sorted(rows,reverse=True): self._vars_table.removeRow(r)

    def _copy_var_value(self):
        rows={i.row() for i in self._vars_table.selectedItems()}
        if not rows: return
        row=min(rows)
        item=self._vars_table.item(row,1)
        if item:
            QApplication.clipboard().setText(item.text())

    def _on_tasks_changed(self):
        if self._numbering: return
        self._numbering=True
        try:
            cursor=self._tasks_edit.textCursor()
            block=cursor.blockNumber(); col=cursor.positionInBlock()
            text=self._tasks_edit.toPlainText()
            lines=text.split("\n")
            numbered=[]; n=0
            for line in lines:
                stripped=re.sub(r"^\d+\.\s","",line)
                if stripped: n+=1; numbered.append(f"{n}. {stripped}")
                else: numbered.append("")
            new_text="\n".join(numbered)
            self._task_badge.setText(str(n)); self._task_badge.setVisible(n>0)
            if new_text!=text:
                new_lines=new_text.split("\n")
                old_line=lines[block] if block<len(lines) else ""
                new_line=new_lines[block] if block<len(new_lines) else ""
                old_m=re.match(r"^\d+\.\s",old_line); new_m=re.match(r"^\d+\.\s",new_line)
                old_p=len(old_m.group()) if old_m else 0; new_p=len(new_m.group()) if new_m else 0
                adj=max(new_p, col+(new_p-old_p))
                adj=min(adj,len(new_line))
                abs_pos=sum(len(new_lines[i])+1 for i in range(min(block,len(new_lines))))+adj
                self._tasks_edit.blockSignals(True)
                self._tasks_edit.setPlainText(new_text)
                self._tasks_edit.blockSignals(False)
                c=self._tasks_edit.textCursor(); c.setPosition(min(abs_pos,len(new_text)))
                self._tasks_edit.setTextCursor(c)
        finally:
            self._numbering=False

    def load(self,notes:str,tasks:str="",variables:Optional[Dict[str,str]]=None,
             autostart_dir:str="",autostart_cmd:str="",github_token_name:str="",
             claude_profile:str="",claude_model:str="",claude_args:str=""):
        self._notes_edit.setPlainText(notes)
        self._numbering=True
        self._tasks_edit.setPlainText(tasks)
        self._numbering=False
        self._on_tasks_changed()
        if self._vault_unlocked:
            self.apply_variables(variables or {})
        else:
            self._vars_table.setRowCount(0)
        self._auto_dir.setText(autostart_dir or "")
        self._auto_cmd.setText(autostart_cmd or "")
        self._gh_token_combo.blockSignals(True)
        idx = self._gh_token_combo.findText(github_token_name) if github_token_name else 0
        self._gh_token_combo.setCurrentIndex(max(0, idx))
        self._gh_token_combo.blockSignals(False)
        self._claude_profile.setText(claude_profile or "")
        idx = max(0, self._claude_model.findData(claude_model or ""))
        self._claude_model.setCurrentIndex(idx)
        self._claude_args.setText(claude_args or "")

    def set_github_token_names(self, names: list, current: str):
        """Populate the per-tab token selector with available token names."""
        self._gh_token_combo.blockSignals(True)
        self._gh_token_combo.clear()
        self._gh_token_combo.addItem("(none)")
        for n in sorted(names):
            self._gh_token_combo.addItem(n)
        idx = self._gh_token_combo.findText(current) if current else 0
        self._gh_token_combo.setCurrentIndex(max(0, idx))
        self._gh_token_combo.blockSignals(False)

    def get_github_token_name(self) -> str:
        t = self._gh_token_combo.currentText()
        return "" if t == "(none)" else t

    def get_notes(self)->str: return self._notes_edit.toPlainText()
    def get_tasks(self)->str: return self._tasks_edit.toPlainText()
    def get_autostart_dir(self)->str: return self._auto_dir.text().strip()
    def get_autostart_cmd(self)->str: return self._auto_cmd.text().strip()
    def get_claude_profile(self)->str: return self._claude_profile.text().strip()
    def get_claude_model(self)->str:   return self._claude_model.currentData() or ""
    def get_claude_args(self)->str:    return self._claude_args.text().strip()
    def get_variables(self)->Optional[Dict[str,str]]:
        """Return current variables, or None if the vault is locked.

        Callers must treat a None return as "don't touch the stored values" —
        otherwise we'd wipe the vault contents any time the user saves while
        locked.
        """
        if not self._vault_unlocked: return None
        out={}
        for r in range(self._vars_table.rowCount()):
            k_item=self._vars_table.item(r,0); v_item=self._vars_table.item(r,1)
            k=k_item.text().strip() if k_item else ""; v=v_item.text() if v_item else ""
            if k: out[k]=v
        return out
    def focus_editor(self): self._notes_edit.setFocus()

def _strip_html(raw:str)->str:
    raw=re.sub(r"<(script|style)[^>]*>.*?</(script|style)>","",raw,flags=re.DOTALL|re.I)
    raw=re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*/?>\s*","\n",raw,flags=re.I)
    raw=re.sub(r"<[^>]+>","",raw)
    import html; raw=html.unescape(raw)
    return "\n".join(l.strip() for l in raw.split("\n") if l.strip())

_BROWSE_SPLASH = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         display: flex; align-items: center; justify-content: center; height: 100vh; }
  .card { text-align: center; max-width: 380px; padding: 32px; }
  h2 { font-size: 22px; color: #58a6ff; margin-bottom: 10px; }
  p  { font-size: 13px; color: #7d8590; line-height: 1.6; margin-bottom: 8px; }
  code { background: #21262d; color: #a5d6ff; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
</style></head><body>
<div class="card">
  <h2>Browser</h2>
  <p>Type a URL in the bar above, or run a local dev server —<br>AIDE will open it here automatically.</p>
  <p><code>localhost:PORT</code> detected in terminal output triggers auto-navigate.</p>
</div></body></html>
"""

_BTN_SS = (
    "QPushButton{background:#21262d;color:#e6edf3;border:none;border-radius:4px;"
    "font-size:14px;padding:0;}"
    "QPushButton:hover{background:#58a6ff44;color:#58a6ff;}"
    "QPushButton:disabled{color:#7d8590;}"
)

class BrowsePane(QWidget):
    url_changed    = pyqtSignal(str)   # emitted whenever the user navigates to a new URL
    _fetch_result  = pyqtSignal(str)   # internal — fallback path only

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{C_BG.name()};")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self._loading = False

        # ── toolbar ──────────────────────────────────────────────────────────
        bar = QWidget(); bar.setFixedHeight(38)
        bar.setStyleSheet(f"background:{C_SURFACE.name()};border-bottom:1px solid {C_PANEL.name()};")
        bl = QHBoxLayout(bar); bl.setContentsMargins(6, 4, 6, 4); bl.setSpacing(4)

        self._back_btn   = QPushButton("←"); self._back_btn.setFixedSize(30, 28)
        self._fwd_btn    = QPushButton("→"); self._fwd_btn.setFixedSize(30, 28)
        self._reload_btn = QPushButton("↻"); self._reload_btn.setFixedSize(30, 28)
        for b in (self._back_btn, self._fwd_btn, self._reload_btn):
            b.setStyleSheet(_BTN_SS); b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.setEnabled(False); self._fwd_btn.setEnabled(False)
        self._back_btn.setToolTip("Back"); self._fwd_btn.setToolTip("Forward")
        self._reload_btn.setToolTip("Reload / Stop")

        self._url = QLineEdit()
        self._url.setPlaceholderText("http://localhost:PORT  or any URL…")
        self._url.setStyleSheet(
            f"QLineEdit{{background:{C_BG.name()};color:{C_FG.name()};"
            f"border:1px solid {C_PANEL.name()};border-radius:4px;"
            f"font-size:12px;padding:3px 8px;}}"
            f"QLineEdit:focus{{border-color:{C_ACCENT.name()};}}"
        )
        self._url.returnPressed.connect(self._go)

        self._sys_btn = QPushButton("⊕"); self._sys_btn.setFixedSize(30, 28)
        self._sys_btn.setStyleSheet(_BTN_SS)
        self._sys_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sys_btn.setToolTip("Open in system browser")
        self._sys_btn.clicked.connect(self._open_sys)

        bl.addWidget(self._back_btn); bl.addWidget(self._fwd_btn)
        bl.addWidget(self._reload_btn); bl.addWidget(self._url, 1)
        bl.addWidget(self._sys_btn)
        lay.addWidget(bar)

        # ── progress bar ──────────────────────────────────────────────────────
        from PyQt6.QtWidgets import QProgressBar
        self._progress = QProgressBar()
        self._progress.setFixedHeight(2); self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{C_SURFACE.name()};border:none;}}"
            f"QProgressBar::chunk{{background:{C_ACCENT.name()};}}"
        )
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        # ── content area ──────────────────────────────────────────────────────
        if _HAS_WEBENGINE:
            self._web = QWebEngineView()
            s = self._web.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
            self._web.urlChanged.connect(self._on_url_changed)
            self._web.loadStarted.connect(self._on_load_started)
            self._web.loadProgress.connect(self._on_load_progress)
            self._web.loadFinished.connect(self._on_load_finished)
            self._back_btn.clicked.connect(self._web.back)
            self._fwd_btn.clicked.connect(self._web.forward)
            self._reload_btn.clicked.connect(self._toggle_reload)
            self._web.setHtml(_BROWSE_SPLASH, QUrl("about:blank"))
            lay.addWidget(self._web, 1)
        else:
            # Fallback: plain-text fetch
            self._back_btn.setVisible(False); self._fwd_btn.setVisible(False)
            self._content = QTextEdit(); self._content.setReadOnly(True)
            self._content.setStyleSheet(
                f"QTextEdit{{background:{C_BG.name()};color:{C_FG.name()};"
                f"border:none;font-family:{FONT_FAMILY};font-size:12px;padding:12px;}}"
            )
            self._content.setHtml(
                "<p style='color:#7d8590;font-family:sans-serif;padding:20px;'>"
                "Install <b>PyQt6-WebEngine</b> for full browser support:<br>"
                "<code style='background:#21262d;padding:4px 8px;border-radius:4px;'>"
                "pip install PyQt6-WebEngine</code><br><br>"
                "Fallback: plain-text HTTP fetch is active.</p>"
            )
            self._reload_btn.clicked.connect(self._go)
            lay.addWidget(self._content, 1)

    # ── WebEngine slots ───────────────────────────────────────────────────────
    def _on_url_changed(self, qurl: "QUrl"):
        u = qurl.toString()
        if u not in ("about:blank", ""):
            self._url.setText(u)
            self.url_changed.emit(u)
        self._update_nav()

    def _on_load_started(self):
        self._loading = True
        self._reload_btn.setText("✕"); self._reload_btn.setToolTip("Stop")
        self._progress.setValue(0); self._progress.setVisible(True)

    def _on_load_progress(self, pct: int):
        self._progress.setValue(pct)

    def _on_load_finished(self, ok: bool):
        self._loading = False
        self._reload_btn.setText("↻"); self._reload_btn.setToolTip("Reload")
        self._progress.setVisible(False)
        self._update_nav()

    def _update_nav(self):
        if _HAS_WEBENGINE:
            h = self._web.page().history()
            self._back_btn.setEnabled(h.canGoBack())
            self._fwd_btn.setEnabled(h.canGoForward())

    def _toggle_reload(self):
        if self._loading: self._web.stop()
        else:             self._web.reload()

    # ── public API ────────────────────────────────────────────────────────────
    def navigate(self, url: str):
        if not url: return
        if "://" not in url: url = "http://" + url
        self._url.setText(url)
        self.url_changed.emit(url)
        if _HAS_WEBENGINE:
            self._web.load(QUrl(url))
        else:
            self._fetch(url)

    def _go(self):
        url = self._url.text().strip()
        if url: self.navigate(url)

    def _open_sys(self):
        url = self._url.text().strip()
        if url: webbrowser.open(url if "://" in url else "http://" + url)

    def set_content(self, text: str):
        """Fallback path used when WebEngine is unavailable."""
        if not _HAS_WEBENGINE:
            self._content.setPlainText(text)

    # ── fallback fetch ────────────────────────────────────────────────────────
    def _fetch(self, url: str):
        self._content.setPlainText(f"Fetching {url}…")
        try: self._fetch_result.disconnect()
        except: pass
        self._fetch_result.connect(self.set_content)
        def _do():
            try:
                req = Request(url, headers={"User-Agent": f"AIDE/{VERSION}"})
                with urlopen(req, timeout=8) as r:
                    ct  = r.headers.get("Content-Type", "")
                    raw = r.read().decode("utf-8", errors="replace")
                text = _strip_html(raw) if "html" in ct.lower() else raw
                if len(text) > 8000: text = text[:8000] + "\n\n[… truncated]"
            except Exception as e:
                text = f"Error: {e}\n\nTip: click ⊕ to open in system browser."
            self._fetch_result.emit(text)
        threading.Thread(target=_do, daemon=True).start()

# ═════════════════════════════════════════════════════════════════════════════
# DIALOGS
# ═════════════════════════════════════════════════════════════════════════════

def _dlg_ss():
    return f"""
        QDialog{{background:{C_PANEL.name()};color:{C_FG.name()};}}
        QLabel{{color:{C_FG.name()};}}
        QLineEdit{{background:{C_BG.name()};color:{C_FG.name()};border:1px solid {C_SURFACE.name()};border-radius:4px;padding:4px 8px;}}
        QTextEdit{{background:{C_BG.name()};color:{C_FG.name()};border:1px solid {C_SURFACE.name()};}}
        QPushButton{{background:{C_SURFACE.name()};color:{C_FG.name()};border:none;border-radius:4px;padding:6px 14px;}}
        QPushButton:hover{{background:{C_ACCENT.name()}44;}}
        QCheckBox{{color:{C_FG.name()};spacing:6px;}}
        QComboBox{{background:{C_BG.name()};color:{C_FG.name()};border:1px solid {C_SURFACE.name()};border-radius:4px;padding:4px;}}
        QComboBox QAbstractItemView{{background:{C_SURFACE.name()};color:{C_FG.name()};}}
        QSpinBox,QDoubleSpinBox{{background:{C_BG.name()};color:{C_FG.name()};border:1px solid {C_SURFACE.name()};border-radius:4px;padding:2px 6px;}}
        QScrollBar:vertical{{width:6px;background:transparent;}}
        QScrollBar::handle:vertical{{background:#444;border-radius:3px;}}
    """

def _primary_btn(btn):
    btn.setStyleSheet(f"QPushButton{{background:{C_ACCENT.name()};color:#000;font-weight:bold;border:none;border-radius:4px;padding:6px 14px;}}QPushButton:hover{{background:{C_ACCENT.name()}cc;}}")

class TerminalConfigDialog(QDialog):
    """Double-click dialog: rename a terminal and configure its Neural Bus membership."""
    def __init__(self, session: "TermSession", neural_url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Terminal Settings")
        self.setStyleSheet(_dlg_ss()); self.setFixedWidth(440)
        self._session = session
        self._new_name: Optional[str] = None
        self._neural_result = None  # None=no change, dict=join/update, False=leave

        lay = QVBoxLayout(self); lay.setSpacing(10); lay.setContentsMargins(20, 20, 20, 20)

        # ── Name ──
        lay.addWidget(QLabel("Terminal name:"))
        self._name_inp = QLineEdit(session.custom_title or session.effective_title())
        self._name_inp.selectAll()
        lay.addWidget(self._name_inp)

        # ── Separator ──
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{C_SURFACE.name()};max-height:1px;")
        lay.addWidget(sep)

        # ── Neural Bus ──
        hdr = QLabel("🤖  Neural Bus")
        hdr.setStyleSheet(f"color:{C_ACCENT.name()};font-weight:bold;font-size:12px;")
        lay.addWidget(hdr)

        self._neural_chk = QCheckBox("Connected to Neural Bus")
        self._neural_chk.setChecked(session.neural_on_bus)
        lay.addWidget(self._neural_chk)

        # Neural fields (shown only when connected)
        self._nf = QWidget()
        nfl = QFormLayout(self._nf); nfl.setSpacing(8)
        nfl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        p = session._neural_profile or {}
        self._n_name = QLineEdit(p.get("name", session.effective_title()))
        self._n_name.setPlaceholderText("e.g. Frontend Agent")
        self._n_tag  = QLineEdit(p.get("tag", ", ".join(getattr(session, "tags", []))))
        self._n_tag.setPlaceholderText("e.g. ui, auth")
        self._n_app  = QLineEdit(p.get("app", ""))
        self._n_app.setPlaceholderText("e.g. nanoai, myapp")
        self._n_role = QLineEdit(p.get("role", ""))
        self._n_role.setPlaceholderText("e.g. Implements UI components")
        self._n_task = QLineEdit(p.get("task", ""))
        self._n_task.setPlaceholderText("e.g. Building the login page")
        nfl.addRow("Name:", self._n_name)
        nfl.addRow("Tag:", self._n_tag)
        nfl.addRow("App:", self._n_app)
        nfl.addRow("Role:", self._n_role)
        nfl.addRow("Current task:", self._n_task)
        lay.addWidget(self._nf)
        self._nf.setVisible(session.neural_on_bus)
        self._neural_chk.toggled.connect(self._nf.setVisible)

        # Copy agent prompt
        self._neural_url = neural_url
        cp_btn = QPushButton("📋  Copy agent prompt")
        cp_btn.setStyleSheet(
            f"QPushButton{{background:{C_SURFACE.name()};color:{C_FG.name()};"
            f"border:none;border-radius:3px;font-size:10px;padding:4px 8px;}}"
            f"QPushButton:hover{{background:{C_ACCENT.name()}44;color:{C_ACCENT.name()};}}")
        cp_btn.clicked.connect(self._copy_agent_prompt)
        lay.addWidget(cp_btn)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        _primary_btn(bb.button(QDialogButtonBox.StandardButton.Ok))
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._name_inp.returnPressed.connect(self._ok)

    def _ok(self):
        name = self._name_inp.text().strip()
        current = self._session.custom_title or self._session.effective_title()
        if name and name != current:
            self._new_name = name
        on_bus = self._neural_chk.isChecked()
        if on_bus:
            n_name = self._n_name.text().strip() or self._session.effective_title()
            self._neural_result = {
                "name": n_name, "tag": self._n_tag.text().strip(),
                "app":  self._n_app.text().strip(), "role": self._n_role.text().strip(),
                "task": self._n_task.text().strip(),
            }
        elif self._session.neural_on_bus:
            self._neural_result = False
        self.accept()

    def _copy_agent_prompt(self):
        url = self._neural_url or "http://127.0.0.1:<port>"
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
- `AIDE_NEURAL_BRAIN_FILE` — path to the shared memory file (readable by all agents)
- The `neural` command is on your PATH

### Shared brain (read on startup)
```
neural brain
```
Prints the shared memory file — instructions and context set by the human
for all agents. Read it at the start of every session.

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
        QApplication.clipboard().setText(prompt)

    def get_new_name(self) -> Optional[str]: return self._new_name
    def get_neural_result(self): return self._neural_result


class NewTerminalDialog(QDialog):
    """Shown when a new terminal is opened — displays an agent onboarding prompt."""

    def __init__(self, session: "TermSession", neural_url: str,
                 brain_file: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Terminal — Agent Prompt")
        self.setStyleSheet(_dlg_ss())
        self.setFixedWidth(560)

        prompt = self._build_prompt(session, neural_url, brain_file)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        hdr = QLabel("🚀  Agent Startup Prompt")
        hdr.setStyleSheet(f"color:{C_ACCENT.name()};font-weight:bold;font-size:13px;")
        lay.addWidget(hdr)

        hint = QLabel("Paste this into your agent at the start of the session.")
        hint.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;")
        lay.addWidget(hint)

        self._text = QPlainTextEdit(prompt)
        self._text.setReadOnly(True)
        self._text.setStyleSheet(
            f"QPlainTextEdit{{background:{C_BG.name()};color:{C_FG.name()};"
            f"border:1px solid {C_SURFACE.name()};border-radius:4px;"
            f"font-family:{FONT_FAMILY};font-size:11px;padding:6px;}}")
        self._text.setMinimumHeight(320)
        lay.addWidget(self._text)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        copy_btn = QPushButton("📋  Copy prompt")
        copy_btn.setStyleSheet(
            f"QPushButton{{background:{C_ACCENT.name()};color:#000;border:none;"
            f"border-radius:4px;font-size:12px;font-weight:bold;padding:6px 18px;}}"
            f"QPushButton:hover{{background:{C_ACCENT.name()}cc;}}")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy)
        copy_btn.setDefault(True)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            f"QPushButton{{background:{C_SURFACE.name()};color:{C_FG.name()};"
            f"border:none;border-radius:4px;font-size:12px;padding:6px 18px;}}"
            f"QPushButton:hover{{background:{C_SURFACE.name()}cc;}}")
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(copy_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._prompt = prompt

    def _copy(self):
        QApplication.clipboard().setText(self._prompt)

    @staticmethod
    def _build_prompt(session: "TermSession", neural_url: str, brain_file: str) -> str:
        sid   = session.tab_id
        wdir  = session.autostart_dir or ""
        cmd   = session.autostart_cmd or ""
        prof  = session.claude_profile or ""
        args  = session.claude_args or ""
        token = session.github_token_name or ""

        lines = [
            f"## AIDE Session — Terminal #{sid}",
            "",
            f"You are an AI agent running inside AIDE terminal #{sid}.",
            "",
            "### Environment",
            f"- `AIDE_SESSION_ID={sid}`",
            f"- `AIDE_NEURAL_URL={neural_url}` — Neural Bus endpoint",
            f"- `AIDE_NEURAL_BRAIN_FILE={brain_file}` — shared memory",
            "- The `neural` command is on your PATH",
            "",
            "### On startup — read the shared brain",
            "```",
            "neural brain",
            "```",
            "This prints the shared notes and instructions set by the human for all agents.",
            "",
            "### Register on the Neural Bus",
            "```",
            'neural register "<your role>" "<what you are working on>"',
            "```",
        ]

        if wdir or cmd:
            lines += ["", "### Workspace"]
            if wdir:
                lines.append(f"- Working directory: `{wdir}`")
            if cmd:
                lines.append(f"- Autostart command: `{cmd}`")

        if prof or args or token:
            lines += ["", "### Claude account"]
            if prof:
                lines.append(f"- Profile: `{prof}` (uses `~/.aide/claude-profiles/{prof}/`)")
            if args:
                lines.append(f"- Extra args: `{args}`")
            if token:
                lines.append(f"- GitHub token: `{token}` (injected as `$GITHUB_TOKEN`)")

        lines += [
            "",
            "### Discover other agents",
            "```",
            "neural agents",
            "```",
            "",
            "### Send a message to another agent",
            "```",
            'neural send <session_id> "<message>"',
            "```",
            "",
            "### Update your task",
            "```",
            'neural task "<what you are doing now>"',
            "```",
            "",
            "### Guidelines",
            "- Only message other agents when genuinely needed for coordination.",
            "- Be concise. Acknowledge messages with `neural send`.",
            "- Do not spam the bus with routine status updates.",
        ]

        return "\n".join(lines)


def _ver_tuple(v: str):
    """Convert "2.1.0" → (2, 1, 0) for comparison."""
    try: return tuple(int(x) for x in v.split("."))
    except: return (0,)

def _whats_new_entries(from_version: str) -> list:
    """Return (version, entries) pairs for all versions newer than from_version."""
    from_t = _ver_tuple(from_version)
    result = []
    for ver, entries in WHATS_NEW.items():
        if _ver_tuple(ver) > from_t:
            result.append((ver, entries))
    # Sort newest first
    result.sort(key=lambda x: _ver_tuple(x[0]), reverse=True)
    return result


class WhatsNewDialog(QDialog):
    """Shown once after an update; displays only changes since the previously installed version."""
    def __init__(self, sections: list, from_version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"What's New in {APP_NAME}")
        self.setStyleSheet(_dlg_ss()); self.setFixedWidth(500)
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,16); lay.setSpacing(0)
        # header
        if from_version:
            hdr_text = f"  ✨  Updated {from_version} → {VERSION}"
        else:
            hdr_text = f"  ✨  What's new in {VERSION}"
        hdr = QLabel(hdr_text); hdr.setFixedHeight(42)
        hdr.setStyleSheet(
            f"background:{C_ACCENT.name()};color:#000;font-weight:bold;"
            f"font-size:15px;padding:0 16px;")
        lay.addWidget(hdr)
        # scrollable body
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}QScrollBar:vertical{width:4px;}QScrollBar::handle:vertical{background:#444;border-radius:2px;}")
        body = QWidget(); bl = QVBoxLayout(body)
        bl.setContentsMargins(20, 14, 20, 4); bl.setSpacing(0)
        for ver, entries in sections:
            # Version sub-header
            ver_lbl = QLabel(f"v{ver}")
            ver_lbl.setStyleSheet(
                f"color:{C_ACCENT.name()};font-size:11px;font-weight:bold;"
                f"background:transparent;padding:8px 0 4px 0;")
            bl.addWidget(ver_lbl)
            for emoji, title, desc in entries:
                row = QWidget(); rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 2, 0, 6); rl.setSpacing(10)
                ico = QLabel(emoji); ico.setFixedWidth(24)
                ico.setStyleSheet("font-size:16px;background:transparent;")
                ico.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
                txt = QLabel(f"<b style='color:{C_FG.name()}'>{title}</b>"
                             f"<br><span style='color:{C_MUTED.name()};font-size:11px;'>{desc}</span>")
                txt.setWordWrap(True); txt.setStyleSheet("background:transparent;")
                rl.addWidget(ico); rl.addWidget(txt, 1)
                bl.addWidget(row)
        bl.addStretch()
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        # dismiss button
        btn = QPushButton("Got it"); _primary_btn(btn)
        btn.clicked.connect(self.accept)
        brow = QWidget(); brl = QHBoxLayout(brow)
        brl.setContentsMargins(20, 8, 20, 0)
        brl.addStretch(); brl.addWidget(btn)
        lay.addWidget(brow)


class SplitTipDialog(QDialog):
    """One-time tip shown when the user first enters terminal split view."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Split View tip")
        self.setStyleSheet(_dlg_ss())
        self.setFixedWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        # Icon + title
        title = QLabel("⊟  Split View")
        title.setStyleSheet(f"color:{C_FG.name()};font-size:16px;font-weight:bold;")
        lay.addWidget(title)

        # Main tip
        tip = QLabel(
            "<b>Tab-to-paste</b> is active between the two panes.<br><br>"
            "Select any text in one terminal, then press <b>Tab</b> —<br>"
            "it gets pasted instantly into the other pane.<br><br>"
            "Great for copying a command from one shell and running it in another."
        )
        tip.setStyleSheet(f"color:{C_FG.name()};font-size:13px;line-height:1.5;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # Visual hint
        hint = QLabel("  Left pane  →  select text  →  Tab  →  Right pane  ")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            f"color:{C_ACCENT.name()};font-family:'JetBrains Mono',monospace;"
            f"font-size:12px;background:{C_SURFACE.name()};"
            f"border:1px solid {C_ACCENT.name()}33;border-radius:6px;padding:8px;"
        )
        lay.addWidget(hint)

        # Dismiss
        btn = QPushButton("Got it")
        _primary_btn(btn)
        btn.clicked.connect(self.accept)
        brow = QWidget(); brl = QHBoxLayout(brow)
        brl.setContentsMargins(0, 4, 0, 0)
        brl.addStretch(); brl.addWidget(btn)
        lay.addWidget(brow)


class ClipboardDialog(QDialog):
    def __init__(self,cb:SharedClipboard,parent=None):
        super().__init__(parent); self.setWindowTitle("Shared Clipboard")
        self.setStyleSheet(_dlg_ss()); self.setFixedSize(600,380); self._result=None
        lay=QVBoxLayout(self); lay.setContentsMargins(0,0,0,12)
        hdr=QLabel("  📋  Click an entry to paste into the active terminal")
        hdr.setFixedHeight(34)
        hdr.setStyleSheet(f"background:{C_ACCENT.name()};color:#000;font-weight:bold;padding:0 12px;")
        lay.addWidget(hdr)
        self._list=QListWidget()
        self._list.setStyleSheet(f"QListWidget{{background:{C_BG.name()};color:{C_FG.name()};border:none;font-family:{FONT_FAMILY};font-size:12px;}}QListWidget::item{{padding:6px 12px;border-bottom:1px solid {C_SURFACE.name()};}}QListWidget::item:hover{{background:{C_ACCENT.name()}22;}}")
        entries=cb.all(); self._entries=entries
        for e in entries: self._list.addItem(e.replace("\n","↵")[:80])
        if not entries: self._list.addItem("(empty — use ^B-y to copy a terminal screen)")
        self._list.itemDoubleClicked.connect(self._pick); lay.addWidget(self._list,1)
        btn=QPushButton("Cancel"); btn.clicked.connect(self.reject)
        br=QWidget(); brl=QHBoxLayout(br); brl.setContentsMargins(12,0,12,0)
        brl.addStretch(); brl.addWidget(btn); lay.addWidget(br)
    def _pick(self,item):
        idx=self._list.row(item)
        if 0<=idx<len(self._entries): self._result=self._entries[idx]; self.accept()
    def get_text(self)->Optional[str]: return self._result

class CardConfigDialog(QDialog):
    _FIELDS=[("title","Title / custom name"),("cwd","Current directory"),
             ("cmd","Last command"),("ssh","SSH host"),("process","Active process")]
    def __init__(self, cfg: "CardConfig", parent=None):
        super().__init__(parent); self.setWindowTitle("Tab Card Fields")
        self.setStyleSheet(_dlg_ss()); self.setFixedWidth(360); self._result=None
        lay=QVBoxLayout(self); lay.setContentsMargins(20,20,20,20); lay.setSpacing(10)
        lay.addWidget(QLabel("<b>Choose which fields appear on each tab card:</b>"))
        self._checks={}
        for k,l in self._FIELDS:
            cb=QCheckBox(l); cb.setChecked(k in cfg.fields); self._checks[k]=cb; lay.addWidget(cb)
        lay.addWidget(QLabel(""))  # spacer
        self._show_tags_cb=QCheckBox("Show tags on cards")
        self._show_tags_cb.setChecked(getattr(cfg,"show_tags",True))
        lay.addWidget(self._show_tags_cb)
        self._dedup_tags_cb=QCheckBox("Hide repeated tag names (deduplicate)")
        self._dedup_tags_cb.setChecked(getattr(cfg,"dedup_tags",True))
        self._dedup_tags_cb.setToolTip("When consecutive cards share the same tag, only show the tag on the first card")
        lay.addWidget(self._dedup_tags_cb)
        bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        _primary_btn(bb.button(QDialogButtonBox.StandardButton.Save))
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject); lay.addWidget(bb)
    def _save(self):
        self._result=([k for k in self._checks if self._checks[k].isChecked()],
                      self._show_tags_cb.isChecked(),
                      self._dedup_tags_cb.isChecked()); self.accept()
    def get_result(self)->Optional[tuple]: return self._result

class NotifConfigDialog(QDialog):
    def __init__(self,cfg:NotifConfig,auto_restart:bool=False,parent=None):
        super().__init__(parent); self.setWindowTitle("Notification Settings")
        self.setStyleSheet(_dlg_ss()); self.setFixedWidth(460); self._cfg=cfg; self._result=None
        lay=QFormLayout(self); lay.setContentsMargins(20,20,20,20); lay.setSpacing(10)
        self._enabled=QCheckBox("Enable notifications"); self._enabled.setChecked(cfg.enabled)
        lay.addRow(self._enabled)
        self._style=QComboBox()
        for opt in ("banner","popup","both","none"): self._style.addItem(opt)
        self._style.setCurrentText(cfg.style); lay.addRow("Style:",self._style)
        self._sound=QCheckBox("Sound alert"); self._sound.setChecked(cfg.sound); lay.addRow(self._sound)
        self._scmd=QLineEdit(cfg.sound_command); self._scmd.setPlaceholderText("auto-detected")
        lay.addRow("Sound command:",self._scmd)
        self._sdev=QComboBox(); self._sdev.setEditable(True)
        self._sdev.addItem("System Default", "")
        for _dev in _list_sound_devices():
            self._sdev.addItem(_dev, _dev)
        if cfg.sound_device:
            _idx = self._sdev.findData(cfg.sound_device)
            if _idx >= 0: self._sdev.setCurrentIndex(_idx)
            else: self._sdev.setCurrentText(cfg.sound_device)
        lay.addRow("Sound device:",self._sdev)

        # Volume slider (0–200%)
        vol_row=QWidget(); vrl=QHBoxLayout(vol_row); vrl.setContentsMargins(0,0,0,0); vrl.setSpacing(8)
        self._svol=QSlider(Qt.Orientation.Horizontal); self._svol.setRange(0,200)
        self._svol.setValue(int(round(cfg.sound_volume*100)))
        self._svol.setFixedHeight(18)
        self._svol.setStyleSheet("QSlider::groove:horizontal{background:#21262d;height:5px;border-radius:2px;}QSlider::handle:horizontal{background:#58a6ff;width:12px;height:12px;margin:-4px 0;border-radius:6px;}QSlider::sub-page:horizontal{background:#58a6ff88;border-radius:2px;}")
        self._svol_lbl=QLabel(f"{self._svol.value()}%"); self._svol_lbl.setFixedWidth(44)
        self._svol.valueChanged.connect(lambda v: self._svol_lbl.setText(f"{v}%"))
        vrl.addWidget(self._svol,1); vrl.addWidget(self._svol_lbl)
        lay.addRow("Volume:",vol_row)

        # Duration spinbox
        self._sdur=QDoubleSpinBox(); self._sdur.setRange(0.2,30.0); self._sdur.setSingleStep(0.5)
        self._sdur.setSuffix(" sec"); self._sdur.setValue(cfg.sound_duration)
        lay.addRow("Sound duration:",self._sdur)

        self._dismiss=QSpinBox(); self._dismiss.setRange(0,60); self._dismiss.setValue(cfg.auto_dismiss_sec)
        lay.addRow("Auto-dismiss (sec):",self._dismiss)
        self._idle=QDoubleSpinBox(); self._idle.setRange(0.5,30); self._idle.setSingleStep(0.5)
        self._idle.setValue(cfg.idle_trigger_sec); lay.addRow("Idle trigger (sec):",self._idle)
        sep=QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setStyleSheet(f"color:{C_SURFACE.name()};"); lay.addRow(sep)
        self._auto_restart=QCheckBox("Auto-restart when AIDE.py is updated on disk")
        self._auto_restart.setChecked(auto_restart); lay.addRow(self._auto_restart)
        br=QWidget(); brl=QHBoxLayout(br); brl.setContentsMargins(0,0,0,0); brl.setSpacing(8)
        sb=QPushButton("Save"); _primary_btn(sb); tb=QPushButton("Test sound"); cb2=QPushButton("Cancel")
        sb.clicked.connect(self._save)
        tb.clicked.connect(self._test_sound)
        cb2.clicked.connect(self.reject)
        brl.addStretch(); brl.addWidget(sb); brl.addWidget(tb); brl.addWidget(cb2); lay.addRow(br)

    def _current_cfg(self)->NotifConfig:
        _dev_text=self._sdev.currentText().strip()
        _sound_dev="" if _dev_text in ("","System Default") else _dev_text
        return NotifConfig(enabled=self._enabled.isChecked(),style=self._style.currentText(),
            sound=self._sound.isChecked(),sound_command=self._scmd.text().strip(),
            sound_device=_sound_dev,sound_volume=self._svol.value()/100.0,
            sound_duration=self._sdur.value(),
            auto_dismiss_sec=self._dismiss.value(),idle_trigger_sec=self._idle.value(),
            patterns=self._cfg.patterns)

    def _test_sound(self):
        # Force-enable sound for the test regardless of the checkbox state
        test = self._current_cfg(); test.sound = True
        threading.Thread(target=play_sound,args=(test,),daemon=True).start()

    def _save(self):
        self._result=self._current_cfg(); self.accept()
    def get_config(self)->Optional[NotifConfig]: return self._result
    def get_auto_restart(self)->bool: return self._auto_restart.isChecked()

class NotifDetailDialog(QDialog):
    go_to_terminal=pyqtSignal(int)
    def __init__(self,tab_title:str,msg:str,context:str,tab_id:int,parent=None):
        super().__init__(parent); self.setWindowTitle(f"⚠  {msg}")
        self.setStyleSheet(_dlg_ss()); self.setFixedSize(680,460); self._tab_id=tab_id
        lay=QVBoxLayout(self); lay.setContentsMargins(0,0,0,12)
        hdr=QLabel(f"  ⚠  {msg}  —  {tab_title}"); hdr.setFixedHeight(34)
        hdr.setStyleSheet(f"background:{C_WARN.name()};color:#000;font-weight:bold;padding:0 12px;font-size:13px;")
        lay.addWidget(hdr)
        sub=QLabel("  Last output context (most recent at bottom):")
        sub.setStyleSheet(f"color:{C_MUTED.name()};padding:6px 12px 2px;font-size:11px;")
        lay.addWidget(sub)
        txt=QTextEdit(); txt.setReadOnly(True)
        txt.setStyleSheet(f"QTextEdit{{background:{C_BG.name()};color:{C_FG.name()};border:none;font-family:{FONT_FAMILY};font-size:12px;padding:8px;}}")
        lines=context.splitlines()[-80:]
        display=[("▶  "+l if re.search(r"\?\s*$",l) else "   "+l) for l in lines]
        txt.setPlainText("\n".join(display))
        sb=txt.verticalScrollBar(); sb.setValue(sb.maximum()); lay.addWidget(txt,1)
        br=QWidget(); brl=QHBoxLayout(br); brl.setContentsMargins(12,0,12,0); brl.setSpacing(8)
        gb=QPushButton("Go to Terminal"); _primary_btn(gb)
        cpb=QPushButton("Copy context"); db=QPushButton("Dismiss")
        gb.clicked.connect(self._go)
        cpb.clicked.connect(lambda: QApplication.clipboard().setText(context))
        db.clicked.connect(self.reject)
        brl.addStretch(); brl.addWidget(gb); brl.addWidget(cpb); brl.addWidget(db); lay.addWidget(br)
    def _go(self): self.go_to_terminal.emit(self._tab_id); self.accept()

# ═════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class SettingsDialog(QDialog):
    _KEYS=[("ANTHROPIC_API_KEY","Anthropic API Key"),("OPENAI_API_KEY","OpenAI API Key"),
           ("GOOGLE_API_KEY","Google / Gemini API Key"),("COHERE_API_KEY","Cohere API Key"),
           ("MISTRAL_API_KEY","Mistral API Key")]
    def __init__(self,config:"AppConfig",parent=None):
        super().__init__(parent); self.setWindowTitle("Settings")
        self.setStyleSheet(_dlg_ss()); self.setFixedWidth(520); self._result=None
        lay=QVBoxLayout(self); lay.setContentsMargins(20,20,20,20); lay.setSpacing(12)
        lay.addWidget(QLabel("<b>Shell</b>"))
        self._shell=QLineEdit(config.shell or DEFAULT_SHELL); lay.addWidget(self._shell)
        sep=QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{C_SURFACE.name()};"); lay.addWidget(sep)
        lay.addWidget(QLabel("<b>API Keys</b>  <span style='color:#7d8590;font-size:11px;'>applied to new terminals as env vars</span>"))
        form=QFormLayout(); form.setSpacing(8); self._key_fields={}
        for key,label in self._KEYS:
            field=QLineEdit(config.env_overrides.get(key,""))
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(f"env ${key} is used if blank")
            self._key_fields[key]=field; form.addRow(f"{label}:",field)
        lay.addLayout(form)
        bb=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        _primary_btn(bb.button(QDialogButtonBox.StandardButton.Save))
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject); lay.addWidget(bb)
    def _save(self):
        self._result={"shell":self._shell.text().strip(),
                      "env_overrides":{k:f.text().strip() for k,f in self._key_fields.items() if f.text().strip()}}
        self.accept()
    def get_result(self)->Optional[dict]: return self._result


class GitHubTokensDialog(QDialog):
    """Manage named GitHub tokens (PATs)."""
    def __init__(self, tokens: Dict[str, str], parent=None):
        super().__init__(parent); self.setWindowTitle("GitHub Tokens")
        self.setStyleSheet(_dlg_ss()); self.setFixedWidth(520); self._result = None
        lay = QVBoxLayout(self); lay.setContentsMargins(20, 20, 20, 20); lay.setSpacing(12)
        lay.addWidget(QLabel("<b>GitHub Tokens</b>  <span style='color:#7d8590;font-size:11px;'>"
                             "set as GITHUB_TOKEN &amp; GH_TOKEN env vars</span>"))
        self._rows: list = []
        self._form = QVBoxLayout(); self._form.setSpacing(6)
        lay.addLayout(self._form)
        for name, token in tokens.items():
            self._add_row(name, token)
        add_btn = QPushButton("+ Add token")
        add_btn.setStyleSheet(
            f"QPushButton{{background:{C_SURFACE.name()};color:{C_ACCENT.name()};border:none;"
            f"border-radius:4px;padding:6px 12px;font-size:12px;}}"
            f"QPushButton:hover{{background:{C_ACCENT.name()}22;}}")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(lambda: self._add_row("", ""))
        lay.addWidget(add_btn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        _primary_btn(bb.button(QDialogButtonBox.StandardButton.Save))
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def _add_row(self, name: str, token: str):
        row = QWidget()
        rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
        name_f = QLineEdit(name); name_f.setPlaceholderText("Name (e.g. work)")
        name_f.setFixedWidth(120)
        tok_f = QLineEdit(token); tok_f.setPlaceholderText("ghp_…")
        tok_f.setEchoMode(QLineEdit.EchoMode.Password)
        rm_btn = QPushButton("✕"); rm_btn.setFixedSize(24, 24)
        rm_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_MUTED.name()};border:none;font-size:11px;}}"
            f"QPushButton:hover{{color:#ff6b6b;}}")
        rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rm_btn.clicked.connect(lambda: self._remove_row(row))
        rl.addWidget(name_f); rl.addWidget(tok_f, 1); rl.addWidget(rm_btn)
        self._form.addWidget(row)
        self._rows.append((row, name_f, tok_f))

    def _remove_row(self, row):
        self._rows = [(r, n, t) for r, n, t in self._rows if r is not row]
        row.deleteLater()

    def _save(self):
        self._result = {}
        for _, name_f, tok_f in self._rows:
            n = name_f.text().strip()
            t = tok_f.text().strip()
            if n and t:
                self._result[n] = t
        self.accept()

    def get_result(self) -> Optional[Dict[str, str]]:
        return self._result


class PermissionDialog(QDialog):
    """Modal dialog shown when Claude Code requests permission for a tool call."""

    def __init__(self, tool_name: str, tool_input: dict,
                 terminal_name: str = "", received_at: float = 0.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Code — Permission Request")
        self.setStyleSheet(_dlg_ss())
        self.setFixedWidth(540)
        self._decision = "deny"  # "allow" | "always_session" | "always_all" | "deny"

        from PyQt6.QtWidgets import QPlainTextEdit
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        # ── header ────────────────────────────────────────────────────────────
        hdr_row = QHBoxLayout()
        hdr = QLabel(f"🛡️  {tool_name}")
        hdr.setStyleSheet(f"color:{C_ACCENT.name()};font-weight:bold;font-size:13px;"
                          f"background:transparent;")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()

        meta_parts = []
        if received_at:
            delta = time.time() - received_at
            meta_parts.append("just now" if delta < 2 else f"{int(delta)}s ago")
        if terminal_name:
            meta_parts.append(terminal_name)
        if meta_parts:
            meta = QLabel("  ·  ".join(meta_parts))
            meta.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;")
            hdr_row.addWidget(meta)
        lay.addLayout(hdr_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{C_SURFACE.name()};max-height:1px;")
        lay.addWidget(sep)

        # ── tool-aware content ────────────────────────────────────────────────
        lay.addWidget(self._build_detail(tool_name, tool_input))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background:{C_SURFACE.name()};max-height:1px;")
        lay.addWidget(sep2)

        # ── primary buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        allow_btn = QPushButton("✅  Allow")
        allow_btn.setStyleSheet(
            f"QPushButton{{background:#a6e3a1;color:#000;border:none;"
            f"border-radius:4px;font-size:12px;font-weight:bold;padding:6px 18px;}}"
            f"QPushButton:hover{{background:#b9f5b4;}}")
        allow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        allow_btn.setDefault(True)
        allow_btn.clicked.connect(lambda: self._decide("allow"))

        deny_btn = QPushButton("🚫  Deny")
        deny_btn.setStyleSheet(
            f"QPushButton{{background:#f38ba8;color:#000;border:none;"
            f"border-radius:4px;font-size:12px;font-weight:bold;padding:6px 18px;}}"
            f"QPushButton:hover{{background:#f5a5b8;}}")
        deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        deny_btn.clicked.connect(lambda: self._decide("deny"))

        btn_row.addWidget(allow_btn)
        btn_row.addWidget(deny_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # ── always-allow buttons ──────────────────────────────────────────────
        always_row = QHBoxLayout(); always_row.setSpacing(6)
        _always_ss = (f"QPushButton{{background:transparent;color:{C_MUTED.name()};"
                      f"border:1px solid {C_SURFACE.name()};border-radius:3px;"
                      f"font-size:10px;padding:3px 10px;}}"
                      f"QPushButton:hover{{color:{C_ACCENT.name()};"
                      f"border-color:{C_ACCENT.name()};}}")

        if terminal_name:
            btn_sess = QPushButton(f"📌  Always allow — {terminal_name}")
            btn_sess.setStyleSheet(_always_ss)
            btn_sess.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_sess.clicked.connect(lambda: self._decide("always_session"))
            always_row.addWidget(btn_sess)

        btn_all = QPushButton("📌  Always allow — all agents")
        btn_all.setStyleSheet(_always_ss)
        btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_all.clicked.connect(lambda: self._decide("always_all"))
        always_row.addWidget(btn_all)
        always_row.addStretch()

        hint = QLabel("  1 = Allow · 2 = Always allow · 3 = Deny")
        hint.setStyleSheet(f"color:{C_MUTED.name()};font-size:10px;background:transparent;")
        always_row.addWidget(hint)
        lay.addLayout(always_row)

        self._has_terminal = bool(terminal_name)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_1:
            self._decide("allow")
        elif key == Qt.Key.Key_2:
            self._decide("always_session" if self._has_terminal else "always_all")
        elif key == Qt.Key.Key_3:
            self._decide("deny")
        else:
            super().keyPressEvent(event)

    def _decide(self, decision: str):
        self._decision = decision
        self.accept() if decision != "deny" else self.reject()

    @property
    def decision(self) -> str: return self._decision

    @property
    def approved(self) -> bool: return self._decision != "deny"

    def _build_detail(self, tool_name: str, tool_input: dict) -> QWidget:
        from PyQt6.QtWidgets import QPlainTextEdit
        _code_ss = (f"QPlainTextEdit{{background:{C_BG.name()};color:{C_FG.name()};"
                    f"border:1px solid {C_SURFACE.name()};border-radius:4px;"
                    f"font-family:{FONT_FAMILY};font-size:11px;padding:6px;}}")

        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)

        def code_box(text, max_h=160):
            b = QPlainTextEdit(text); b.setReadOnly(True)
            b.setStyleSheet(_code_ss); b.setMaximumHeight(max_h)
            return b

        def lbl(text, col=None):
            l = QLabel(text)
            l.setStyleSheet(f"color:{col or C_MUTED.name()};font-size:11px;"
                            f"background:transparent;"); l.setWordWrap(True)
            return l

        if tool_name in ("Bash", "bash"):
            desc = tool_input.get("description", "")
            cmd  = tool_input.get("command", "")
            if desc: lay.addWidget(lbl(desc, C_FG.name()))
            lay.addWidget(code_box(cmd))

        elif tool_name in ("Edit", "MultiEdit"):
            fpath = tool_input.get("file_path", "")
            lay.addWidget(lbl(f"📄  {fpath}", C_ACCENT.name()))
            old = tool_input.get("old_string", "")
            new = tool_input.get("new_string", "")
            if old: lay.addWidget(lbl("Remove:", "#f38ba8")); lay.addWidget(code_box(old, 100))
            if new: lay.addWidget(lbl("Add:",    "#a6e3a1")); lay.addWidget(code_box(new, 100))

        elif tool_name == "Write":
            fpath   = tool_input.get("file_path", "")
            content = tool_input.get("content", "")
            lines   = content.splitlines()
            preview = "\n".join(lines[:30]) + ("\n…" if len(lines) > 30 else "")
            lay.addWidget(lbl(f"📄  {fpath}", C_ACCENT.name()))
            lay.addWidget(code_box(preview, 160))

        elif tool_name in ("Read", "Glob", "Grep", "LS"):
            key  = next((k for k in ("file_path", "pattern", "path") if k in tool_input), "")
            val  = tool_input.get(key, json.dumps(tool_input))
            lay.addWidget(lbl(f"📄  {val}", C_ACCENT.name()))

        elif tool_name in ("WebFetch", "web_fetch"):
            url    = tool_input.get("url", "")
            prompt = tool_input.get("prompt", "")
            lay.addWidget(lbl(f"🌐  {url}", C_ACCENT.name()))
            if prompt: lay.addWidget(lbl(prompt))

        elif tool_name in ("WebSearch", "web_search"):
            lay.addWidget(lbl(f"🔍  {tool_input.get('query', '')}", C_FG.name()))

        else:
            lines = []
            for k, v in tool_input.items():
                v_str = str(v)
                if len(v_str) > 120: v_str = v_str[:120] + "…"
                lines.append(f"{k}: {v_str}")
            lay.addWidget(code_box("\n".join(lines), 200))

        return w


class _RestoreDialog(QDialog):
    """List available session backups and let the user pick one to restore."""
    restore_requested = pyqtSignal(str)   # path to the chosen backup file

    def __init__(self, backups: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Restore Session from Backup")
        self.setStyleSheet(_dlg_ss())
        self.setFixedWidth(520)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        hdr = QLabel("Select a backup to restore:")
        hdr.setStyleSheet(f"color:{C_ACCENT.name()};font-weight:bold;font-size:13px;")
        lay.addWidget(hdr)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{C_BG.name()};color:{C_FG.name()};"
            f"border:1px solid {C_SURFACE.name()};border-radius:4px;"
            f"font-family:{FONT_FAMILY};font-size:12px;}}"
            f"QListWidget::item:selected{{background:{C_ACCENT.name()}33;color:{C_FG.name()};}}")
        self._list.setMinimumHeight(200)
        self._paths = []
        for p in backups:
            age = time.time() - p.stat().st_mtime
            size_kb = p.stat().st_size // 1024
            if age < 3600:
                age_s = f"{int(age/60)}m ago"
            elif age < 86400:
                age_s = f"{int(age/3600)}h ago"
            else:
                age_s = f"{int(age/86400)}d ago"
            self._list.addItem(f"{p.name}  ·  {size_kb} KB  ·  {age_s}")
            self._paths.append(str(p))
        self._list.setCurrentRow(0)
        lay.addWidget(self._list)

        btn_row = QHBoxLayout()
        restore_btn = QPushButton("Restore selected")
        restore_btn.setDefault(True)
        restore_btn.setStyleSheet(
            f"QPushButton{{background:{C_ACCENT.name()};color:#000;border:none;"
            f"border-radius:4px;font-size:12px;font-weight:bold;padding:6px 18px;}}"
            f"QPushButton:hover{{background:{C_ACCENT.name()}cc;}}")
        restore_btn.clicked.connect(self._on_restore)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            f"QPushButton{{background:{C_SURFACE.name()};color:{C_FG.name()};"
            f"border:none;border-radius:4px;font-size:12px;padding:6px 18px;}}"
            f"QPushButton:hover{{background:{C_SURFACE.name()}cc;}}")
        cancel_btn.clicked.connect(self.close)
        btn_row.addWidget(restore_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _on_restore(self):
        row = self._list.currentRow()
        if 0 <= row < len(self._paths):
            self.restore_requested.emit(self._paths[row])
            self.close()


class AIDEWindow(QMainWindow):
    _mcp_perm_signal = pyqtSignal(str, object)

    def __init__(self,shell:str=""):
        super().__init__()
        self.config=AppConfig.load()
        if shell: self.config.shell=shell
        self.sessions:Dict[int,TermSession]={}
        self._browsers:Dict[int,BrowsePane]={}
        self._next_id=0; self.active_id=-1
        self._split_mode="none"; self._secondary_id=-1
        self._split_picking=False
        self._focused_pane=0       # int index 0-5 — which pane last had keyboard focus
        self._num_panes=1          # number of visible panes (1-6)
        self._pane_ids=[-1,-1,-1,-1,-1,-1]  # session ID for each pane slot
        self._notes_vis=True; self._last_notif:Optional[tuple]=None
        self._cb=SharedClipboard()
        self._vault=SecureVault()
        self._script_path=Path(sys.argv[0]).resolve()
        try: self._script_mtime=self._script_path.stat().st_mtime
        except: self._script_mtime=0.0
        self._update_pending=False
        self._show_screenshot_overlay=True  # only the first _switch_to (during session restore) gets the overlay
        self._ball_overlay=SplitBallOverlay(self)
        self._ball_overlay.hide()
        self._perm_always_allow: Dict[str, set] = {}  # tool_name → {"all"|"session:{tid}"}
        self._neural_bus = NeuralBus(self._on_neural_request,
                                     on_permission=self._on_permission_request)
        self._mcp_perm_signal.connect(self._show_permission_dialog)
        self._neural_port = self._neural_bus.start()
        self._neural_client_dir = str(Path(sys.argv[0]).parent / "_neural_bin")
        write_client(self._neural_client_dir)
        self._write_mcp_config()
        self._build_ui(); _build_keymap()
        self._start_dashboard()
        self._hotkey_bar.set_btn_active("toggle_notes", self._notes_vis)
        self._hotkey_bar.set_btn_active("toggle_uber", self.config.uber_mode)
        self._hotkey_bar.set_btn_active("toggle_dashboard", True)
        for interval,fn in [(100,self._process_events),(1000,self._check_idle),
                             (500,self._refresh_cards),(1000,self._refresh_dashboard),
                             (30000,self._save_session),(5000,self._check_for_update)]:
            t=QTimer(self); t.timeout.connect(fn); t.start(interval)
        self._load_session()
        # Initial restore is done — any further tab switches must not flash
        # the previous-session screenshot overlay.
        self._show_screenshot_overlay=False
        if not self.sessions: self._new_tab()
        # Initialise brain card preview from existing file (if any)
        try:
            self._tab_bar.update_brain_preview(
                NEURAL_BRAIN_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        # Show What's New popup if AIDE.py was updated since last run.
        QTimer.singleShot(400, self._maybe_show_whats_new)

    def _build_ui(self):
        self.setWindowTitle(f"{APP_NAME} {VERSION}  —  AI Dev Env")
        self.resize(1280,800)
        self.setStyleSheet(f"QMainWindow{{background:{C_BG.name()};}}QMenuBar{{background:{C_PANEL.name()};color:{C_FG.name()};border-bottom:1px solid {C_SURFACE.name()};}}QMenuBar::item:selected{{background:{C_SURFACE.name()};}}QMenu{{background:{C_SURFACE.name()};color:{C_FG.name()};border:1px solid {C_MUTED.name()};}}QMenu::item:selected{{background:{C_ACCENT.name()}44;color:{C_ACCENT.name()};}}")
        # macOS already creates an "AIDE" application menu automatically.
        # Use MenuRole to slot our actions into it without creating a duplicate.
        from PyQt6.QtGui import QAction
        mb = self.menuBar()
        _app_m = mb.addMenu("_app")          # throwaway menu — roles move actions to system menu
        _backup_act = QAction("Backup Session Now", self)
        _backup_act.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        _backup_act.triggered.connect(self._action_backup_session)
        _app_m.addAction(_backup_act)
        _restore_act = QAction("Restore Session from Backup…", self)
        _restore_act.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        _restore_act.triggered.connect(self._action_restore_session)
        _app_m.addAction(_restore_act)
        _check_act = QAction("Check for Updates", self)
        _check_act.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        _check_act.triggered.connect(self._manual_check_update)
        _app_m.addAction(_check_act)
        _dash_act = QAction("Open Mobile Dashboard", self)
        _dash_act.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        _dash_act.triggered.connect(self._open_dashboard_browser)
        _app_m.addAction(_dash_act)
        _about_act = QAction(f"About {APP_NAME}", self)
        _about_act.setMenuRole(QAction.MenuRole.AboutRole)
        _about_act.triggered.connect(self._show_about)
        _app_m.addAction(_about_act)
        _quit_act = QAction("Quit", self)
        _quit_act.setMenuRole(QAction.MenuRole.QuitRole)
        _quit_act.triggered.connect(self.close)
        _app_m.addAction(_quit_act)

        central=QWidget(); self.setCentralWidget(central)
        root=QVBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        self._hotkey_bar=HotkeyBar()
        self._hotkey_bar.action_triggered.connect(self._dispatch_action)
        self._hotkey_bar.font_size_changed.connect(self._set_font_size)
        self._hotkey_bar.restart_requested.connect(self._do_restart)
        root.addWidget(self._hotkey_bar)
        self._info_bar=AIInfoBar(); self._info_bar.setVisible(False)
        mid=QWidget(); ml=QHBoxLayout(mid); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)
        self._tab_bar=TabBar()
        self._tab_bar.tab_selected.connect(self._on_tab_clicked)
        self._tab_bar.shift_tab_selected.connect(self._on_shift_tab_clicked)
        self._tab_bar.new_tab_clicked.connect(lambda: self._new_tab())
        self._tab_bar.rename_requested.connect(self._rename_tab_by_id)
        self._tab_bar.close_requested.connect(self._close_tab_with_confirm)
        self._tab_bar.tabs_reordered.connect(self._on_tabs_reordered)
        self._tab_bar.neural_toggle_requested.connect(self._on_neural_toggle)
        self._tab_bar.brain_clicked.connect(self._open_brain_editor)
        self._sidebar_splitter=QSplitter(Qt.Orientation.Horizontal)
        self._sidebar_splitter.setHandleWidth(3)
        self._sidebar_splitter.setStyleSheet(f"QSplitter::handle{{background:{C_SURFACE.name()};}}QSplitter::handle:hover{{background:{C_ACCENT.name()}44;}}")
        self._sidebar_splitter.addWidget(self._tab_bar)
        ml.addWidget(self._sidebar_splitter, 1)
        term_area=QWidget(); term_area.setStyleSheet(f"background:{C_BG.name()};")
        tv=QVBoxLayout(term_area); tv.setContentsMargins(0,0,0,0); tv.setSpacing(0)
        _back_bar=QWidget(); _back_bar.setFixedHeight(26)
        _back_bar.setStyleSheet(f"background:{C_PANEL.name()};border-bottom:1px solid {C_SURFACE.name()};")
        _bbl=QHBoxLayout(_back_bar); _bbl.setContentsMargins(6,2,6,2); _bbl.setSpacing(0)
        _back_btn=QPushButton("◀  Dashboard")
        _back_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_MUTED.name()};border:none;"
            f"font-size:11px;padding:2px 8px;}}"
            f"QPushButton:hover{{color:{C_ACCENT.name()};background:{C_SURFACE.name()};}}")
        _back_btn.clicked.connect(self._show_dashboard)
        _bbl.addWidget(_back_btn); _bbl.addStretch()
        tv.addWidget(_back_bar)
        self._notif_banner=NotifBanner(); tv.addWidget(self._notif_banner)
        # ── Outer vertical splitter: top row (panes 0+1) / bot row (panes 2+3) ──
        _splitter_ss=f"QSplitter::handle{{background:{C_SURFACE.name()};}}"
        self._outer_splitter=QSplitter(Qt.Orientation.Vertical)
        self._outer_splitter.setHandleWidth(2)
        self._outer_splitter.setStyleSheet(_splitter_ss)
        self._term_splitter=QSplitter(Qt.Orientation.Horizontal)
        self._term_splitter.setHandleWidth(2)
        self._term_splitter.setStyleSheet(_splitter_ss)
        self._bot_splitter=QSplitter(Qt.Orientation.Horizontal)
        self._bot_splitter.setHandleWidth(2)
        self._bot_splitter.setStyleSheet(_splitter_ss)
        self._bot_splitter.setVisible(False)
        self._outer_splitter.addWidget(self._term_splitter)
        self._outer_splitter.addWidget(self._bot_splitter)

        def _make_pane(idx: int):
            """Create a pane container with header + terminal; returns (widget, header_widget, header_label, terminal)."""
            pane=QWidget(); pane.setStyleSheet("background:transparent;")
            lay=QVBoxLayout(pane); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
            hdr_w=QWidget(); hdr_w.setFixedHeight(22)
            hdr_w.setStyleSheet(f"background:{C_SURFACE.name()};border-bottom:1px solid #30363d;")
            hdr_w.setVisible(False)
            hdr_lay=QHBoxLayout(hdr_w); hdr_lay.setContentsMargins(0,0,4,0); hdr_lay.setSpacing(0)
            hdr_lbl=QLabel()
            hdr_lbl.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;font-family:'JetBrains Mono',monospace;background:transparent;padding:0 8px;border:none;")
            hdr_lay.addWidget(hdr_lbl, 1)
            if idx > 0:
                close_btn=QPushButton("×"); close_btn.setFixedSize(16,16)
                close_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{C_MUTED.name()};border:none;font-size:13px;line-height:1;padding:0;}}QPushButton:hover{{color:#ff6b6b;}}")
                close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                close_btn.setToolTip("Close this pane")
                close_btn.clicked.connect(lambda _, i=idx: self._remove_pane(i))
                hdr_lay.addWidget(close_btn)
            term=TerminalWidget()
            term.prefix_action.connect(self._dispatch_action)
            term.split_tab_paste.connect(lambda text, i=idx: self._on_split_paste(i, text))
            lay.addWidget(hdr_w)
            lay.addWidget(term, 1)
            return pane, hdr_w, hdr_lbl, term

        # ── Pane 0 (main) ─────────────────────────────────────────────────────
        self._main_pane, self._main_header, self._main_header_lbl, self._main_terminal = _make_pane(0)
        self._main_terminal.sent_to_waiting.connect(self._auto_advance_to_next_waiting)
        self._term_splitter.addWidget(self._main_pane)

        # ── Pane 1 (secondary, in QStackedWidget to support browser tabs) ─────
        self._split_panel=QStackedWidget(); self._split_panel.setVisible(False)
        self._secondary_pane, self._secondary_header, self._secondary_header_lbl, self._secondary_terminal = _make_pane(1)
        self._split_panel.addWidget(self._secondary_pane)
        # per-tab browsers are lazily created in _get_or_create_browser()
        self._term_splitter.addWidget(self._split_panel)

        # ── Pane 2 (top row, 3rd column) ──────────────────────────────────────
        self._pane2_widget, self._pane2_header, self._pane2_header_lbl, self._pane2_terminal = _make_pane(2)
        self._pane2_widget.setVisible(False)
        self._term_splitter.addWidget(self._pane2_widget)

        # ── Panes 3, 4, 5 (bottom row) ────────────────────────────────────────
        self._pane3_widget, self._pane3_header, self._pane3_header_lbl, self._pane3_terminal = _make_pane(3)
        self._pane4_widget, self._pane4_header, self._pane4_header_lbl, self._pane4_terminal = _make_pane(4)
        self._pane5_widget, self._pane5_header, self._pane5_header_lbl, self._pane5_terminal = _make_pane(5)
        self._pane3_widget.setVisible(False)
        self._pane4_widget.setVisible(False)
        self._pane5_widget.setVisible(False)
        self._bot_splitter.addWidget(self._pane3_widget)
        self._bot_splitter.addWidget(self._pane4_widget)
        self._bot_splitter.addWidget(self._pane5_widget)

        # Convenience lists (index == pane index)
        self._terminals   =[self._main_terminal, self._secondary_terminal,
                            self._pane2_terminal, self._pane3_terminal,
                            self._pane4_terminal, self._pane5_terminal]
        self._pane_widgets=[self._main_pane, self._secondary_pane,
                            self._pane2_widget, self._pane3_widget,
                            self._pane4_widget, self._pane5_widget]
        self._pane_headers=[self._main_header, self._secondary_header,
                            self._pane2_header, self._pane3_header,
                            self._pane4_header, self._pane5_header]
        self._pane_header_lbls=[self._main_header_lbl, self._secondary_header_lbl,
                                self._pane2_header_lbl, self._pane3_header_lbl,
                                self._pane4_header_lbl, self._pane5_header_lbl]

        tv.addWidget(self._outer_splitter, 1)
        self._notes_panel=NotesPanel()
        self._notes_panel.vault_unlock_requested.connect(self._on_vault_unlock_requested)
        self._notes_panel.vault_lock_requested.connect(self._on_vault_lock_requested)
        self._notes_panel.github_token_changed.connect(self._on_gh_token_selected)
        self._notes_panel.claude_login_requested.connect(self._on_claude_login)
        # ── Center stack: page 0 = agent dashboard, page 1 = terminal area ───────
        self._center_stack = QStackedWidget()
        self._agent_table = AgentTable()
        self._agent_table.open_terminal.connect(self._dashboard_open_terminal)
        self._agent_table.new_agent.connect(lambda: self._new_tab())
        self._agent_table.launch_agent.connect(self._run_autostart)
        self._agent_table.send_message.connect(self._dashboard_send_message)
        self._agent_table.set_validation.connect(self._dashboard_set_validation)
        self._agent_table.run_task.connect(self._run_agent_task)
        self._center_stack.addWidget(self._agent_table)  # page 0
        self._center_stack.addWidget(term_area)           # page 1
        self._center_stack.setCurrentIndex(0)

        self._main_splitter=QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setHandleWidth(3)
        self._main_splitter.setStyleSheet(f"QSplitter::handle{{background:{C_SURFACE.name()};}}")
        self._main_splitter.addWidget(self._center_stack)
        self._main_splitter.addWidget(self._notes_panel)
        self._main_splitter.setStretchFactor(0,1); self._main_splitter.setStretchFactor(1,0)
        self._sidebar_splitter.addWidget(self._main_splitter)
        self._sidebar_splitter.setStretchFactor(0,0); self._sidebar_splitter.setStretchFactor(1,1)
        self._sidebar_splitter.setSizes([220, 1060])
        root.addWidget(mid,1)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        sb=self.statusBar(); sb.setFixedHeight(22)
        sb.setStyleSheet(f"QStatusBar{{background:{C_PANEL.name()};color:{C_MUTED.name()};border-top:1px solid {C_SURFACE.name()};font-family:{FONT_FAMILY};font-size:11px;padding:0 8px;}}QStatusBar::item{{border:none;}}")
        self._cwd_bar=QLabel(); self._cwd_bar.setStyleSheet(f"color:{C_MUTED.name()};font-size:11px;background:transparent;padding:0;")
        sb.addWidget(self._cwd_bar,1)

    # ── notes-panel / pane synchronisation ────────────────────────────────────
    def _focused_tid(self) -> int:
        """Session ID in the currently focused pane (-1 if no session assigned)."""
        return self._pane_ids[self._focused_pane]

    def _sync_notes_from_panel(self):
        """Save notes-panel content to the focused pane's session (if any)."""
        tid = self._focused_tid()
        if tid < 0 or tid not in self.sessions: return
        s = self.sessions[tid]
        s.notes = self._notes_panel.get_notes()
        s.tasks = self._notes_panel.get_tasks()
        s.autostart_dir = self._notes_panel.get_autostart_dir()
        s.autostart_cmd = self._notes_panel.get_autostart_cmd()
        s.claude_profile = self._notes_panel.get_claude_profile()
        s.claude_model   = self._notes_panel.get_claude_model()
        s.claude_args    = self._notes_panel.get_claude_args()
        if self._vault.is_unlocked():
            s.github_token_name = self._notes_panel.get_github_token_name()
        v = self._notes_panel.get_variables()
        if v is not None:
            s.variables = v; self._vault.set_vars(tid, v)

    def _sync_notes_to_panel(self, tid: int):
        """Load session tid's data into the notes panel."""
        if tid < 0 or tid not in self.sessions: return
        s = self.sessions[tid]
        token_names = list(self._vault.get_github_tokens().keys()) if self._vault.is_unlocked() else []
        self._notes_panel.set_github_token_names(token_names, s.github_token_name)
        self._notes_panel.load(s.notes, s.tasks, s.variables,
                               s.autostart_dir, s.autostart_cmd, s.github_token_name,
                               s.claude_profile, s.claude_model, s.claude_args)

    def _set_focused_pane(self, idx: int):
        """Change which pane is focused, syncing notes panel as needed."""
        if idx == self._focused_pane: return
        self._sync_notes_from_panel()
        self._focused_pane = idx
        self._sync_notes_to_panel(self._pane_ids[idx])

    def _set_pane_session(self, pane_idx: int, tid: int):
        """Assign session tid to pane pane_idx, refreshing the notes panel if needed."""
        if pane_idx == 0:
            self._switch_to(tid); return
        if tid not in self.sessions: return
        was_focused = (self._focused_pane == pane_idx)
        if was_focused: self._sync_notes_from_panel()
        self._pane_ids[pane_idx] = tid
        self._terminals[pane_idx].set_session(self.sessions[tid])
        if was_focused: self._sync_notes_to_panel(tid)
        self._tab_bar.set_active(self.active_id,
            next((self._pane_ids[i] for i in range(1,6) if self._pane_ids[i]>=0), -1))
        self._update_split_headers()
        self._terminals[pane_idx].setFocus()

    def _add_split_pane(self, tid: int):
        """Add tid as the next available pane (up to 6), or replace the focused pane."""
        if tid not in self.sessions: return
        # If already shown in any pane, just focus it
        for i in range(self._num_panes):
            if self._pane_ids[i] == tid:
                self._set_focused_pane(i); self._terminals[i].setFocus()
                self._update_split_headers(); return
        if self._num_panes < 6:
            new_idx = self._num_panes
            self._num_panes += 1
            self._pane_ids[new_idx] = tid
            self._terminals[new_idx].set_session(self.sessions[tid])
            self._terminals[new_idx].in_split = True
            tw = self._term_splitter.width() or 800
            if new_idx == 1:
                self._split_panel.setCurrentWidget(self._secondary_pane)
                self._split_panel.setVisible(True)
                self._term_splitter.setSizes([tw//2, tw//2, 0])
                threading.Thread(target=_tennis_serve_sound, daemon=True).start()
            elif new_idx == 2:
                # Complete top row: 3 equal columns
                self._pane2_widget.setVisible(True)
                self._term_splitter.setSizes([tw//3, tw//3, tw//3])
            elif new_idx == 3:
                # First pane in bottom row — split outer vertically
                self._pane3_widget.setVisible(True)
                self._bot_splitter.setVisible(True)
                self._bot_splitter.setSizes([tw, 0, 0])
                oh = self._outer_splitter.height()
                self._outer_splitter.setSizes([oh//2, oh//2])
            elif new_idx == 4:
                self._pane4_widget.setVisible(True)
                bw = self._bot_splitter.width() or tw
                self._bot_splitter.setSizes([bw//2, bw//2, 0])
            elif new_idx == 5:
                # Complete bottom row: 3 equal columns
                self._pane5_widget.setVisible(True)
                bw = self._bot_splitter.width() or tw
                self._bot_splitter.setSizes([bw//3, bw//3, bw//3])
            if not self.config.split_tip_shown and self._num_panes == 2:
                self.config.split_tip_shown = True; self.config.save()
                QTimer.singleShot(300, lambda: SplitTipDialog(self).exec())
            if self._split_mode != "browse": self._split_mode = "terminal"
        else:
            # All 6 panes used — replace the focused one
            self._set_pane_session(self._focused_pane, tid); return
        for i in range(self._num_panes): self._terminals[i].in_split = True
        self._tab_bar.set_active(self.active_id,
            next((self._pane_ids[i] for i in range(1,6) if self._pane_ids[i]>=0), -1))
        self._hotkey_bar.set_btn_active("split_term", self._num_panes > 1 and self._split_mode=="terminal")
        self._update_split_headers()
        self._update_status()

    def _remove_pane(self, pane_idx: int):
        """Close pane pane_idx (valid for idx 1-5). Shifts higher panes down so no holes."""
        if pane_idx < 1 or pane_idx >= self._num_panes: return
        for i in range(pane_idx, self._num_panes - 1):
            next_tid = self._pane_ids[i + 1]
            self._pane_ids[i] = next_tid
            self._terminals[i].set_session(self.sessions.get(next_tid) if next_tid >= 0 else None)
        last = self._num_panes - 1
        self._pane_ids[last] = -1
        self._terminals[last].set_session(None)
        if last == 1:
            self._split_panel.setVisible(False)
        else:
            self._pane_widgets[last].setVisible(False)
        self._num_panes -= 1
        if self._num_panes <= 3: self._bot_splitter.setVisible(False)
        if self._num_panes == 1: self._split_mode = "none"
        if self._focused_pane >= self._num_panes:
            self._focused_pane = 0
            self._sync_notes_to_panel(self._pane_ids[0])
        for i in range(6): self._terminals[i].in_split = (self._num_panes > 1)
        self._update_split_headers()
        self._update_status()

    # ── tab lifecycle ──────────────────────────────────────────────────────────
    def _claude_profile_dir(self, profile: str) -> str:
        """Resolve a profile name to an absolute CLAUDE_CONFIG_DIR path."""
        return str(Path.home() / ".aide" / "claude-profiles" / profile)

    def _env_with_vars(self, session: "TermSession") -> dict:
        """Merge config env_overrides, this session's GitHub token, vault variables, and neural bus."""
        env = dict(self.config.env_overrides)
        env["AIDE_NEURAL_URL"]        = f"http://127.0.0.1:{self._neural_port}"
        env["AIDE_SESSION_ID"]        = str(session.tab_id)
        env["AIDE_NEURAL_BRAIN_FILE"] = str(NEURAL_BRAIN_FILE)
        env["AIDE_PERMISSION_TOOL"]   = "mcp__aide__permission_prompt"
        env["PATH"]                   = f"{self._neural_client_dir}:{os.environ.get('PATH', '')}"
        # Inject this tab's GitHub token (env is set before autostart runs,
        # since autostart is sent as shell input after execvpe with env).
        name = getattr(session, "github_token_name", "")
        if name and self._vault.is_unlocked():
            token = self._vault.get_github_tokens().get(name, "")
            if token:
                env["GITHUB_TOKEN"] = token
                env["GH_TOKEN"] = token
        # Per-tab Claude profile → own CLAUDE_CONFIG_DIR
        prof = getattr(session, "claude_profile", "")
        if prof:
            d = self._claude_profile_dir(prof)
            Path(d).mkdir(parents=True, exist_ok=True)
            env["CLAUDE_CONFIG_DIR"] = d
        # Per-tab model selection (the claude wrapper and task dispatcher also add --model;
        # ANTHROPIC_MODEL is a fallback for any bare `claude` invocation in the shell)
        model = getattr(session, "claude_model", "")
        if model:
            env["ANTHROPIC_MODEL"] = model
        env.update(session.variables)   # vault vars take precedence
        return env

    def _new_tab(self, title: str = "") -> int:
        tid=self._next_id; self._next_id+=1; s=TermSession(tid)
        if title: s.custom_title=title
        if self._vault.is_unlocked():
            s.variables = self._vault.get_vars(tid)
        self.sessions[tid]=s; s.start(self.config.shell or DEFAULT_SHELL, self._env_with_vars(s))
        self._tab_bar.add_card(s,self.config.card); self._switch_to(tid)
        self._run_autostart(tid)
        return tid

    @staticmethod
    def _model_flag(s: "TermSession") -> str:
        """Return a --model flag string for session *s*, or empty string."""
        m = (s.claude_model or "").strip()
        return f" --model {m}" if m else ""

    def _run_autostart(self, tid: int, delay_ms: int = 800):
        """Execute the autostart command + cd for session *tid* after *delay_ms* ms."""
        s = self.sessions.get(tid)
        if not s: return
        cmd = (s.autostart_cmd or "").strip()
        d   = (s.autostart_dir or "").strip()
        if not cmd and not d: return
        # Inject --model into bare `claude` invocations if a model is selected
        model_flag = self._model_flag(s)
        if model_flag and re.match(r"claude(\s|$)", cmd) and "--model" not in cmd:
            cmd = "claude" + model_flag + cmd[6:]
        payload = b""
        gh_exports = self._gh_token_exports(s)
        if gh_exports:
            payload += f"stty -echo; {gh_exports} stty echo\n".encode("utf-8")
        if d:   payload += f"cd {shlex.quote(d)}\n".encode("utf-8")
        if cmd: payload += f"{cmd}\n".encode("utf-8")
        if not payload: return
        QTimer.singleShot(delay_ms, lambda t=tid, p=payload: (
            self.sessions[t].write(p) if t in self.sessions else None))

    # ── Agent dashboard integration ────────────────────────────────────────────

    def _session_data(self, s: "TermSession") -> dict:
        status = "idle"
        if s.claude_working:   status = "working"
        elif s.claude_thinking: status = "thinking"
        elif s.waiting_input:  status = "waiting"
        resume_cmd = s.claude_resume_cmd or ""
        session_id = ""
        if m := re.search(r"--resume\s+([a-zA-Z0-9_-]+)", resume_cmd):
            session_id = m.group(1)
        return {
            "tid":                s.tab_id,
            "name":               s.effective_title(),
            "status":             status,
            "last_active":        s.last_out_time,
            "tags":               list(s.tags),
            "dir":                s.autostart_dir or s.info.cwd_full or s.info.cwd or "",
            "cmd":                s.claude_resume_cmd or s.autostart_cmd or "",
            "session_id":         session_id,
            "profile":            s.claude_profile or "",
            "model":              s.claude_model or "",
            "tokens_used":        s.tokens_used,
            "pending_validation": s.pending_validation,
            "validation_note":    s.validation_note,
        }

    def _refresh_dashboard(self):
        data = [self._session_data(s) for s in self.sessions.values()]
        self._agent_table.refresh(data)
        self._update_dock_badge(data)

    @staticmethod
    def _update_dock_badge(data: list):
        if not IS_MAC:
            return
        waiting   = sum(1 for s in data if s.get("status") == "waiting")
        validate  = sum(1 for s in data if s.get("pending_validation"))
        total     = waiting + validate
        label     = str(total) if total else ""
        try:
            from AppKit import NSApplication
            NSApplication.sharedApplication().dockTile().setBadgeLabel_(label)
        except Exception:
            pass

    def _show_dashboard(self):
        self._center_stack.setCurrentIndex(0)
        self._hotkey_bar.set_btn_active("toggle_dashboard", True)

    def _show_terminal_view(self):
        self._center_stack.setCurrentIndex(1)
        self._hotkey_bar.set_btn_active("toggle_dashboard", False)

    def _action_toggle_dashboard(self):
        if self._center_stack.currentIndex() == 0:
            self._show_terminal_view()
        else:
            self._show_dashboard()

    def _dashboard_open_terminal(self, tid: int):
        if tid >= 0 and tid in self.sessions:
            self._switch_to(tid)
        self._show_terminal_view()

    def _dashboard_send_message(self, tid: int, msg: str):
        s = self.sessions.get(tid)
        if s and s.alive:
            s.write(msg.encode("utf-8"))

    def _dashboard_set_validation(self, tid: int, note: str, enabled: bool):
        s = self.sessions.get(tid)
        if not s: return
        s.pending_validation = enabled
        s.validation_note    = note

    def _run_agent_task(self, tid: int, task: str):
        """Run claude non-interactively (-p) for *task* in session tid.

        Uses --continue to resume the most recent session for the working
        directory so context is preserved across successive task dispatches.
        Claude exits after the task completes, freeing memory.
        """
        s = self.sessions.get(tid)
        if not s or not s.alive: return
        d    = (s.autostart_dir or "").strip()
        args = self._model_flag(s)
        if s.claude_args:
            args += f" {s.claude_args}"
        payload = b""
        gh_exports = self._gh_token_exports(s)
        if gh_exports:
            payload += f"stty -echo; {gh_exports} stty echo\n".encode("utf-8")
        if d:
            payload += f"cd {shlex.quote(d)}\n".encode("utf-8")
        safe_task = task.replace("'", "'\\''")
        # prefer explicit --resume <id> if known; fall back to --continue
        resume_base = s.claude_resume_cmd or "claude --continue"
        payload += f"{resume_base}{args} -p '{safe_task}'\n".encode("utf-8")
        def _write(t=tid, p=payload):
            sess = self.sessions.get(t)
            if sess: sess.write(p)
        QTimer.singleShot(200, _write)

    def _close_tab(self,tid:int):
        if len(self.sessions)<=1: return
        self._neural_bus.unregister(tid)
        self.sessions[tid].kill(); del self.sessions[tid]
        self._tab_bar.remove_card(tid)
        self._vault.drop_tab(tid)
        if (bp := self._browsers.pop(tid, None)):
            self._split_panel.removeWidget(bp); bp.deleteLater()
        # Remove from any split pane (idx 1-5)
        for i in range(1, 6):
            if self._pane_ids[i] == tid:
                self._remove_pane(i)
        if self.active_id==tid: self._switch_to(next(iter(self.sessions)))

    def _switch_to(self,tid:int):
        if tid not in self.sessions: return
        # Clear unread marker when user switches to a tab
        if card:=self._tab_bar._card_map.get(tid): card._clear_unread()
        if tid != self.active_id:
            idx = list(self.sessions.keys()).index(tid) if tid in self.sessions else 0
            try: threading.Thread(target=_ping_pong_sound, args=(idx,), daemon=True).start()
            except: pass
        # Save current notes panel data to whichever session is focused in the panel.
        # When pane 0 is focused, that's active_id; otherwise the notes panel belongs
        # to a different pane whose session is NOT changing here.
        if self._focused_pane == 0:
            self._sync_notes_from_panel()
        elif self.active_id >= 0 and self.active_id in self.sessions:
            # Pane 0 is not focused — still capture screenshot before changing its session
            pass
        # Capture screenshot of pane 0 before leaving
        if self.active_id >= 0 and self.active_id in self.sessions and self.active_id != tid:
            px = self._main_terminal.grab()
            if not px.isNull():
                self.sessions[self.active_id]._screenshot = px
                try:
                    SCREENSHOTS_DIR.mkdir(exist_ok=True)
                    px.save(str(SCREENSHOTS_DIR / f"tab_{self.active_id}.png"))
                except: pass
        self.active_id=tid; self._pane_ids[0]=tid; s=self.sessions[tid]
        self._main_terminal.set_session(s)
        w=self._main_terminal.width(); h=self._main_terminal.height()
        if w>0 and h>0:
            s.resize(max(1,w//self._main_terminal._cw),max(1,h//self._main_terminal._ch))
        # Only update notes panel if pane 0 is the focused one
        if self._focused_pane == 0:
            self._sync_notes_to_panel(tid)
        split_secondary = next((self._pane_ids[i] for i in range(1,6) if self._pane_ids[i]>=0), -1)
        self._tab_bar.set_active(tid, split_secondary if self._split_mode=="terminal" else -1)
        self._main_terminal.update(); self._update_status()
        # Show the previous-session screenshot overlay only on the first
        # _switch_to after launch (during _load_session). User-initiated tab
        # switches skip the overlay entirely so it isn't disruptive.
        px = None
        if self._show_screenshot_overlay:
            px = getattr(s, '_screenshot', None)
            if px is None:
                path = SCREENSHOTS_DIR / f"tab_{tid}.png"
                if path.exists():
                    px = QPixmap(str(path))
                    if px.isNull(): px = None
                    else: s._screenshot = px
        if px is not None:
            self._main_terminal.show_screenshot(px)
        else:
            self._main_terminal.setFocus()
        if self._split_mode=="browse":
            bp = self._get_or_create_browser(tid)
            self._split_panel.setCurrentWidget(bp)
            # only navigate if the per-tab browser has no URL yet
            if not bp._url.text().strip():
                url = s.browser_url or s.info.local_url
                if url: bp.navigate(url)

    def switch_to_index(self,n:int):
        ids=list(self.sessions.keys())
        if 0<=n<len(ids): self._switch_to(ids[n])

    # ── split view ─────────────────────────────────────────────────────────────
    def _set_split(self, mode: str):
        """Enter a layout mode: 'none' collapses all extra panes, 'terminal' adds pane 1,
        'browse' opens a browser pane.  Calling with the current mode toggles off."""
        prev_mode = self._split_mode
        if mode == self._split_mode and mode in ("browse",): mode = "none"
        if mode != prev_mode:
            if mode != "none" and prev_mode == "none":
                threading.Thread(target=_tennis_serve_sound, daemon=True).start()
            elif mode == "none" and prev_mode != "none":
                threading.Thread(target=_tennis_point_sound, daemon=True).start()
        if mode == "none":
            self._split_mode = "none"
            # Hide and detach all extra panes
            for i in range(5, 0, -1):
                if self._pane_ids[i] >= 0 or i < self._num_panes:
                    self._pane_ids[i] = -1
                    self._terminals[i].set_session(None)
                    if i == 1:
                        self._split_panel.setVisible(False)
                    else:
                        self._pane_widgets[i].setVisible(False)
            self._bot_splitter.setVisible(False)
            self._secondary_id = -1
            self._num_panes = 1
            # Restore notes panel to pane 0 if it was on another pane
            if self._focused_pane != 0:
                self._focused_pane = 0
                self._sync_notes_to_panel(self._pane_ids[0])
            for t in self._terminals: t.in_split = False
            self._terminals[0].setFocus()
        elif mode == "terminal":
            self._split_mode = "terminal"
            if self._secondary_id < 0 or self._secondary_id not in self.sessions:
                self._secondary_id = self._create_secondary()
            else:
                self._terminals[1].set_session(self.sessions[self._secondary_id])
            self._pane_ids[1] = self._secondary_id
            self._num_panes = max(self._num_panes, 2)
            self._split_panel.setCurrentWidget(self._secondary_pane)
            self._split_panel.setVisible(True)
            total = self._term_splitter.width()
            self._term_splitter.setSizes([total // 2, total // 2])
            for i in range(self._num_panes): self._terminals[i].in_split = True
            if not self.config.split_tip_shown:
                self.config.split_tip_shown = True; self.config.save()
                QTimer.singleShot(300, lambda: SplitTipDialog(self).exec())
        elif mode == "browse":
            self._split_mode = "browse"
            bp = self._get_or_create_browser(self.active_id)
            self._split_panel.setCurrentWidget(bp); self._split_panel.setVisible(True)
            total = self._term_splitter.width()
            self._term_splitter.setSizes([total * 6 // 10, total * 4 // 10])
            s = self.sessions.get(self.active_id)
            if s and not bp._url.text().strip():
                url = s.browser_url or s.info.local_url
                if url: bp.navigate(url)
        self._hotkey_bar.set_btn_active("split_browse", self._split_mode == "browse")
        self._hotkey_bar.set_btn_active("split_term", self._num_panes > 1 and self._split_mode == "terminal")
        self._update_split_headers()
        self._update_status()

    def _create_secondary(self) -> int:
        tid = self._next_id; self._next_id += 1; s = TermSession(tid); s.custom_title = "(split)"
        self.sessions[tid] = s; s.start(self.config.shell or DEFAULT_SHELL, self.config.env_overrides)
        self._tab_bar.add_card(s, self.config.card)
        self._terminals[1].set_session(s); return tid

    # Header wrapper (QWidget) background styles
    _HDR_FOCUSED   = f"background:{C_ACCENT.name()};border-bottom:1px solid {C_ACCENT.name()};"
    _HDR_UNFOCUSED = f"background:{C_SURFACE.name()};border-bottom:1px solid #30363d;"
    _HDR_WAITING   = f"background:#1f2d3d;border-bottom:1px solid {C_ACCENT.name()};"
    # Header label (QLabel) text styles
    _LBL_FOCUSED   = f"color:#000;font-weight:600;font-size:11px;font-family:'JetBrains Mono',monospace;background:transparent;padding:0 8px;border:none;"
    _LBL_UNFOCUSED = f"color:{C_MUTED.name()};font-size:11px;font-family:'JetBrains Mono',monospace;background:transparent;padding:0 8px;border:none;"
    _LBL_WAITING   = f"color:{C_ACCENT.name()};font-weight:700;font-size:11px;font-family:'JetBrains Mono',monospace;background:transparent;padding:0 8px;border:none;"
    _GEAR_FRAMES   = ("◐","◓","◑","◒")

    def _header_indicator(self, session) -> str:
        """Return a short status prefix for the pane header based on Claude state."""
        if not session: return ""
        if getattr(session, "waiting_input", False):
            i = getattr(self, "_blink_phase_i", 0)
            return "?" if i % 2 == 0 else " "
        if getattr(session, "claude_working", False) or getattr(session, "claude_thinking", False):
            frame = self._GEAR_FRAMES[getattr(self, "_blink_phase_i", 0) % len(self._GEAR_FRAMES)]
            return f"{frame}⚙"
        return ""

    def _update_split_headers(self):
        """Show/update the name labels above each pane in terminal split mode."""
        active = self._num_panes > 1 and self._split_mode == "terminal"
        # Advance spinner frame on each refresh when active
        if active:
            self._blink_phase_i = getattr(self, "_blink_phase_i", 0) + 1
        _shortcuts = ["⌘1", "⌘2", "⌘3", "⌘4", "⌘5", "⌘6"]

        def _label(session, shortcut):
            title = session.effective_title() if session else "—"
            ind = self._header_indicator(session)
            return f"  {shortcut}  {ind + '  ' if ind else ''}{title}"

        def _lbl_style(session, is_focused):
            if getattr(session, "waiting_input", False): return self._LBL_WAITING
            return self._LBL_FOCUSED if is_focused else self._LBL_UNFOCUSED

        def _hdr_style(session, is_focused):
            if getattr(session, "waiting_input", False): return self._HDR_WAITING
            return self._HDR_FOCUSED if is_focused else self._HDR_UNFOCUSED

        for i, (hdr, lbl, term) in enumerate(zip(self._pane_headers, self._pane_header_lbls, self._terminals)):
            visible = active and i < self._num_panes
            hdr.setVisible(visible)
            if visible:
                sess = term.session
                new_text  = _label(sess, _shortcuts[i])
                new_lsty  = _lbl_style(sess, i == self._focused_pane)
                new_hsty  = _hdr_style(sess, i == self._focused_pane)
                if lbl.text() != new_text:   lbl.setText(new_text)
                if lbl.styleSheet() != new_lsty: lbl.setStyleSheet(new_lsty)
                if hdr.styleSheet() != new_hsty: hdr.setStyleSheet(new_hsty)

    def _on_focus_changed(self, _old, new):
        """Track which split pane last received keyboard focus."""
        if self._num_panes <= 1: return
        w = new
        while w:
            for idx, term in enumerate(self._terminals):
                if w is term and idx < self._num_panes:
                    if idx != self._focused_pane:
                        old_idx = self._focused_pane
                        self._sync_notes_from_panel()
                        self._focused_pane = idx
                        self._sync_notes_to_panel(self._pane_ids[idx])
                    self._update_split_headers()
                    return
            w = w.parent() if callable(getattr(w, "parent", None)) else None

    def _on_split_paste(self, src_idx: int, text: str):
        """Tab-paste from pane src_idx. With 2 panes auto-sends; with 3+ shows a picker menu."""
        if self._num_panes <= 1: return
        if self._num_panes == 2:
            dst_idx = 1 - src_idx  # the only other pane
            self._do_split_paste(src_idx, dst_idx, text)
            return
        # 3+ panes: show a popup menu to pick the target
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{C_SURFACE.name()};color:{C_FG.name()};"
            f"border:1px solid {C_ACCENT.name()}44;border-radius:4px;padding:2px;}}"
            f"QMenu::item{{padding:5px 14px;font-size:12px;font-family:{FONT_FAMILY};}}"
            f"QMenu::item:selected{{background:{C_ACCENT.name()}33;color:{C_ACCENT.name()};}}"
        )
        menu.setTitle("Send to pane…")
        actions = {}
        for i in range(self._num_panes):
            if i == src_idx: continue
            s = self._terminals[i].session
            label = f"⌘{i+1}  {s.effective_title() if s else f'Pane {i+1}'}"
            act = menu.addAction(label)
            actions[act] = i
        src_w = self._terminals[src_idx]
        pos = src_w.mapToGlobal(src_w.rect().center())
        chosen = menu.exec(pos)
        if chosen and chosen in actions:
            self._do_split_paste(src_idx, actions[chosen], text)

    def _do_split_paste(self, src_idx: int, dst_idx: int, text: str):
        src_s = self._terminals[src_idx].session
        dst_s = self._terminals[dst_idx].session
        if not dst_s: return
        dst_s.scroll_offset = 0
        sender = src_s.effective_title() if src_s else "other pane"
        payload = f"# incoming from [{sender}]\n{text}"
        dst_s.write(payload.encode("utf-8"))
        self._terminals[dst_idx].setFocus()
        src_w = self._terminals[src_idx]; dst_w = self._terminals[dst_idx]
        src_c = src_w.mapTo(self, src_w.rect().center())
        dst_c = dst_w.mapTo(self, dst_w.rect().center())
        self._ball_overlay.launch(QPointF(src_c), QPointF(dst_c))
        threading.Thread(target=_smash_sound, daemon=True).start()

    def _swap_focus(self):
        if self._num_panes <= 1 and self._split_mode != "browse": return
        threading.Thread(target=_ping_pong_sound, args=(99,), daemon=True).start()
        if self._split_mode == "browse":
            if self._main_terminal.hasFocus():
                bp = self._browsers.get(self.active_id)
                if bp: bp._url.setFocus()
            else: self._main_terminal.setFocus()
        else:
            next_idx = (self._focused_pane + 1) % self._num_panes
            self._terminals[next_idx].setFocus()



    # ── event queue ────────────────────────────────────────────────────────────
    def _process_events(self):
        while True:
            try: ev=_EVENT_Q.get_nowait()
            except queue.Empty: break
            if ev[0]=="notif": self._show_notif(ev[1],ev[2],ev[3])
            elif ev[0]=="neural_msg":
                self._on_neural_delivered(ev[1], ev[2], ev[3])
            elif ev[0]=="blink":
                QApplication.alert(self,3000)
                if self.config.uber_mode:
                    self._uber_focus(ev[1])
            elif ev[0]=="github_update" and not self._update_pending:
                self._update_pending=True
                remote_ver=ev[1]
                self._hotkey_bar.mark_update_available(True, remote_ver)
                threading.Thread(target=_macos_notify,
                    args=(f"Update available: v{remote_ver}",
                          f"AIDE v{VERSION} → v{remote_ver}. Click ↻ Update to install."),
                    daemon=True).start()
            elif ev[0]=="git_up_to_date":
                QMessageBox.information(self,"Check for Updates",
                    f"{APP_NAME} v{VERSION} is up to date.")

    def _show_notif(self,tid:int,msg:str,ctx:str):
        s=self.sessions.get(tid)
        if not s or not self.config.notif.enabled: return
        threading.Thread(target=play_sound,args=(self.config.notif,),daemon=True).start()
        # macOS system notification — fires for any tab so user is alerted
        # even when AIDE isn't the focused app.
        if not self.isActiveWindow() or tid != self.active_id:
            threading.Thread(target=_macos_notify,
                             args=(s.effective_title(), msg), daemon=True).start()
        if tid==self.active_id: return
        full=f"{s.effective_title()}: {msg}"; self._last_notif=(s,msg,ctx)
        style=self.config.notif.style
        if style in ("banner","both"):
            self._notif_banner.show_msg(full,self.config.notif.auto_dismiss_sec)

    def _on_neural_request(self, msg):
        """Called from NeuralBus thread. Marshal to main thread with full payload."""
        _EVENT_Q.put(("neural_msg", msg.from_session, msg.to_session, msg.content))

    # ── MCP permission prompt ─────────────────────────────────────────────────

    def _on_permission_request(self, perm_id: str, args: dict):
        """Called from NeuralBus/MCP thread — stamps timestamp, then crosses to Qt main thread."""
        payload = dict(args)
        payload["_received_at"] = time.time()
        self._mcp_perm_signal.emit(perm_id, payload)

    def _find_requesting_terminal(self):
        """Best-effort: find the terminal whose Claude is currently working."""
        working = [(tid, s) for tid, s in self.sessions.items()
                   if s.claude_working or s.claude_thinking]
        if len(working) == 1:
            return working[0]
        ftid = self._focused_tid()
        if ftid >= 0 and ftid in self.sessions:
            return ftid, self.sessions[ftid]
        return -1, None

    def _show_permission_dialog(self, perm_id: str, args: object):
        tool_name   = args.get("tool_name", "unknown")
        tool_input  = args.get("tool_input", {})
        received_at = args.get("_received_at", 0.0)

        tab_id, session = self._find_requesting_terminal()
        terminal_name = session.effective_title() if session else ""

        # Check always-allow rules before showing the dialog
        scopes = self._perm_always_allow.get(tool_name, set())
        if "all" in scopes or (tab_id >= 0 and f"session:{tab_id}" in scopes):
            self._neural_bus.resolve_permission(perm_id, True)
            return

        dlg = PermissionDialog(
            tool_name=tool_name, tool_input=tool_input,
            terminal_name=terminal_name, received_at=received_at,
            parent=self,
        )
        dlg.exec()

        decision = dlg.decision
        approved = decision != "deny"
        if decision == "always_session" and tab_id >= 0:
            self._perm_always_allow.setdefault(tool_name, set()).add(f"session:{tab_id}")
        elif decision == "always_all":
            self._perm_always_allow.setdefault(tool_name, set()).add("all")

        self._neural_bus.resolve_permission(perm_id, approved)

    def _write_mcp_config(self):
        settings_path = Path.home() / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cfg = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        except Exception:
            cfg = {}
        mcp_servers = cfg.setdefault("mcpServers", {})
        mcp_servers["aide"] = {
            "type": "sse",
            "url":  f"http://127.0.0.1:{self._neural_port}/mcp/sse",
        }
        settings_path.write_text(json.dumps(cfg, indent=2))

    def _on_neural_delivered(self, from_sid: int, to_sid: int, content: str):
        """Injects neural message into target terminal PTY and animates the rail."""
        sender_name = self._neural_bus.sender_name(from_sid)
        targets = [to_sid] if to_sid != -1 else [
            s.tab_id for s in self.sessions.values() if s.neural_on_bus and s.tab_id != from_sid
        ]
        safe = content.replace("\n", "\n# ")
        for tid in targets:
            s = self.sessions.get(tid)
            if not s or not s.alive: continue
            payload = f"# 🤖 neural from [{sender_name}]: {safe}\n"
            s.write(payload.encode("utf-8"))
            self._tab_bar.animate_neural_rail(from_sid, tid)
            threading.Thread(target=_blop_sound, daemon=True).start()

    def _on_neural_toggle(self, tid: int):
        """Context-menu 'Join/Leave Neural Bus' → open the unified Terminal Settings dialog."""
        self._rename_tab_by_id(tid)

    def _check_idle(self):
        now=time.time()
        # Decay working/thinking flags — clear them if no spinner output has
        # arrived in the last _AI_IDLE_SECS seconds.
        needs_refresh=False
        for s in self.sessions.values():
            if (s.claude_working or s.claude_thinking) and s._ai_active_time>0:
                if now - s._ai_active_time >= TermSession._AI_IDLE_SECS:
                    s.claude_working=False; s.claude_thinking=False
                    if not s.waiting_input:
                        s.waiting_input=True
                        s.last_ping_time=now; s.last_waiting_at=now
                        _EVENT_Q.put(("blink",s.tab_id,"Claude is waiting"))
                        _EVENT_Q.put(("notif",s.tab_id,"Claude is waiting",s._output_tail))
                    needs_refresh=True
        if needs_refresh:
            self._refresh_cards()
        if not self.config.notif.enabled: return
        thr=self.config.notif.idle_trigger_sec
        for tid,s in self.sessions.items():
            if tid==self.active_id or not s.watching: continue
            if not s._notif_armed or s.last_out_time<=0: continue
            if now-s.last_out_time>=thr:
                s._notif_armed=False; self._show_notif(tid,"may be waiting for input",s._output_tail)

    def _refresh_cards(self):
        self._blink_phase=not getattr(self,"_blink_phase",False)
        split_ids = set(self._pane_ids[i] for i in range(1,6) if self._pane_ids[i]>=0) \
                    if self._split_mode=="terminal" else set()
        for tid,s in self.sessions.items():
            card=self._tab_bar._card_map.get(tid)
            if card:
                if s.waiting_input:
                    card._blink_phase=self._blink_phase
                if s.claude_thinking or s.claude_working:
                    card._gear_tick=getattr(card,"_gear_tick",0)+1
                card.mark_visible(tid in split_ids)
            self._tab_bar.refresh_card(tid)
        self._update_waiting_badge()
        self._update_split_headers()
        s=self.sessions.get(self.active_id)
        if s:
            full=s.info.cwd_full or s.info.cwd
            cur = s.screen.cursor
            new_cwd = (f"📁  {full}" if full else "") + f"  {cur.y+1}:{cur.x+1}"
            if self._cwd_bar.text() != new_cwd:
                self._cwd_bar.setText(new_cwd)

    def _update_waiting_badge(self):
        count=sum(1 for s in self.sessions.values() if getattr(s,"waiting_input",False))
        if getattr(self, "_last_wait_count", -1) == count: return
        self._last_wait_count = count
        base=f"{APP_NAME} v{VERSION}  —  AI Dev Env"
        self.setWindowTitle(f"[{count} waiting]  {base}" if count else base)
        try: QApplication.instance().setBadgeNumber(count)
        except: pass

    # ── actions ────────────────────────────────────────────────────────────────
    def _dispatch_action(self,action:str):
        if action.startswith("goto_"): self.switch_to_index(int(action[5:])-1); return
        if action.startswith("focus_pane_"):
            n = int(action[11:]) - 1  # 0-indexed
            if 0 <= n < self._num_panes:
                self._set_focused_pane(n)
                self._terminals[n].setFocus()
                self._update_split_headers()
            return
        if fn:=getattr(self,f"_action_{action}",None): fn()

    def _close_tab_with_confirm(self,tid:int):
        if len(self.sessions)<=1: return
        s=self.sessions.get(tid)
        name=s.effective_title() if s else f"Terminal {tid}"
        reply=QMessageBox.question(self,f"Close {name}",
            f'Close terminal "{name}"?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply==QMessageBox.StandardButton.Yes: self._close_tab(tid)

    def _action_new_tab(self): self._new_tab(f"Terminal {len(self.sessions)+1}")
    def _action_close_tab(self): self._close_tab_with_confirm(self.active_id)

    def _action_clear_line(self):
        """Clear whatever the user has typed on the current shell input line.
        Sends Ctrl+E (move to end-of-line) followed by Ctrl+U (kill to start)
        which works for bash, zsh, fish, and most readline-based REPLs."""
        if self.active_id<0 or self.active_id not in self.sessions: return
        s=self.sessions[self.active_id]
        s.scroll_offset=0
        s.write(b"\x05\x15")

    def _on_tabs_reordered(self,order:list):
        new_sessions={}
        for tid in order:
            if tid in self.sessions: new_sessions[tid]=self.sessions[tid]
        for tid,s in self.sessions.items():
            if tid not in new_sessions: new_sessions[tid]=s
        self.sessions=new_sessions

    # ── Mobile dashboard ──────────────────────────────────────────────────────
    def _start_dashboard(self):
        try:
            self._dashboard=DashboardServer(
                DASHBOARD_PORT,
                get_sessions=lambda: self.sessions,
                send_cb=self._dashboard_send,
            )
            self._dashboard.start()
            ip=local_ip()
            self._tab_bar.set_dashboard_url(f"{ip}:{DASHBOARD_PORT}")
        except OSError:
            self._tab_bar.set_dashboard_url("port busy")

    def _dashboard_send(self, tab_id: int, text: str):
        s=self.sessions.get(tab_id)
        if s and s.alive: s.write((text+"\n").encode())

    def _open_dashboard_browser(self):
        ip=local_ip(); webbrowser.open(f"http://{ip}:{DASHBOARD_PORT}")

    def _auto_advance_to_next_waiting(self):
        """After sending to a waiting terminal, jump to the next one that is waiting."""
        ids = list(self.sessions.keys())
        if self.active_id not in ids: return
        cur = ids.index(self.active_id)
        # First pass: find the next terminal waiting for input
        for i in range(1, len(ids)):
            tid = ids[(cur + i) % len(ids)]
            if tid != self.active_id and getattr(self.sessions[tid], "waiting_input", False):
                threading.Thread(target=_blop_sound, daemon=True).start()
                QTimer.singleShot(200, lambda t=tid: self._switch_to(t))
                return
        # No waiting terminal found — settle on the next one that is working/thinking
        for i in range(1, len(ids)):
            tid = ids[(cur + i) % len(ids)]
            s = self.sessions[tid]
            if tid != self.active_id and (getattr(s, "claude_working", False) or getattr(s, "claude_thinking", False)):
                QTimer.singleShot(200, lambda t=tid: self._switch_to(t))
                return

    def _sidebar_ids(self) -> list:
        """Session IDs in sidebar visual order (matches what the user sees)."""
        ids = [c.session.tab_id for c in self._tab_bar._cards() if c.session]
        # Fall back to sessions dict order if tab bar is empty
        return ids or list(self.sessions.keys())

    def _action_next_tab(self):
        if self._num_panes > 1:
            nxt = (self._focused_pane + 1) % self._num_panes
            self._set_focused_pane(nxt); self._terminals[nxt].setFocus()
            self._update_split_headers(); return
        ids = self._sidebar_ids()
        if ids:
            idx = ids.index(self.active_id) if self.active_id in ids else 0
            self._switch_to(ids[(idx+1) % len(ids)])

    def _action_prev_tab(self):
        if self._num_panes > 1:
            prv = (self._focused_pane - 1) % self._num_panes
            self._set_focused_pane(prv); self._terminals[prv].setFocus()
            self._update_split_headers(); return
        ids = self._sidebar_ids()
        if ids:
            idx = ids.index(self.active_id) if self.active_id in ids else 0
            self._switch_to(ids[(idx-1) % len(ids)])

    def _action_rename_tab(self):
        self._rename_tab_by_id(self.active_id)

    def _rename_tab_by_id(self, tid: int):
        if tid < 0 or tid not in self.sessions: return
        s = self.sessions[tid]
        url = f"http://127.0.0.1:{self._neural_port}"
        dlg = TerminalConfigDialog(s, url, self)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        new_name = dlg.get_new_name()
        if new_name is not None:
            s.custom_title = new_name
            self._tab_bar.refresh_card(tid, force=True)
            self._update_status()
        nr = dlg.get_neural_result()
        if nr is False:
            self._neural_bus.unregister(tid); s.neural_on_bus = False; s._neural_profile = None
            self._tab_bar.refresh_card(tid, force=True)
        elif isinstance(nr, dict):
            task = "  |  ".join(filter(None, [nr["app"], nr["role"], nr["task"]]))
            self._neural_bus.register(tid, nr["name"], task, extras=nr)
            s.neural_on_bus = True; s._neural_profile = nr
            self._tab_bar.refresh_card(tid, force=True)

    def _open_brain_editor(self):
        try:
            content = NEURAL_BRAIN_FILE.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        dlg = NeuralBrainDialog(content, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._tab_bar.update_brain_preview(dlg.get_content())

    def _maybe_show_whats_new(self):
        """Show the What's New dialog when the version has changed since last run."""
        try:
            current_mtime = self._script_path.stat().st_mtime
        except Exception:
            return
        version_changed = self.config.last_seen_version != VERSION
        mtime_changed   = current_mtime != self.config.last_seen_mtime
        if not (version_changed or mtime_changed):
            return
        prev_version = self.config.last_seen_version
        if version_changed and prev_version:
            self._backup_session_data(prev_version)
        self.config.last_seen_mtime    = current_mtime
        self.config.last_seen_version  = VERSION
        self.config.save()
        sections = _whats_new_entries(prev_version)
        if sections:
            dlg = WhatsNewDialog(sections, prev_version, self)
            dlg.exec()

    def _backup_session_data(self, from_version: str):
        """Write versioned snapshots of session.json and neural_brain.md."""
        import shutil
        tag = from_version.replace(".", "-")
        pairs = [
            (SESSION_FILE,      CONFIG_DIR / f"session.backup-{tag}.json"),
            (NEURAL_BRAIN_FILE, CONFIG_DIR / f"neural_brain.backup-{tag}.md"),
        ]
        for src, dst in pairs:
            try:
                if src.exists():
                    shutil.copy2(src, dst)
            except Exception:
                pass

    def _action_backup_session(self):
        """Manual on-demand backup with a timestamp tag."""
        import shutil
        tag = time.strftime("manual-%Y-%m-%d-%H-%M")
        self._sync_notes_from_panel()
        self._save_session()
        pairs = [
            (SESSION_FILE,      CONFIG_DIR / f"session.backup-{tag}.json"),
            (NEURAL_BRAIN_FILE, CONFIG_DIR / f"neural_brain.backup-{tag}.md"),
        ]
        backed = []
        for src, dst in pairs:
            try:
                if src.exists():
                    shutil.copy2(src, dst)
                    backed.append(dst.name)
            except Exception as e:
                _log_err(f"backup failed: {e}")
        if backed:
            QMessageBox.information(
                self, "Backup complete",
                "Saved to ~/.aide/:\n" + "\n".join(backed))

    def _action_restore_session(self):
        """Show available session backups and restore the selected one."""
        import shutil
        backups = sorted(CONFIG_DIR.glob("session.backup-*.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if not backups:
            QMessageBox.information(self, "No backups", "No session backups found in ~/.aide/")
            return
        dlg = _RestoreDialog(backups, self)
        dlg.restore_requested.connect(self._do_restore_session)
        dlg.show()

    def _do_restore_session(self, backup_path: str):
        """Replace session.json with *backup_path* and restart the session."""
        import shutil
        reply = QMessageBox.question(
            self, "Restore session",
            f"Replace the current session with:\n{Path(backup_path).name}\n\n"
            "AIDE will restart after restoring.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.copy2(backup_path, SESSION_FILE)
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e))
            return
        self._do_restart()

    def _check_for_update(self):
        if getattr(self, "_git_update_checked", False): return
        self._git_update_checked = True
        threading.Thread(target=self._check_github_update, daemon=True).start()

    def _check_github_update(self, manual: bool = False):
        try:
            req = Request(GITHUB_RAW_URL, headers={"User-Agent": f"AIDE/{VERSION}"})
            with urlopen(req, timeout=10) as r:
                chunk = r.read(300).decode("utf-8", errors="ignore")
            m = re.search(r'VERSION\s*=\s*"([^"]+)"', chunk)
            if not m:
                return
            remote_ver = m.group(1)
            if _ver_tuple(remote_ver) > _ver_tuple(VERSION):
                _EVENT_Q.put(("github_update", remote_ver))
            elif manual:
                _EVENT_Q.put(("git_up_to_date", None))
        except Exception:
            pass

    def _manual_check_update(self):
        """Triggered from the AIDE menu → Check for Updates."""
        self._update_pending = False
        self._git_update_checked = False
        self._hotkey_bar.mark_update_available(False)
        threading.Thread(target=self._check_github_update, args=(True,), daemon=True).start()

    def _show_about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{VERSION}<br>"
            f"AI Dev Env — Native Desktop Terminal<br><br>"
            f"<a href='https://github.com/gitayg/aide'>github.com/gitayg/aide</a><br><br>"
            f"Licensed under the "
            f"<a href='https://www.gnu.org/licenses/agpl-3.0.html'>GNU Affero GPL v3.0</a> "
            f"or later. See the LICENSE file for full terms.")

    def _do_restart(self):
        self._save_session()
        try:
            req = Request(GITHUB_RAW_URL, headers={"User-Agent": f"AIDE/{VERSION}"})
            with urlopen(req, timeout=30) as r:
                data = r.read()
            self._script_path.write_bytes(data)
        except Exception as e:
            QMessageBox.warning(self, "Update Failed", f"Could not download update:\n{e}")
            return
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _action_toggle_notes(self):
        self._notes_vis=not self._notes_vis; self._notes_panel.setVisible(self._notes_vis)
        self._hotkey_bar.set_btn_active("toggle_notes",self._notes_vis)

    def _action_focus_notes(self):
        if not self._notes_vis:
            self._notes_vis=True; self._notes_panel.setVisible(True)
            self._hotkey_bar.set_btn_active("toggle_notes",True)
        self._notes_panel.focus_editor()

    def _set_font_size(self,size:int):
        for t in self._terminals: t.set_font_size(size)

    def _uber_focus(self, tid: int):
        """Uber mode: auto-focus the terminal that just got a question from Claude."""
        if tid not in self.sessions: return
        # If already the active focused terminal, nothing to do
        if tid == self.active_id and self._focused_pane == 0: return
        threading.Thread(target=_blop_sound, daemon=True).start()
        # If the session is visible in a split pane, focus that pane
        for i in range(self._num_panes):
            if self._pane_ids[i] == tid:
                self._set_focused_pane(i)
                self._terminals[i].setFocus()
                self._update_split_headers()
                return
        # Otherwise switch the active tab
        self._switch_to(tid)

    def _action_toggle_uber(self):
        self.config.uber_mode = not self.config.uber_mode
        self.config.save()
        self._hotkey_bar.set_btn_active("toggle_uber", self.config.uber_mode)

    def _action_toggle_watch(self):
        if self.active_id<0: return
        s=self.sessions[self.active_id]; s.watching=not s.watching
        self._tab_bar.refresh_card(self.active_id); self._update_status()

    def _action_split_term(self):
        # Already in terminal split → close all extra panes (toggle off).
        if self._num_panes > 1 and self._split_mode == "terminal":
            self._set_split("none")
            return
        # Cancel picking mode if user clicks Split again.
        if self._split_picking:
            self._split_picking=False
            self._update_split_picking_ui()
            return
        # Only one tab open — nothing to split with, fall back to creating a new secondary.
        if len(self.sessions) < 2:
            self._set_split("terminal")
            return
        # Enter picking mode: the next clicked tab becomes the split partner.
        self._split_picking=True
        self._update_split_picking_ui()

    def _action_split_browse(self): self._set_split("browse")

    def _on_tab_clicked(self, tid: int):
        """Tab-bar click handler. Routes to split-pick, focused-pane replace, or normal switch."""
        if self._split_picking:
            self._split_picking=False
            self._update_split_picking_ui()
            if tid in self.sessions and tid != self.active_id:
                self._secondary_id=tid
                self._set_split("terminal")
            return
        # If the clicked terminal is already visible in another pane, swap it with
        # the focused pane instead of navigating to it in the focused pane.
        if self._split_mode == "terminal" and tid in self.sessions:
            for i in range(self._num_panes):
                if self._pane_ids[i] == tid and i != self._focused_pane:
                    focused_tid = self._pane_ids[self._focused_pane]
                    self._set_pane_session(self._focused_pane, tid)
                    self._set_pane_session(i, focused_tid)
                    return
        # In split-terminal mode: clicking a card replaces whichever pane is focused
        if self._split_mode == "terminal" and tid in self.sessions:
            if self._focused_pane > 0:
                self._set_pane_session(self._focused_pane, tid)
                return
        self._switch_to(tid)

    def _on_shift_tab_clicked(self, tid: int):
        """Shift+click → add as a new split pane (up to 4), or replace the focused pane."""
        if tid not in self.sessions: return
        self._add_split_pane(tid)

    def _update_split_picking_ui(self):
        if self._split_picking:
            self._hotkey_bar.set_btn_active("split_term", True)
            self._hotkey_bar.update_info("⊟  Click a terminal in the sidebar to split with…  (click Split again to cancel)")
        else:
            self._hotkey_bar.set_btn_active("split_term", self._num_panes > 1 and self._split_mode=="terminal")
            self._update_status()
    def _action_split_focus(self):  self._swap_focus()

    def _action_copy_screen(self):
        if self.active_id<0: return
        self._cb.push(self.sessions[self.active_id].screen_text())
        self._notif_banner.show_msg("Screen copied to shared clipboard",3)

    def _action_clipboard_menu(self):
        dlg=ClipboardDialog(self._cb,self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            text=dlg.get_text()
            if text and self.active_id>=0:
                self.sessions[self.active_id].write(text.encode("utf-8"))
                self._main_terminal.setFocus()

    def _action_show_notif_detail(self):
        if not self._last_notif: return
        s,msg,ctx=self._last_notif
        dlg=NotifDetailDialog(s.effective_title(),msg,ctx,s.tab_id,self)
        dlg.go_to_terminal.connect(self._switch_to); dlg.exec()

    def _action_configure_cards(self):
        dlg=CardConfigDialog(self.config.card,self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            result=dlg.get_result()
            if result:
                fields,show_tags,dedup_tags=result
                self.config.card.fields=fields or self.config.card.fields
                self.config.card.show_tags=show_tags
                self.config.card.dedup_tags=dedup_tags
                self.config.save()
                for tid in self.sessions:
                    if c:=self._tab_bar._card_map.get(tid): c.cfg=self.config.card; c.refresh()
                self._tab_bar.rebuild_layout(self._tab_bar._sessions)

    def _action_open_settings(self): self._open_settings()

    def _open_settings(self):
        dlg=SettingsDialog(self.config,self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            r=dlg.get_result()
            if r:
                self.config.shell=r["shell"]; self.config.env_overrides=r["env_overrides"]
                self.config.save(); self._info_bar._refresh()

    def _on_claude_login(self):
        """User clicked 'claude /login' in notes panel — export CLAUDE_CONFIG_DIR
        for this tab's profile and run `claude /login` in the live shell."""
        self._sync_notes_from_panel()
        tid = self._focused_tid()
        if tid < 0 or tid not in self.sessions: return
        s = self.sessions[tid]
        if not s.alive:
            QMessageBox.warning(self, "claude /login", "This terminal is not alive."); return
        args = f" {s.claude_args}" if s.claude_args else ""
        if s.claude_profile:
            d = self._claude_profile_dir(s.claude_profile)
            Path(d).mkdir(parents=True, exist_ok=True)
            cmd = f'export CLAUDE_CONFIG_DIR={shlex.quote(d)}; claude /login{args}\n'
        else:
            cmd = f'claude /login{args}\n'
        s.write(cmd.encode("utf-8"))

    def _on_gh_token_selected(self, name: str):
        """User picked a new token in the notes panel — persist + re-export into live shell."""
        # The notes panel always reflects the focused pane's session
        tid = self._focused_tid()
        if tid < 0 or tid not in self.sessions: return
        s = self.sessions[tid]
        if s.github_token_name == name: return
        s.github_token_name = name
        # Persist immediately so the selection survives an unexpected exit.
        self._save_session()
        if s.alive:
            exports = self._gh_token_exports(s)
            if not exports and self._vault.is_unlocked():
                exports = "unset GITHUB_TOKEN GH_TOKEN; "
            if exports:
                try: s.write(f"\nstty -echo; {exports} stty echo\n".encode())
                except Exception: pass

    def _action_github_tokens(self):
        if not self._vault.is_unlocked():
            self._on_vault_unlock_requested()
            if not self._vault.is_unlocked(): return
        tokens = self._vault.get_github_tokens()
        dlg = GitHubTokensDialog(tokens, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_tokens = dlg.get_result()
            if new_tokens is not None:
                self._vault.set_github_tokens(new_tokens)
                self._vault.flush()
                # Clear per-tab selections that reference removed tokens
                for s in self.sessions.values():
                    if s.github_token_name and s.github_token_name not in new_tokens:
                        s.github_token_name = ""
                # Refresh the notes-panel selector for the focused pane
                ftid = self._focused_tid()
                if ftid in self.sessions:
                    self._notes_panel.set_github_token_names(
                        list(new_tokens.keys()),
                        self.sessions[ftid].github_token_name)

    def _action_configure_notifs(self):
        dlg=NotifConfigDialog(self.config.notif,self.config.auto_restart,self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            cfg=dlg.get_config()
            if cfg:
                self.config.notif=cfg
                self.config.auto_restart=dlg.get_auto_restart()
                self.config.save()

    def _on_browser_url_changed(self, url: str):
        if self.active_id in self.sessions:
            self.sessions[self.active_id].browser_url = url

    def _get_or_create_browser(self, tid: int) -> "BrowsePane":
        bp = self._browsers.get(tid)
        if bp is None:
            bp = BrowsePane()
            bp.url_changed.connect(lambda u, t=tid: self._on_browser_url_changed_for(t, u))
            self._split_panel.addWidget(bp)
            self._browsers[tid] = bp
        return bp

    def _on_browser_url_changed_for(self, tid: int, url: str):
        if tid in self.sessions:
            self.sessions[tid].browser_url = url

    def _update_status(self):
        s=self.sessions.get(self.active_id)
        if not s:
            self._hotkey_bar.update_info(f"{APP_NAME} v{VERSION}  —  click any command or use Ctrl+B prefix")
            self._cwd_bar.setText("")
            return
        parts=[]
        if s.watching: parts.append("👁")
        if self._num_panes > 1 or self._split_mode == "browse":
            parts.append(s.effective_title())
        if s.info.ssh_host:  parts.append(f"⬡ {s.info.ssh_host}")
        if s.info.local_url: parts.append(f"🌐 {s.info.local_url}")
        if self._num_panes > 1: parts.append(f"[{self._num_panes} panes]")
        elif self._split_mode=="browse": parts.append("[split: browse]")
        self._hotkey_bar.update_info("  ".join(parts))
        full=s.info.cwd_full or s.info.cwd
        cur = s.screen.cursor
        pos_str = f"  {cur.y+1}:{cur.x+1}"
        self._cwd_bar.setText((f"📁  {full}" if full else "") + pos_str)

    # ── encrypted variables vault ──────────────────────────────────────────────
    def _on_vault_unlock_requested(self):
        """Unlock the vault by fetching its key from the macOS login Keychain.

        On first use this generates a random key, stores it in the Keychain
        (macOS will show its native auth prompt), and creates an empty vault
        file. On subsequent runs, macOS either returns the key instantly
        (if the user chose "Always Allow") or shows the login-password /
        Touch ID prompt before releasing it.
        """
        if self._vault.is_unlocked(): return
        try:
            ok = self._vault.unlock()
        except VaultKeyUnavailable as e:
            QMessageBox.warning(self,"🔒  Unlock Vault",
                f"Could not read the vault key from the login Keychain.\n\n"
                f"{e}\n\nMake sure you approved the macOS authentication prompt.")
            return
        except Exception as e:
            QMessageBox.critical(self,"🔒  Unlock Vault",
                f"Unexpected error while unlocking vault:\n\n{e}")
            return
        if not ok:
            QMessageBox.critical(self,"🔒  Unlock Vault",
                "The vault file exists but could not be decrypted with the key "
                "stored in your Keychain. This usually means the Keychain entry "
                "was replaced or the file was copied from another machine.\n\n"
                "Delete ~/.aide/vault.enc to start fresh (you will lose the "
                "previously stored variables).")
            return
        self._after_vault_unlocked()

    def _after_vault_unlocked(self):
        # Populate in-memory variables for every session from the vault.
        # Vault vars go to all sessions; GH token only to the focused session
        # (other terminals get it when the user explicitly selects it per-terminal).
        ftid = self._focused_tid()
        for tid,s in self.sessions.items():
            s.variables=self._vault.get_vars(tid)
            self._inject_vars_into_shell(s, include_gh=(tid == ftid))
        self._notes_panel.set_vault_unlocked(True)
        # Refresh the focused pane's table + GitHub token selector
        if ftid >= 0 and ftid in self.sessions:
            self._notes_panel.apply_variables(self.sessions[ftid].variables)
            self._notes_panel.set_github_token_names(
                list(self._vault.get_github_tokens().keys()),
                self.sessions[ftid].github_token_name)

    def _gh_token_exports(self, s: "TermSession") -> str:
        """Return `export GITHUB_TOKEN=…; export GH_TOKEN=…; ` for this session, or ''."""
        name = getattr(s, "github_token_name", "")
        if not name or not self._vault.is_unlocked(): return ""
        tok = self._vault.get_github_tokens().get(name, "")
        if not tok: return ""
        return f"export GITHUB_TOKEN={tok!r}; export GH_TOKEN={tok!r}; "

    def _inject_vars_into_shell(self, s: "TermSession", include_gh: bool = True):
        """Silently export vault variables into an already-running shell.

        Uses stty -echo so the export commands don't appear in the terminal
        display and therefore aren't visible to any AI reading the screen.
        """
        if not s.alive: return
        exports = "".join(
            f"export {k}={v!r};" for k, v in s.variables.items() if k.isidentifier()
        )
        if include_gh:
            exports += self._gh_token_exports(s)
        if not exports: return
        # Suppress echo → run exports → restore echo, all in one write
        payload = f"\nstty -echo; {exports} stty echo\n"
        try:
            s.write(payload.encode())
        except Exception:
            pass

    def _on_vault_lock_requested(self):
        # Capture current UI edits before locking (from whichever pane is in the notes panel)
        ftid = self._focused_tid()
        if ftid >= 0 and ftid in self.sessions:
            v=self._notes_panel.get_variables()
            if v is not None:
                self.sessions[ftid].variables=v
                self._vault.set_vars(ftid,v)
        # Persist and sync all tabs
        for tid,s in self.sessions.items():
            self._vault.set_vars(tid,s.variables)
        self._vault.flush()
        # Wipe in-memory copies and lock
        for s in self.sessions.values(): s.variables={}
        self._vault.lock()
        self._notes_panel.set_vault_unlocked(False)
        self._notes_panel.set_github_token_names([], "")

    # ── persistence ────────────────────────────────────────────────────────────
    def _save_session(self):
        # Flush the notes panel into whichever session it's currently showing
        self._sync_notes_from_panel()
        if self.active_id>=0 and self.active_id in self.sessions:
            bp=self._browsers.get(self.active_id)
            if bp: self.sessions[self.active_id].browser_url=bp._url.text().strip()
        data={"tabs":{str(k):v.to_dict() for k,v in self.sessions.items()},
              "active":self.active_id,"next_id":self._next_id,
              "order":self._tab_bar.get_full_order()}
        try: SESSION_FILE.write_text(json.dumps(data,indent=2))
        except: pass
        # Flush vault (no-op if locked; otherwise re-encrypts with current key)
        if self._vault.is_unlocked():
            for tid,sess in self.sessions.items():
                self._vault.set_vars(tid,sess.variables)
            self._vault.flush()

    def _load_session(self):
        log_file = CONFIG_DIR / "app.log"
        def _log(msg):
            try: log_file.open("a").write(f"[session] {msg}\n")
            except: pass
        def _log_err(msg): _log(f"ERROR: {msg}")
        _log(f"loading from {SESSION_FILE}")
        try:
            raw=SESSION_FILE.read_text()
        except FileNotFoundError: _log("session file not found"); return
        except Exception as e: _log_err(f"read error: {e}"); return
        try:
            data=json.loads(raw)
        except Exception as e: _log_err(f"parse error: {e}"); return
        # Keep a rolling backup of the last good session file
        try: (CONFIG_DIR/"session.json.bak").write_text(raw)
        except: pass
        shell=self.config.shell or DEFAULT_SHELL
        # One-time scrub: older sessions stored variables in cleartext here.
        _had_cleartext=False
        for v in data.get("tabs",{}).values():
            if isinstance(v,dict) and "variables" in v:
                v.pop("variables",None); _had_cleartext=True
        if _had_cleartext:
            try: SESSION_FILE.write_text(json.dumps(data,indent=2))
            except: pass
        tabs = data.get("tabs", {})
        _log(f"found {len(tabs)} tabs")
        for k,v in tabs.items():
            try:
                tid=int(k); s=TermSession.from_dict(v,tid)
                self.sessions[tid]=s; self._next_id=max(self._next_id,tid+1)
                s.start(shell, self._env_with_vars(s)); self._tab_bar.add_card(s,self.config.card)
                _log(f"loaded tab {k}: {s.custom_title!r}")
            except Exception as e:
                _log_err(f"tab {k} load failed: {e}")
        _log(f"loaded {len(self.sessions)} sessions total")
        # Restore full order
        saved_order = data.get("order", [])
        if saved_order:
            self._tab_bar.set_full_order(saved_order)
            self._tab_bar.rebuild_layout(self._tab_bar._sessions)
        # Restore Neural bus registrations
        for tid, s in self.sessions.items():
            p = getattr(s, "_neural_profile", None)
            if p:
                task = "  |  ".join(filter(None, [p.get("app",""), p.get("role",""), p.get("task","")]))
                self._neural_bus.register(tid, p.get("name", s.effective_title()), task, extras=p)
                s.neural_on_bus = True
        active=data.get("active",-1)
        target=active if active in self.sessions else (next(iter(self.sessions)) if self.sessions else -1)
        if target>=0: self._switch_to(target)

    def closeEvent(self,event):
        working=[s for s in self.sessions.values() if s.claude_working]
        if working:
            names=", ".join(s.effective_title() for s in working[:3])
            if len(working)>3: names+=f" and {len(working)-3} more"
            mb=QMessageBox(self)
            mb.setWindowTitle("Agents Still Working")
            mb.setText(f"These agents are still working:\n{names}\n\nClose AIDE anyway?")
            mb.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
            mb.setDefaultButton(QMessageBox.StandardButton.No)
            if mb.exec()!=QMessageBox.StandardButton.Yes:
                event.ignore(); return
        self._save_session()
        if self._vault.is_unlocked(): self._vault.flush()
        for s in self.sessions.values(): s.kill()
        # Clean up temp image files created during this session
        try:
            import tempfile, shutil
            tmp_dir = Path(tempfile.gettempdir()) / "aide_images"
            if tmp_dir.exists():
                shutil.rmtree(str(tmp_dir), ignore_errors=True)
        except Exception:
            pass
        event.accept()

# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def _dark_palette()->QPalette:
    p=QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor("#161b22"))
    p.setColor(QPalette.ColorRole.WindowText,      QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Base,            QColor("#0d1117"))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor("#161b22"))
    p.setColor(QPalette.ColorRole.Text,            QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Button,          QColor("#21262d"))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor("#e6edf3"))
    p.setColor(QPalette.ColorRole.Highlight,       QColor("#1f6feb"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link,            QColor("#58a6ff"))
    return p

if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser(description=f"{APP_NAME} {VERSION} — AI Dev Env")
    parser.add_argument("--shell",help="Shell to use (default: $SHELL)")
    parser.add_argument("--reset",action="store_true",help="Clear saved session")
    args=parser.parse_args()
    if args.reset:
        for f in (SESSION_FILE,CLIP_FILE): f.unlink(missing_ok=True)
        print(f"{APP_NAME}: session cleared."); sys.exit(0)
    # Set macOS app name and Dock icon explicitly — when Python is exec'd
    # from the .app bundle the Dock would otherwise show the Python rocket icon.
    try:
        from Foundation import NSBundle
        from AppKit import NSApplication, NSImage
        NSBundle.mainBundle().infoDictionary()['CFBundleName'] = APP_NAME
        # Locate AIDE.icns: prefer the bundle's Resources dir, fall back to
        # a path relative to this script (dev mode).
        _bundle_res = NSBundle.mainBundle().resourcePath()
        _icns = Path(_bundle_res) / "AIDE.icns" if _bundle_res else None
        if not _icns or not _icns.exists():
            _icns = Path(__file__).parent / "AIDE.app" / "Contents" / "Resources" / "AIDE.icns"
        if _icns and _icns.exists():
            _ns_app = NSApplication.sharedApplication()
            _img = NSImage.alloc().initWithContentsOfFile_(str(_icns))
            if _img:
                _ns_app.setApplicationIconImage_(_img)
    except Exception:
        pass
    app=QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    # Also set via Qt for the window icon / taskbar
    try:
        from PyQt6.QtGui import QIcon
        _icns_qt = CONFIG_DIR.parent / ".aide" / "AIDE.icns"  # won't exist, use bundle path
        _icns_path = Path(sys.argv[0]).parent.parent / "Resources" / "AIDE.icns"
        if _icns_path.exists():
            app.setWindowIcon(QIcon(str(_icns_path)))
    except Exception:
        pass
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    win=AIDEWindow(shell=args.shell or "")
    win.show()
    sys.exit(app.exec())
