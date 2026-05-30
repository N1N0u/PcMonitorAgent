"""
PC Monitor Agent — Grafana Dashboard + Chat
Left : live Grafana panel charts (Canvas)
Right: chat with N1n@u
"""

import os, sys, json, threading, time
from datetime import datetime

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from tools.prometheus import query_prometheus_range
from tools.grafana    import (check_grafana, list_dashboards,
                               fetch_panels_for_dashboard)
from agent.agent      import call_llm, check_ollama_server

OLLAMA_URL = "http://localhost:11434"
REFRESH_MS = 30_000          # Grafana panel refresh interval

RANGE_OPTIONS = {
    "Last 5 min":    300,
    "Last 15 min":   900,
    "Last 30 min":  1800,
    "Last 1 hour":  3600,
    "Last 3 hours": 10800,
    "Last 6 hours": 21600,
    "Last 24 hours":86400,
}

# ── shared state ──────────────────────────────────────────────────
chat_history   = []
_dashboard_ctx = {}   # stores latest panel data for the LLM context
_window        = None
_win_ready     = threading.Event()


def _push(event: str, data):
    _win_ready.wait(timeout=10)
    if _window:
        try:
            _window.evaluate_js(
                f"window.__recv({json.dumps({'event':event,'data':data}, default=str)})")
        except Exception:
            pass


# ── Python API ────────────────────────────────────────────────────
class Api:

    def ready(self):
        _win_ready.set()
        return {"ok": True}

    def ollama_status(self):
        return {"running": check_ollama_server(OLLAMA_URL)}

    def send_message(self, text: str):
        def _run():
            if not check_ollama_server(OLLAMA_URL):
                _push("chat_reply",
                      {"error": "Ollama not running — start with: ollama serve"})
                return

            # Build system prompt from the loaded Grafana dashboard panels
            ctx_block = _format_dashboard_context()
            system_prompt = f"""You are N1n@u, a sharp Senior AI Infrastructure Engineer.
You have access to live metrics pulled directly from the Grafana dashboard panels.

{ctx_block}

Rules:
- Answer questions about the system using the data above.
- If a metric is missing, say so clearly — do not invent values.
- Be concise and technical. You can also have normal conversations.
"""
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(chat_history)
            messages.append({"role": "user", "content": text})

            try:
                reply = call_llm(messages)
            except Exception as e:
                reply = f"[Error: {e}]"
            chat_history.append({"role": "user",      "content": text})
            chat_history.append({"role": "assistant", "content": reply})
            _push("chat_reply", {"reply": reply})
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True}

    def store_dashboard_ctx(self, panels: list):
        """Called by JS after panels are rendered — stores panel data for chat."""
        _dashboard_ctx.clear()
        _dashboard_ctx["panels"] = panels
        return {"ok": True}

    def grafana_connect(self, url: str, user: str, password: str):
        """Update credentials and return dashboard list."""
        def _run():
            import tools.grafana as gmod
            gmod.GRAFANA_URL  = url.rstrip("/")
            gmod.GRAFANA_USER = user
            gmod.GRAFANA_PASS = password
            ok    = check_grafana()
            dashs = list_dashboards() if ok else []
            _push("grafana_init", {
                "ok":         ok,
                "dashboards": dashs,    # [{uid, title}, ...]
            })
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True}

    def load_dashboard(self, uid: str, range_label: str):
        """Fetch panels for uid, query Prometheus, push series data."""
        def _run():
            panels = fetch_panels_for_dashboard(uid)
            if not panels:
                _push("panels_ready", {"error": "No panels found for this dashboard."})
                return

            duration_s = RANGE_OPTIONS.get(range_label, 1800)
            out = []
            for panel in panels:
                series = []
                for tgt in panel["targets"]:
                    expr   = tgt["expr"]
                    legend = tgt["legend"]
                    try:
                        results = query_prometheus_range(
                            expr, duration_s=duration_s)
                    except Exception:
                        results = []
                    for s in results:
                        name = legend or s["metric"].get("__name__", expr[:40])
                        for k, v in s["metric"].items():
                            name = name.replace("{{" + k + "}}", v)
                        series.append({
                            "name":       name,
                            "timestamps": [
                                datetime.fromtimestamp(t).strftime("%H:%M")
                                for t in s["timestamps"]],
                            "values": s["values"],
                        })
                out.append({
                    "title":  panel["panel_title"],
                    "type":   panel["panel_type"],
                    "series": series,
                })
            _push("panels_ready", {"panels": out})
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True}


