import html as _h
import math


# ── Gauge SVG (analog needle, terminal style) ─────────────────────────────────

def _gauge_svg(saturation_pct: float) -> str:
    """
    Renders a half-circle analog gauge with a needle and arc fill.
    saturation_pct: 0-100.  0 = green zone, 100 = red zone.
    """
    pct   = max(0.0, min(100.0, saturation_pct))
    # Needle goes from -180deg (left) to 0deg (right) across the half circle
    angle = -180 + (pct / 100) * 180   # degrees, from left to right

    # Convert angle to SVG coords (origin at center-bottom of half circle)
    rad   = math.radians(angle)
    cx, cy = 120, 110          # center of the arc
    r_needle = 80

    nx = cx + r_needle * math.cos(rad)
    ny = cy + r_needle * math.sin(rad)

    # Arc color: green → yellow → red
    if pct < 50:
        needle_color = "#15ff00"
    elif pct < 80:
        needle_color = "#ffcc00"
    else:
        needle_color = "#ff4444"

    # Build colored arc segments (green 0-50, yellow 50-80, red 80-100)
    def arc_path(start_pct, end_pct, color):
        r = 80
        sa = math.radians(-180 + start_pct * 1.8)
        ea = math.radians(-180 + end_pct   * 1.8)
        x1, y1 = cx + r * math.cos(sa), cy + r * math.sin(sa)
        x2, y2 = cx + r * math.cos(ea), cy + r * math.sin(ea)
        large  = 1 if (end_pct - start_pct) > 50 else 0
        return (
            f'<path d="M {x1:.1f} {y1:.1f} A {r} {r} 0 {large} 1 {x2:.1f} {y2:.1f}"'
            f' stroke="{color}" stroke-width="10" fill="none" stroke-linecap="round"/>'
        )

    return f"""
<svg width="240" height="130" viewBox="0 0 240 130" xmlns="http://www.w3.org/2000/svg"
     style="display:block;margin:0 auto;">
  <!-- Track background -->
  <path d="M 40 110 A 80 80 0 0 1 200 110"
        stroke="#0a2200" stroke-width="12" fill="none" stroke-linecap="round"/>
  <!-- Colored zones -->
  {arc_path(0,  50,  "#0a8f00")}
  {arc_path(50, 80,  "#806600")}
  {arc_path(80, 100, "#802222")}
  <!-- Needle -->
  <line x1="{cx}" y1="{cy}"
        x2="{nx:.1f}" y2="{ny:.1f}"
        stroke="{needle_color}" stroke-width="2.5" stroke-linecap="round"
        style="filter:drop-shadow(0 0 4px {needle_color})"/>
  <!-- Pivot -->
  <circle cx="{cx}" cy="{cy}" r="5" fill="{needle_color}"
          style="filter:drop-shadow(0 0 6px {needle_color})"/>
  <!-- Labels -->
  <text x="32"  y="126" fill="#0a8f00" font-family="monospace" font-size="9">0%</text>
  <text x="108" y="24"  fill="#0a8f00" font-family="monospace" font-size="9">50%</text>
  <text x="196" y="126" fill="#802222" font-family="monospace" font-size="9">100%</text>
  <!-- Value -->
  <text x="120" y="108" fill="{needle_color}" font-family="monospace" font-size="13"
        text-anchor="middle" style="filter:drop-shadow(0 0 4px {needle_color})">
    {pct:.1f}%
  </text>
</svg>"""


# ── Sub-renderers ─────────────────────────────────────────────────────────────

def _log_rows(recent: list) -> str:
    if not recent:
        return '<tr><td colspan="6" style="color:var(--dim)">NO REQUESTS YET</td></tr>'
    rows = ""
    for r in recent:
        blocked_style = "color:var(--red)" if r.blocked else "color:var(--green)"
        blocked_label = f"[{r.blocked_by.upper()}]" if r.blocked_by else "[OK]"
        ts = r.created_at.strftime("%H:%M:%S")
        rows += (
            f"<tr>"
            f'<td style="color:var(--dim)">{ts}</td>'
            f'<td style="color:var(--bright)">{_h.escape(r.method)}</td>'
            f'<td class="mono-overflow">{_h.escape(r.path[:60])}</td>'
            f'<td>{r.status_code}</td>'
            f'<td style="color:var(--dim)">{r.latency_ms:.0f}ms</td>'
            f'<td style="{blocked_style}">{blocked_label}</td>'
            f"</tr>"
        )
    return rows


