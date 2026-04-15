# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2024-2026 Itay Glick. Licensed under the AGPL-3.0-or-later.
"""AIDE mobile dashboard — lightweight HTTP server, no external deps."""
from __future__ import annotations

import json, re, socket, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

_ANSI = re.compile(r"\x1b(?:\[[0-9;]*[A-Za-z]|[@-_][0-?]*[ -/]*[@-~])")

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<meta name="theme-color" content="#0d1117">
<title>AIDE</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --surface:#1c2128; --active:#1f2d3d;
  --fg:#e6edf3; --muted:#8b949e; --accent:#58a6ff;
  --green:#3fb950; --amber:#d29922; --red:#f85149;
  --border:#30363d; --inner:#21262d;
  --mono:"SF Mono","Fira Code",Menlo,monospace;
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;font-size:13px;line-height:1.4;min-height:100vh;-webkit-font-smoothing:antialiased}

/* ── Header ── */
.hdr{background:var(--panel);border-bottom:1px solid var(--border);padding:10px 12px;display:flex;align-items:center;gap:8px;position:sticky;top:0;z-index:100;user-select:none}
.conn{width:7px;height:7px;border-radius:50%;background:var(--green);flex-shrink:0;transition:background .3s}
.conn.off{background:var(--red)}
.hdr-name{font-size:14px;font-weight:700;letter-spacing:.06em;font-family:var(--mono);color:var(--fg)}
.hdr-sub{font-size:10px;color:var(--muted);font-family:var(--mono);margin-left:1px}
.hdr-sum{font-size:11px;color:var(--muted);margin-left:auto;white-space:nowrap}

/* ── Section label ── */
.sec{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;padding:10px 12px 3px;font-family:var(--mono)}

/* ── Tab card (matches AIDE TabCard exactly) ── */
.card{border-left:3px solid transparent;background:var(--panel);cursor:pointer;border-bottom:1px solid var(--inner);position:relative;transition:background .12s}
.card.s-waiting{background:var(--active);border-left-color:var(--accent);animation:bpulse 2s ease-in-out infinite}
.card.s-working{border-left-color:var(--green)}
.card.s-thinking{border-left-color:var(--amber)}
.card.s-idle:active{background:var(--surface)}
@keyframes bpulse{0%,100%{box-shadow:inset 3px 0 8px rgba(88,166,255,.10)}50%{box-shadow:inset 3px 0 18px rgba(88,166,255,.26)}}