def _format_dashboard_context() -> str:
    """Formats stored Grafana panel data into a readable block for the LLM."""
    panels = _dashboard_ctx.get("panels", [])
    if not panels:
        return ("=== DASHBOARD DATA ===\n"
                "No dashboard loaded yet.\n"
                "=====================")
    lines = ["=== LIVE GRAFANA DASHBOARD DATA ==="]
    for panel in panels:
        title  = panel.get("title", "Panel")
        series = panel.get("series", [])
        lines.append(f"\n[{title}]")
        if not series:
            lines.append("  No data"); continue
        for s in series:
            name   = s.get("name", "")
            values = s.get("values", [])
            if not values:
                lines.append(f"  {name}: no data"); continue
            last = values[-1]
            mn   = min(values)
            mx   = max(values)
            avg  = sum(values) / len(values)
            lines.append(
                f"  {name}: latest={last:.3g}  min={mn:.3g}"
                f"  max={mx:.3g}  avg={avg:.3g}"
            )
    lines.append("====================================")
    return "\n".join(lines)


# ── HTML / JS ────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<style>
:root{
  --bg:#080b10;--bg1:#0d1117;--bg2:#111820;--bg3:#172030;
  --border:#1e2d3d;--border2:#263545;
  --text:#cdd9e5;--dim:#4a6070;--dim2:#7090a0;
  --orange:#e07040;--blue:#4090d0;--green:#38b050;
  --red:#d04848;--purple:#7860d0;--cyan:#30b0aa;--yellow:#c09030;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--text);
  font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;overflow:hidden}

#root{display:flex;flex-direction:column;height:100vh}

/* topbar */
#topbar{
  height:42px;flex-shrink:0;display:flex;align-items:center;gap:10px;
  padding:0 16px;background:var(--bg1);border-bottom:1px solid var(--border);
}
.brand{font-size:12px;font-weight:700;letter-spacing:.12em;
  text-transform:uppercase;color:var(--orange)}
.sp{flex:1}
.badge{
  font-size:10px;font-weight:600;letter-spacing:.06em;
  padding:3px 9px;border-radius:3px;
  border:1px solid var(--border2);background:var(--bg2);
}
.badge.ok {color:var(--green);border-color:#1a3525}
.badge.err{color:var(--red);  border-color:#351a1a}
.badge.dim{color:var(--dim2)}

/* toolbar */
#toolbar{
  flex-shrink:0;display:flex;align-items:center;flex-wrap:wrap;gap:8px;
  padding:9px 16px;background:var(--bg1);border-bottom:1px solid var(--border);
}
.lbl{font-size:11px;color:var(--dim2);white-space:nowrap}
.inp{
  background:var(--bg2);border:1px solid var(--border2);color:var(--text);
  font-size:11px;padding:5px 8px;border-radius:3px;outline:none;
  transition:border-color .15s;
}
.inp:focus{border-color:var(--blue)}
.btn{
  font-size:10px;font-weight:700;letter-spacing:.06em;
  padding:5px 13px;border-radius:3px;cursor:pointer;border:1px solid var(--border2);
  transition:opacity .15s;
}
.btn:hover{opacity:.85}
.btn:disabled{opacity:.35;cursor:default}
.btn-connect{background:var(--purple);color:#fff;border-color:var(--purple)}
.btn-refresh{background:transparent;color:var(--blue)}
#g-status{font-size:11px;font-weight:600}
#g-status.ok {color:var(--green)}
#g-status.err{color:var(--red)}
#countdown{font-size:10px;color:var(--dim);margin-left:auto;
  font-variant-numeric:tabular-nums}

/* body: panels + chat */
#body{flex:1;display:flex;overflow:hidden}

/* panels area */
#panels-col{
  flex:1;display:flex;flex-direction:column;overflow:hidden;
  border-right:1px solid var(--border);
}
#panels-grid{
  flex:1;overflow-y:auto;padding:10px;
  display:grid;grid-template-columns:repeat(2,1fr);gap:8px;
  background:var(--bg);align-content:start;
}
#panels-grid::-webkit-scrollbar{width:5px}
#panels-grid::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}