def _top_ip_rows(top_ips: list) -> str:
    if not top_ips:
        return '<tr><td colspan="2" style="color:var(--dim)">NO DATA</td></tr>'
    rows = ""
    for entry in top_ips:
        rows += (
            f"<tr>"
            f'<td style="color:var(--bright)">{_h.escape(entry["ip"])}</td>'
            f'<td style="color:var(--green)">{entry["count"]} req</td>'
            f"</tr>"
        )
    return rows


# ── Full template ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="5">
  <title>RATE GUARDIAN // DASHBOARD</title>
  <link href="https://fonts.googleapis.com/css2?family=VT323&family=Share+Tech+Mono&display=swap" rel="stylesheet">
  <style>
    :root {
      --green: #15ff00; --dim: #0a8f00; --bright: #39ff14;
      --red: #ff4444;   --yellow: #ffcc00;
      --bg: #080808;    --glow: 0 0 8px #15ff00, 0 0 2px #15ff00;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { background: var(--bg); color: var(--green); font-family: 'Share Tech Mono', monospace; min-height: 100vh; }
    .terminal  { max-width: 1000px; margin: 0 auto; padding: 32px 24px 60px; }
    .title     { font-family: 'VT323', monospace; font-size: clamp(2rem,6vw,3rem); color: var(--bright); text-shadow: var(--glow); letter-spacing: 4px; }
    .subtitle  { font-size: 0.72rem; color: var(--dim); letter-spacing: 2px; margin-top: 4px; margin-bottom: 24px; }
    hr  { border: none; border-top: 1px solid var(--dim); margin: 18px 0; }
    .line { margin: 4px 0; font-size: 0.88rem; }
    .label { color: var(--dim); }
    .value { color: var(--bright); }
    .ok .value::before { content: "[OK] "; color: var(--green); }

    .section-title { font-family: 'VT323', monospace; font-size: 1.35rem; color: var(--bright); letter-spacing: 2px; margin-bottom: 10px; }
    .box  { border: 1px solid var(--dim); background: rgba(21,255,0,0.02); padding: 14px 18px; margin: 16px 0; }

    /* Grid layout */
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    @media (max-width: 640px) { .grid-2 { grid-template-columns: 1fr; } }

    /* Tables */
    table  { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th     { color: var(--dim); text-transform: uppercase; font-size: 0.68rem; letter-spacing: 1px; padding: 3px 6px; border-bottom: 1px solid var(--dim); text-align: left; }
    td     { padding: 4px 6px; border-bottom: 1px solid rgba(21,255,0,0.06); }
    .mono-overflow { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

    /* Stat cards */
    .stats-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
    .stat-card { border: 1px solid var(--dim); padding: 10px 16px; flex: 1; min-width: 110px; }
    .stat-num  { font-family: 'VT323', monospace; font-size: 2.2rem; color: var(--bright); line-height: 1; text-shadow: var(--glow); }
    .stat-lbl  { font-size: 0.65rem; color: var(--dim); letter-spacing: 1px; text-transform: uppercase; margin-top: 2px; }
    .stat-card.danger .stat-num { color: var(--red); text-shadow: 0 0 8px var(--red); }

    /* Gauge */
    .gauge-box  { text-align: center; padding: 10px; }
    .gauge-lbl  { font-size: 0.7rem; color: var(--dim); letter-spacing: 2px; text-transform: uppercase; margin-top: 4px; }

    /* Buttons */
    .btn { border: 1px solid var(--green); color: var(--green); background: transparent; padding: 6px 16px; font-family: 'Share Tech Mono', monospace; font-size: 0.8rem; cursor: pointer; text-decoration: none; letter-spacing: 1px; text-transform: uppercase; }
    .btn:hover { background: rgba(21,255,0,0.1); color: var(--bright); }
    .actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 16px; }

    /* Refresh indicator */
    .refresh { font-size: 0.68rem; color: var(--dim); float: right; }
    .cursor  { display: inline-block; width: 9px; height: 1em; background: var(--green); animation: blink 1s step-end infinite; vertical-align: text-bottom; margin-left: 3px; }
    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
    .footer  { margin-top: 36px; font-size: 0.68rem; color: var(--dim); }

    /* Form */
    .form-row { display: flex; align-items: center; gap: 10px; margin: 7px 0; flex-wrap: wrap; }
    .form-row label { color: var(--dim); font-size: 0.74rem; min-width: 120px; text-transform: uppercase; letter-spacing: 1px; }
    .form-row input, .form-row select {
      background: rgba(21,255,0,0.04); border: 1px solid var(--dim);
      color: var(--green); font-family: 'Share Tech Mono', monospace;
      font-size: 0.82rem; padding: 4px 10px; outline: none; flex: 1; min-width: 150px;
    }
    .form-row input:focus, .form-row select:focus { border-color: var(--bright); }
    .form-row select option { background: #080808; }
    .msg { font-size: 0.78rem; margin-top: 6px; padding: 4px 10px; min-height: 1.2em; }
    .msg.ok  { color: var(--bright); border-left: 2px solid var(--bright); }
    .msg.err { color: var(--red);    border-left: 2px solid var(--red); }
  </style>
</head>
<body>
<div class="terminal">

  <span class="refresh">AUTO-REFRESH: 5s</span>
  <div class="title">RATE GUARDIAN</div>
  <div class="subtitle">API GATEWAY &mdash; LIVE DASHBOARD &mdash; LAST 5 MIN</div>

  <div class="line ok"><span class="label">SYSTEM &nbsp;&nbsp;&nbsp;</span><span class="value">ONLINE</span></div>
  <div class="line ok"><span class="label">GATEWAY &nbsp;&nbsp;</span><span class="value">ACTIVE</span></div>
  <div class="line"><span class="label">UPTIME &nbsp;&nbsp;&nbsp;</span><span class="value" id="uptime">00:00:00</span></div>

  <hr>

  <!-- ── SATURATION GAUGE + STATS ─────────────────────────────────────────── -->
  <div class="grid-2">

    <div class="box gauge-box">
      <div class="section-title">&gt; API SATURATION</div>
      {{GAUGE_SVG}}
      <div class="gauge-lbl">blocked / total requests</div>
    </div>

    <div class="box">
      <div class="section-title">&gt; STATS</div>
      <div class="stats-row">
        <div class="stat-card">
          <div class="stat-num">{{TOTAL}}</div>
          <div class="stat-lbl">Total req</div>
        </div>
        <div class="stat-card">
          <div class="stat-num">{{ALLOWED}}</div>
          <div class="stat-lbl">Allowed</div>
        </div>
        <div class="stat-card danger">
          <div class="stat-num">{{BLOCKED}}</div>
          <div class="stat-lbl">Blocked</div>
        </div>
      </div>
      <div class="stats-row" style="margin-top:8px">
        <div class="stat-card">
          <div class="stat-num">{{AVG_LAT}}<span style="font-size:1rem">ms</span></div>
          <div class="stat-lbl">Avg latency</div>
        </div>
        <div class="stat-card">
          <div class="stat-num">{{SAT_PCT}}<span style="font-size:1rem">%</span></div>
          <div class="stat-lbl">Sat. rate</div>
        </div>
      </div>
    </div>

  </div>

  <!-- ── LOGS + TOP IPs ────────────────────────────────────────────────────── -->
  <div class="grid-2">

    <div class="box">
      <div class="section-title">&gt; RECENT REQUESTS</div>
      <table>
        <thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>Lat</th><th>Result</th></tr></thead>
        <tbody>{{LOG_ROWS}}</tbody>
      </table>
    </div>

    <div class="box">
      <div class="section-title">&gt; TOP IPs (1h)</div>
      <table>
        <thead><tr><th>IP</th><th>Requests</th></tr></thead>
        <tbody>{{TOP_IP_ROWS}}</tbody>
      </table>
    </div>

  </div>

  <!-- ── ADD RULE FORM ─────────────────────────────────────────────────────── -->
  <div class="box">
    <div class="section-title">&gt; ADD RATE LIMIT RULE</div>
    <form id="rule-form" onsubmit="submitRule(event)">
      <div class="form-row">
        <label>NAME</label>
        <input type="text" id="r-name" placeholder="e.g. Limit /api/posts" required>
      </div>
      <div class="form-row">
        <label>PATH PATTERN</label>
        <input type="text" id="r-path" placeholder="e.g. /proxy/*/posts*" required>
      </div>
      <div class="form-row">
        <label>LIMIT</label>
        <input type="number" id="r-limit" placeholder="e.g. 10" min="1" required>
        <span style="color:var(--dim);font-size:0.75rem">requests per window</span>
      </div>
      <div class="form-row">
        <label>WINDOW (sec)</label>
        <input type="number" id="r-window" placeholder="e.g. 60" min="1" required>
      </div>
      <div class="form-row">
        <label>KEY TYPE</label>
        <select id="r-key">
          <option value="ip">Per IP</option>
          <option value="global">Global</option>
        </select>
      </div>
      <div class="form-row" style="margin-top:8px">
        <button class="btn" type="submit">&gt; CREATE RULE</button>
      </div>
      <div id="rule-msg" class="msg"></div>
    </form>
  </div>

  <!-- ── ACTIVE RULES ──────────────────────────────────────────────────────── -->
  <div class="box">
    <div class="section-title">&gt; ACTIVE RULES</div>
    <div id="rules-list"><span style="color:var(--dim);font-size:0.8rem">Loading...</span></div>
  </div>

  <div class="actions">
    <a class="btn" href="/docs">&gt; API DOCS</a>
    <a class="btn" href="/logs">&gt; ALL LOGS (JSON)</a>
    <a class="btn" href="/dashboard">&gt; STATS JSON</a>
  </div>

  <hr>
  <div class="line"><span style="color:var(--dim)">&gt; MONITORING ACTIVE</span><span class="cursor"></span></div>
  <div class="footer">RATE GUARDIAN &mdash; BUILT BY FEDERICO MOROZ</div>
</div>

<script>
  // Uptime
  const t0 = Date.now();
  setInterval(() => {
    const s = Math.floor((Date.now()-t0)/1000);
    const p = n => String(n).padStart(2,'0');
    document.getElementById('uptime').textContent =
      p(Math.floor(s/3600))+':'+p(Math.floor((s%3600)/60))+':'+p(s%60);
  }, 1000);

  // Rules
  async function loadRules() {
    const el = document.getElementById('rules-list');
    try {
      const data = await fetch('/rules').then(r => r.json());
      if (!data.length) { el.innerHTML='<span style="color:var(--dim);font-size:0.8rem">No rules configured.</span>'; return; }
      el.innerHTML = `
        <table>
          <thead><tr><th>Name</th><th>Pattern</th><th>Limit</th><th>Window</th><th>Key</th><th>Status</th><th></th></tr></thead>
          <tbody>
            ${data.map(r => `
              <tr>
                <td style="color:var(--bright)">${r.name}</td>
                <td style="color:var(--dim)">${r.path_pattern}</td>
                <td>${r.limit}</td>
                <td>${r.window_seconds}s</td>
                <td>${r.key_type}</td>
                <td style="color:${r.active ? 'var(--green)':'var(--dim)'}">${r.active ? 'ACTIVE':'OFF'}</td>
                <td style="display:flex;gap:6px">
                  <button class="btn" style="font-size:0.68rem;padding:2px 8px"
                    onclick="toggleRule(${r.id})">&gt; TOGGLE</button>
                  <button class="btn" style="font-size:0.68rem;padding:2px 8px;border-color:var(--red);color:var(--red)"
                    onclick="deleteRule(${r.id})">&gt; DEL</button>
                </td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    } catch { el.innerHTML='<span style="color:var(--red)">Error loading rules.</span>'; }
  }

  async function submitRule(e) {
    e.preventDefault();
    const msg = document.getElementById('rule-msg');
    try {
      const res = await fetch('/rules', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          name:           document.getElementById('r-name').value,
          path_pattern:   document.getElementById('r-path').value,
          limit:          parseInt(document.getElementById('r-limit').value),
          window_seconds: parseInt(document.getElementById('r-window').value),
          key_type:       document.getElementById('r-key').value,
        })
      });
      if (res.ok) {
        msg.className='msg ok'; msg.textContent='[OK] Rule created.';
        e.target.reset(); loadRules();
        setTimeout(()=>{ msg.textContent=''; msg.className='msg'; }, 4000);
      } else {
        const err = await res.json();
        msg.className='msg err'; msg.textContent='[ERR] '+(err.detail||'Error.');
      }
    } catch { msg.className='msg err'; msg.textContent='[ERR] Network error.'; }
  }

  async function toggleRule(id) {
    await fetch(`/rules/${id}/toggle`, {method:'PATCH'});
    loadRules();
  }

  async function deleteRule(id) {
    await fetch(`/rules/${id}`, {method:'DELETE'});
    loadRules();
  }

  loadRules();
</script>
</body>
</html>"""


def render_dashboard(stats: dict, top_ips: list, recent: list, saturation_pct: float) -> str:
    danger_cls = "danger" if saturation_pct >= 80 else ""
    return (
        DASHBOARD_HTML
        .replace("{{GAUGE_SVG}}",   _gauge_svg(saturation_pct))
        .replace("{{TOTAL}}",       str(stats["total_requests"]))
        .replace("{{ALLOWED}}",     str(stats["allowed_requests"]))
        .replace("{{BLOCKED}}",     str(stats["blocked_requests"]))
        .replace("{{AVG_LAT}}",     str(stats["avg_latency_ms"]))
        .replace("{{SAT_PCT}}",     str(saturation_pct))
        .replace("{{LOG_ROWS}}",    _log_rows(recent))
        .replace("{{TOP_IP_ROWS}}", _top_ip_rows(top_ips))
    )