/* card top row */
.crow{display:flex;align-items:center;gap:6px;padding:7px 8px 6px 8px;min-height:32px}
.cicon{font-size:11px;width:15px;text-align:center;flex-shrink:0;font-family:var(--mono);color:var(--muted)}
.ctitle{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px;color:var(--muted);transition:color .15s,font-weight .15s}
.s-waiting .ctitle{color:var(--fg);font-weight:700}
.s-working .ctitle,.s-thinking .ctitle{color:var(--fg);font-weight:500}
.cmeta{display:flex;align-items:center;gap:4px;flex-shrink:0;margin-left:2px}
.tag{font-size:9px;color:var(--accent);background:rgba(88,166,255,.1);border-radius:3px;padding:1px 4px;font-family:var(--mono)}
.badge{font-size:9px;font-weight:700;color:var(--fg);background:#1f6feb;border-radius:8px;padding:1px 6px;min-width:18px;text-align:center}
.ping{font-size:9px;color:var(--muted);font-family:var(--mono)}
.chev{font-size:11px;color:var(--muted);margin-left:1px;transition:transform .18s;flex-shrink:0;font-family:var(--mono)}
.card.open .chev{transform:rotate(90deg)}

/* card body (expanded) */
.cbody{display:none;flex-direction:column}
.card.open .cbody{display:flex}
.cout{background:var(--bg);padding:6px 10px;font-family:var(--mono);font-size:11px;color:var(--muted);white-space:pre-wrap;word-break:break-all;max-height:76px;overflow:hidden;line-height:1.55;border-top:1px solid var(--inner)}
.cout.empty{opacity:.4;font-style:italic}
.creply{display:flex;gap:6px;padding:7px 8px;border-top:1px solid var(--inner);background:var(--surface)}
.rinput{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:13px;padding:5px 8px;resize:none;min-height:30px;max-height:80px;font-family:-apple-system,sans-serif;line-height:1.4;-webkit-appearance:none;transition:border-color .15s}
.rinput:focus{outline:none;border-color:var(--accent)}
.rsend{background:var(--accent);color:#000;border:none;border-radius:6px;padding:0 12px;font-size:12px;font-weight:700;cursor:pointer;height:30px;flex-shrink:0;font-family:var(--mono);transition:opacity .1s}
.rsend:active{opacity:.7}

/* ── Footer ── */
.foot{background:var(--surface);border-top:1px solid var(--border);padding:8px 12px;display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:10px;color:var(--muted);position:sticky;bottom:0;padding-bottom:max(8px,env(safe-area-inset-bottom))}
.furl{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ── Empty ── */
.empty{padding:48px 16px;text-align:center;color:var(--muted);font-family:var(--mono);font-size:11px}
.empty-icon{font-size:24px;margin-bottom:10px;opacity:.35}
</style>
</head>
<body>

<div class="hdr">
  <div class="conn" id="conn"></div>
  <span class="hdr-name">AIDE</span>
  <span class="hdr-sub">remote</span>
  <span class="hdr-sum" id="sum"></span>
</div>

<div id="sidebar"></div>

<div class="foot">
  <span>&#128241;</span>
  <span class="furl" id="furl">connecting\u2026</span>
</div>

<script>
const GEAR = ['\u280b','\u2819','\u2839','\u2838','\u283c','\u2834','\u2826','\u2827','\u2807','\u280f'];
let tick = 0;
setInterval(() => tick++, 120);

function sc(s){ return s.waiting?'s-waiting':s.working?'s-working':s.thinking?'s-thinking':'s-idle'; }
function icon(s){ return (s.working||s.thinking) ? GEAR[tick%GEAR.length] : '\u25b8'; }
function ago(ts){
  if(!ts) return '';
  const d = Math.round(Date.now()/1000 - ts);
  if(d<5) return 'now'; if(d<60) return d+'s'; if(d<3600) return Math.round(d/60)+'m';
  return Math.round(d/3600)+'h';
}

function el(tag,cls){ const e=document.createElement(tag); if(cls) e.className=cls; return e; }
function tx(tag,cls,t){ const e=el(tag,cls); e.textContent=t; return e; }

let _openId = null;

function buildCard(s){
  const cls = sc(s);
  const card = el('div','card '+cls);
  card.dataset.id = s.id;

  /* top row */
  const row = el('div','crow');
  const ico = el('span','cicon'); ico.textContent = icon(s); row.appendChild(ico);
  row.appendChild(tx('span','ctitle',s.title));

  const meta = el('div','cmeta');
  s.tags.forEach(t => meta.appendChild(tx('span','tag','['+t+']')));
  if(s.tasks>0) meta.appendChild(tx('span','badge',String(s.tasks)));
  if(s.ping)    meta.appendChild(tx('span','ping','\u23f1\u202f'+ago(s.ping)));
  meta.appendChild(tx('span','chev','\u203a'));
  row.appendChild(meta);
  card.appendChild(row);

  /* body */
  const body = el('div','cbody');
  const out = el('div','cout'+(s.output?'':' empty'));
  out.textContent = s.output || '— no output yet —';
  body.appendChild(out);

  if(s.waiting){
    const rep = el('div','creply');
    const ta  = el('textarea','rinput');
    ta.id = 'ta'+s.id; ta.placeholder = 'Reply\u2026'; ta.rows = 1;
    ta.addEventListener('input', function(){ this.style.height='auto'; this.style.height=Math.min(this.scrollHeight,80)+'px'; });
    ta.addEventListener('keydown', e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();doSend(s.id);} });
    const btn = tx('button','rsend','Send');
    btn.addEventListener('click', ()=>doSend(s.id));
    rep.appendChild(ta); rep.appendChild(btn); body.appendChild(rep);
  }
  card.appendChild(body);

  row.addEventListener('click', ()=>{
    const wasOpen = card.classList.contains('open');
    document.querySelectorAll('.card.open').forEach(c=>c.classList.remove('open'));
    if(!wasOpen){ card.classList.add('open'); _openId=s.id; }
    else _openId=null;
  });

  return card;
}

async function doSend(id){
  const ta = document.getElementById('ta'+id); if(!ta) return;
  const text = ta.value.trim(); if(!text) return;
  ta.value=''; ta.style.height='';
  await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,text})});
}