.panel{
  background:var(--bg1);border:1px solid var(--border);
  border-radius:4px;display:flex;flex-direction:column;overflow:hidden;
}
.panel-title{
  padding:10px 14px 4px;
  font-size:10px;font-weight:600;letter-spacing:.08em;
  color:var(--dim2);text-transform:uppercase;
}
.panel canvas{width:100%;display:block;height:190px}

#placeholder{
  grid-column:1/-1;
  display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:12px;padding:80px;text-align:center;
}
#placeholder .icon{font-size:40px;opacity:.15}
#placeholder p{color:var(--dim2);line-height:1.75;font-size:12px}

/* chat */
#chat-col{
  width:320px;flex-shrink:0;
  display:flex;flex-direction:column;background:var(--bg1);
}
#chat-hdr{
  padding:11px 14px;border-bottom:1px solid var(--border);flex-shrink:0;
  display:flex;align-items:center;justify-content:space-between;
}
#chat-hdr .title{
  font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--orange)}
#typing{font-size:10px;color:var(--dim2);font-style:italic;min-height:14px}
#chat-log{
  flex:1;overflow-y:auto;padding:10px;
  display:flex;flex-direction:column;gap:8px;
}
#chat-log::-webkit-scrollbar{width:3px}
#chat-log::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.msg{display:flex;flex-direction:column;gap:3px}
.msg-role{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.msg-role.you {color:var(--orange)}
.msg-role.alex{color:var(--blue)}
.msg-body{
  background:var(--bg2);border:1px solid var(--border);
  border-radius:4px;padding:8px 10px;
  font-size:11px;line-height:1.55;color:var(--text);
  white-space:pre-wrap;word-break:break-word;
}
.msg.you  .msg-body{border-color:#2d1f14}
.msg.alex .msg-body{border-color:#142030}
#inp-row{
  display:flex;gap:6px;padding:8px 10px;
  border-top:1px solid var(--border);flex-shrink:0;
}
#chat-inp{
  flex:1;background:var(--bg2);border:1px solid var(--border2);
  color:var(--text);font-size:12px;padding:7px 10px;
  border-radius:3px;outline:none;transition:border-color .15s;
}
#chat-inp:focus{border-color:var(--orange)}
#send-btn{
  background:var(--orange);color:var(--bg);
  font-size:10px;font-weight:700;letter-spacing:.08em;
  border:none;border-radius:3px;padding:7px 14px;
  cursor:pointer;transition:opacity .15s;
}
#send-btn:hover{opacity:.85}
#send-btn:disabled{opacity:.35;cursor:default}

@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.pulse{animation:pulse 1.4s infinite}
</style>
</head>
<body>
<div id="root">

  <!-- topbar -->
  <div id="topbar">
    <div class="brand">⬛ PC Monitor Agent</div>
    <div class="sp"></div>
    <div id="badge-ollama" class="badge dim">● Ollama …</div>
    <div id="badge-time"   class="badge dim">--:--:--</div>
  </div>

  <!-- toolbar -->
  <div id="toolbar">
    <span class="lbl">URL</span>
    <input class="inp" id="g-url"  value="http://localhost:3000" style="width:185px"/>
    <span class="lbl">User</span>
    <input class="inp" id="g-user" value="admin"    style="width:72px"/>
    <span class="lbl">Pass</span>
    <input class="inp" id="g-pass" value="admin123" type="password" style="width:80px"/>
    <button class="btn btn-connect" id="btn-connect">Connect</button>

    <span id="g-status" style="color:var(--dim2)">● Not connected</span>

    <span class="lbl" style="margin-left:8px">Dashboard</span>
    <select class="inp" id="g-dash" style="width:190px"><option>— none —</option></select>

    <span class="lbl">Range</span>
    <select class="inp" id="g-range" style="width:110px">
      <option>Last 5 min</option>
      <option>Last 15 min</option>
      <option selected>Last 30 min</option>
      <option>Last 1 hour</option>
      <option>Last 3 hours</option>
      <option>Last 6 hours</option>
      <option>Last 24 hours</option>
    </select>

    <button class="btn btn-refresh" id="btn-refresh">⟳ Refresh</button>
    <span id="countdown"></span>
  </div>

  <!-- body -->
  <div id="body">

    <!-- panels -->
    <div id="panels-col">
      <div id="panels-grid">
        <div id="placeholder">
          <div class="icon">📡</div>
          <p>Enter your Grafana credentials and click <b>Connect</b>.<br/>
             Your dashboard panels will appear here, pulled live from Prometheus.</p>
        </div>
      </div>
    </div>

    <!-- chat -->
    <div id="chat-col">
      <div id="chat-hdr">
        <div class="title">⬡ Alex</div>
        <div id="typing"></div>
      </div>
      <div id="chat-log">
        <div class="msg alex">
          <div class="msg-role alex">Alex</div>
          <div class="msg-body" id="welcome-body">Checking Ollama…</div>
        </div>
      </div>
      <div id="inp-row">
        <input id="chat-inp" type="text"
               placeholder="Ask about your system…" autocomplete="off"/>
        <button id="send-btn">SEND</button>
      </div>
    </div>

  </div>
</div>

<script>
// ── Event bus ─────────────────────────────────────────────────────
window.__recv = p => { const {event,data}=p; handlers[event]?.(data); };
const handlers = {};
const on = (ev,fn) => { handlers[ev]=fn; };

// ── Ready ─────────────────────────────────────────────────────────
window.addEventListener('pywebviewready', () => {
  window.pywebview.api.ready();
  initBadges();
  // auto-connect on start with default credentials
  connect();
});

// ── Clock ─────────────────────────────────────────────────────────
setInterval(()=>{
  document.getElementById('badge-time').textContent =
    new Date().toTimeString().slice(0,8);
},1000);
document.getElementById('badge-time').textContent =
  new Date().toTimeString().slice(0,8);

// ── Badges ────────────────────────────────────────────────────────
async function initBadges(){
  try{
    const r = await window.pywebview.api.ollama_status();
    const b = document.getElementById('badge-ollama');
    b.textContent = r.running ? '● Ollama  ON' : '● Ollama  OFF';
    b.className   = 'badge '+(r.running?'ok':'err');
    document.getElementById('welcome-body').textContent = r.running
      ? 'Ready. Ask me anything about your system.'
      : '⚠ Ollama not running.\nStart it with:  ollama serve';
  }catch(e){}
}

// ── Grafana connect ───────────────────────────────────────────────
let _dashs = [];
let _timer = null;

function connect(){
  const st = document.getElementById('g-status');
  st.textContent = '● Connecting…'; st.className='';
  window.pywebview.api.grafana_connect(
    document.getElementById('g-url').value,
    document.getElementById('g-user').value,
    document.getElementById('g-pass').value,
  );
}

document.getElementById('btn-connect').addEventListener('click', connect);

on('grafana_init', d => {
  const st  = document.getElementById('g-status');
  const sel = document.getElementById('g-dash');
  if(d.ok){
    _dashs = d.dashboards;
    st.textContent = `● Connected  (${d.dashboards.length} dashboard${d.dashboards.length!==1?'s':''})`;
    st.className = 'ok';
    sel.innerHTML = d.dashboards
      .map(x=>`<option value="${x.uid}">${x.title}</option>`)
      .join('');
    if(d.dashboards.length) loadDashboard();
  } else {
    st.textContent = '● Unreachable'; st.className = 'err';
    placeholder('⚠ Cannot reach Grafana.\n\nCheck the URL and make sure Grafana is running.');
  }
});