function render(data){
  document.getElementById('conn').classList.remove('off');
  document.getElementById('furl').textContent = location.host;
  const sb = document.getElementById('sidebar');
  sb.replaceChildren();

  const waiting = data.filter(s=>s.waiting);
  const running  = data.filter(s=>!s.waiting);

  if(!data.length){
    const d=el('div','empty');
    d.appendChild(tx('div','empty-icon','\u25b8'));
    d.appendChild(tx('div','','No active sessions'));
    sb.appendChild(d); return;
  }

  if(waiting.length){
    sb.appendChild(tx('div','sec','\u23f3\u2002Waiting for input'));
    waiting.forEach(s=>{ const c=buildCard(s); if(s.id===_openId) c.classList.add('open'); sb.appendChild(c); });
  }
  if(running.length){
    sb.appendChild(tx('div','sec',waiting.length?'\u25b8\u2002Running':'\u25b8\u2002Sessions'));
    running.forEach(s=>{ const c=buildCard(s); if(s.id===_openId) c.classList.add('open'); sb.appendChild(c); });
  }

  /* auto-expand sole waiting card on first load */
  if(_openId===null && waiting.length===1){
    const c=sb.querySelector('.card.s-waiting');
    if(c){ c.classList.add('open'); _openId=Number(c.dataset.id); }
  }

  const n=data.length, w=waiting.length;
  document.getElementById('sum').textContent=n+' session'+(n!==1?'s':'')+(w?' \u00b7 '+w+' waiting':'');
}

async function refresh(){
  try{ const r=await fetch('/api/sessions'); render(await r.json()); }
  catch{ document.getElementById('conn').classList.add('off'); }
}

setInterval(refresh,2000);
refresh();
</script>
</body>
</html>
"""


def local_ip() -> str:
    """Best-effort LAN IP address (used to show the dashboard URL)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class _Handler(BaseHTTPRequestHandler):
    server: "DashboardServer"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = _HTML.encode()
            self._send(200, "text/html; charset=utf-8", body)
        elif self.path == "/api/sessions":
            body = json.dumps(self.server.sessions_json()).encode()
            self._send(200, "application/json", body)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/api/send":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                self.server.send_to(int(data["id"]), str(data["text"]))
            except Exception:
                pass
            self.send_response(204); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class DashboardServer(HTTPServer):
    """Threaded HTTP server that exposes AIDE sessions to a phone browser."""

    def __init__(self, port: int, get_sessions: Callable, send_cb: Callable):
        super().__init__(("0.0.0.0", port), _Handler)
        self._get_sessions = get_sessions
        self._send_cb = send_cb
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self.shutdown()

    def sessions_json(self) -> list:
        result = []
        for s in self._get_sessions().values():
            tail = getattr(s, "_output_tail", "")
            lines = [_ANSI.sub("", ln) for ln in tail.splitlines() if ln.strip()][-3:]
            tasks = s.tasks or ""
            result.append({
                "id":       s.tab_id,
                "title":    s.effective_title(),
                "tags":     getattr(s, "tags", []),
                "tasks":    len([l for l in tasks.splitlines() if l.strip()]),
                "waiting":  getattr(s, "waiting_input", False),
                "working":  getattr(s, "claude_working", False),
                "thinking": getattr(s, "claude_thinking", False),
                "ping":     getattr(s, "last_ping_time", 0) or None,
                "output":   "\n".join(lines),
            })
        return result

    def send_to(self, tab_id: int, text: str):
        self._send_cb(tab_id, text)