// ── Load dashboard ────────────────────────────────────────────────
function loadDashboard(){
  const uid   = document.getElementById('g-dash').value;
  const range = document.getElementById('g-range').value;
  if(!uid || uid==='— none —') return;
  document.getElementById('btn-refresh').disabled = true;
  placeholder('⏳ Loading panels…');
  window.pywebview.api.load_dashboard(uid, range);
  scheduleCountdown();
}

document.getElementById('g-dash').addEventListener('change', loadDashboard);
document.getElementById('g-range').addEventListener('change', loadDashboard);
document.getElementById('btn-refresh').addEventListener('click', loadDashboard);

on('panels_ready', d => {
  document.getElementById('btn-refresh').disabled = false;
  if(d.error){ placeholder('⚠ '+d.error); return; }

  const grid = document.getElementById('panels-grid');
  grid.innerHTML = '';

  if(!d.panels||!d.panels.length){
    placeholder('No panels with Prometheus queries found.\nAdd Prometheus-backed panels in your Grafana dashboard.');
    return;
  }

  d.panels.forEach((p,i)=>{
    const div = document.createElement('div');
    div.className = 'panel';
    div.innerHTML = `<div class="panel-title">${esc(p.title)}</div>
      <canvas id="pc${i}"></canvas>`;
    grid.appendChild(div);
    requestAnimationFrame(()=>{
      const c = document.getElementById('pc'+i);
      if(c) drawPanel(c, p.series);
    });
  });
});

function placeholder(msg){
  document.getElementById('panels-grid').innerHTML =
    `<div id="placeholder">
       <div class="icon">📡</div>
       <p>${esc(msg).replace(/\n/g,'<br/>')}</p>
     </div>`;
}

// ── Countdown ─────────────────────────────────────────────────────
const INTERVAL_S = ${REFRESH_MS} / 1000;

function scheduleCountdown(){
  if(_timer) clearInterval(_timer);
  const el = document.getElementById('countdown');
  let rem = INTERVAL_S;
  el.textContent = `⟳ ${rem}s`;
  _timer = setInterval(()=>{
    rem--;
    if(rem<=0){ clearInterval(_timer); el.textContent=''; loadDashboard(); }
    else el.textContent = `⟳ ${rem}s`;
  },1000);
}

// ── Canvas chart ──────────────────────────────────────────────────
const DPR = window.devicePixelRatio || 1;
const PAL = ['#4090d0','#e07040','#38b050','#7860d0','#30b0aa','#d04848','#c09030'];

function rgba(hex,a){
  const n=parseInt(hex.slice(1),16);
  return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`;
}

function drawPanel(canvas, seriesList){
  const w = canvas.offsetWidth;
  const h = canvas.offsetHeight;
  if(!w||!h) return;
  canvas.width  = w * DPR;
  canvas.height = h * DPR;
  const ctx = canvas.getContext('2d');
  ctx.scale(DPR, DPR);

  const pad = {l:52,r:12,t:10,b:28};
  const cw  = w - pad.l - pad.r;
  const ch  = h - pad.t - pad.b;

  ctx.clearRect(0,0,w,h);

  if(!seriesList||!seriesList.length){
    ctx.fillStyle='#4a6070'; ctx.font='11px monospace'; ctx.textAlign='center';
    ctx.fillText('No data', w/2, h/2);
    return;
  }

  // value range
  let mn=Infinity, mx=-Infinity;
  seriesList.forEach(s=>s.values.forEach(v=>{ if(v<mn)mn=v; if(v>mx)mx=v; }));
  if(!isFinite(mn)){mn=0;mx=100;}
  if(mn===mx){mn=mn*0.9||0; mx=mx*1.1||1;}
  const vr = mx - mn;

  // grid + y-axis labels
  ctx.strokeStyle='#1e2d3d'; ctx.lineWidth=.5;
  ctx.fillStyle='#4a6070'; ctx.font='9px monospace'; ctx.textAlign='right';
  for(let i=0;i<=4;i++){
    const y = pad.t + ch*i/4;
    ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(pad.l+cw,y); ctx.stroke();
    const v = mx - vr*i/4;
    const lbl = v>=1e6?(v/1e6).toFixed(1)+'M'
              : v>=1000?(v/1000).toFixed(1)+'k'
              : v>=1  ? v.toFixed(v<10?2:0)
              : v.toFixed(3);
    ctx.fillText(lbl, pad.l-5, y+3);
  }

  // series
  seriesList.forEach((s,si)=>{
    const n = s.values.length;
    if(n<2) return;
    const color = PAL[si % PAL.length];
    const xs = s.values.map((_,i)=>pad.l + i*(cw/(n-1)));
    const ys = s.values.map(v=>pad.t + ch - ((v-mn)/vr)*ch);

    // fill under first series
    if(si===0){
      ctx.beginPath(); ctx.moveTo(xs[0],pad.t+ch);
      xs.forEach((x,i)=>ctx.lineTo(x,ys[i]));
      ctx.lineTo(xs[n-1],pad.t+ch); ctx.closePath();
      ctx.fillStyle=rgba(color,.08); ctx.fill();
    }

    // smooth line
    ctx.beginPath(); ctx.moveTo(xs[0],ys[0]);
    for(let i=1;i<n;i++){
      const cx=(xs[i-1]+xs[i])/2;
      ctx.bezierCurveTo(cx,ys[i-1],cx,ys[i],xs[i],ys[i]);
    }
    ctx.strokeStyle=color; ctx.lineWidth=1.5; ctx.stroke();

    // series label (top-right)
    ctx.fillStyle=color; ctx.font='9px monospace'; ctx.textAlign='right';
    ctx.fillText(s.name||'series '+si, pad.l+cw, pad.t+12+si*13);
  });

  // x-axis time labels
  const ts = seriesList[0]?.timestamps;
  if(ts&&ts.length){
    const n=ts.length;
    ctx.fillStyle='#4a6070'; ctx.font='9px monospace'; ctx.textAlign='center';
    [0,.25,.5,.75,1].forEach(t=>{
      const i=Math.min(Math.floor(t*(n-1)),n-1);
      ctx.fillText(ts[i]||'', pad.l+t*cw, pad.t+ch+18);
    });
  }
}

// ── Chat ──────────────────────────────────────────────────────────
function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function addMsg(role, text){
  const log = document.getElementById('chat-log');
  const d   = document.createElement('div');
  d.className = 'msg '+(role==='You'?'you':'alex');
  d.innerHTML = `<div class="msg-role ${role==='You'?'you':'alex'}">${role}</div>
    <div class="msg-body">${esc(text)}</div>`;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

async function sendChat(){
  const inp = document.getElementById('chat-inp');
  const txt = inp.value.trim(); if(!txt) return;
  inp.value = '';
  document.getElementById('send-btn').disabled = true;
  document.getElementById('typing').innerHTML = '<span class="pulse">typing…</span>';
  addMsg('You', txt);
  await window.pywebview.api.send_message(txt);
}

on('chat_reply', d=>{
  document.getElementById('send-btn').disabled = false;
  document.getElementById('typing').textContent = '';
  addMsg('Alex', d.error ? '⚠ '+d.error : d.reply);
  document.getElementById('chat-inp').focus();
});

document.getElementById('send-btn').addEventListener('click', sendChat);
document.getElementById('chat-inp').addEventListener('keydown', e=>{
  if(e.key==='Enter') sendChat();
});
</script>
</body>
</html>
""".replace("${REFRESH_MS}", str(REFRESH_MS))


# ── Entry ─────────────────────────────────────────────────────────
def main():
    import webview
    global _window
    _window = webview.create_window(
        title="PC Monitor Agent",
        html=HTML,
        js_api=Api(),
        width=1380, height=860,
        min_size=(1000, 600),
        background_color="#080b10",
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
