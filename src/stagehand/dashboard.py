"""
dashboard.py — Visual pipeline dashboard.

Serves a self-contained HTML page that renders pipeline DAGs
from ~/.stagehand checkpoint and registry data.

Usage:
    stagehand dashboard              # open on port 7400
    stagehand dashboard --port 8080  # custom port
"""

import http.server
import json
import os
import threading
import webbrowser
from pathlib import Path

from . import checkpoint as ckpt
from . import registry as reg


def get_dashboard_data() -> dict:
    """Collect all pipeline data for the dashboard."""
    active = ckpt.list_active()
    registry = reg.load()
    runs_data = {}

    # Collect run history for each known pipeline
    known_ids = set()
    for a in active:
        known_ids.add(a["pipeline_id"])
    for pid in registry.get("pipelines", {}):
        known_ids.add(pid)

    for pid in known_ids:
        runs = ckpt.list_runs(pid)
        if runs:
            runs_data[pid] = runs[:10]

    return {
        "active": active,
        "registry": registry.get("pipelines", {}),
        "runs": runs_data,
    }


def collect_all_pipelines() -> list:
    """Merge active checkpoints + registry + run history into a unified list."""
    stagehand_dir = Path(os.environ.get("STAGEHAND_DIR", "~/.stagehand")).expanduser()
    pipelines = {}

    # From registry
    registry = reg.load()
    for pid, info in registry.get("pipelines", {}).items():
        pipelines[pid] = {
            "id": pid,
            "status": info.get("status", "unknown"),
            "started_at": info.get("started_at", ""),
            "finished_at": info.get("finished_at", ""),
            "stages": info.get("stages", {}),
            "dag": info.get("dag", {}),
            "source": "registry",
        }

    # From active checkpoints (overrides registry — more current)
    active_dir = stagehand_dir / "active"
    if active_dir.exists():
        for f in sorted(active_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                pid = data.get("pipeline_id", f.stem)
                stages_summary = {}
                for sname, sdata in data.get("stages", {}).items():
                    stages_summary[sname] = sdata.get("status", "pending")
                pipelines[pid] = {
                    "id": pid,
                    "status": "active",
                    "started_at": data.get("started_at", ""),
                    "finished_at": "",
                    "stages": stages_summary,
                    "dag": data.get("dag", {}),
                    "source": "active",
                }
            except Exception:
                pass

    # From run history (add completed runs not in registry)
    runs_dir = stagehand_dir / "runs"
    if runs_dir.exists():
        for pipeline_dir in sorted(runs_dir.iterdir()):
            if not pipeline_dir.is_dir():
                continue
            run_files = sorted(pipeline_dir.glob("*.json"), reverse=True)
            for rf in run_files[:5]:  # last 5 runs per pipeline
                try:
                    data = json.loads(rf.read_text(encoding="utf-8"))
                    pid = data.get("pipeline_id", pipeline_dir.name)
                    run_id = f"{pid}@{rf.stem}"
                    stages_summary = {}
                    for sname, sdata in data.get("stages", {}).items():
                        stages_summary[sname] = sdata.get("status", "pending")
                    failed = any(s == "failed" for s in stages_summary.values())
                    pipelines[run_id] = {
                        "id": pid,
                        "run": rf.stem,
                        "status": "failed" if failed else "success",
                        "started_at": data.get("started_at", ""),
                        "finished_at": data.get("archived_at", ""),
                        "stages": stages_summary,
                        "dag": data.get("dag", {}),
                        "source": "history",
                    }
                except Exception:
                    pass

    # Sort by started_at descending
    sorted_pipelines = sorted(
        pipelines.values(),
        key=lambda p: p.get("started_at", ""),
        reverse=True,
    )
    return sorted_pipelines[:50]


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stagehand — Pipeline Dashboard</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
    background:#0D0F14;color:#EDE8DF;min-height:100vh;padding:2rem}
  a{color:#C4541C;text-decoration:none}
  .header{display:flex;align-items:center;justify-content:space-between;
    margin-bottom:2rem;padding-bottom:1rem;border-bottom:1px solid #1E2229}
  .header h1{font-size:1.4rem;font-weight:600;letter-spacing:-.01em}
  .header h1 span{color:#C4541C}
  .header .meta{font-size:.75rem;color:#4A5060;font-family:monospace}
  .pipeline-list{display:flex;flex-direction:column;gap:1.5rem}
  .pipeline-card{background:#111318;border:1px solid #1E2229;position:relative;overflow:hidden}
  .pipeline-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
  .pipeline-card.success::before{background:#22c55e}
  .pipeline-card.failed::before{background:#ef4444}
  .pipeline-card.active::before{background:#C4541C;animation:pulse 2s ease-in-out infinite}
  .pipeline-card.unknown::before{background:#4A5060}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .card-header{padding:1rem 1.25rem .75rem;display:flex;align-items:center;justify-content:space-between}
  .card-title{font-size:.9rem;font-weight:500;font-family:monospace}
  .card-status{font-size:.65rem;font-family:monospace;text-transform:uppercase;letter-spacing:.1em;
    padding:.2rem .5rem;border:1px solid}
  .card-status.success{color:#22c55e;border-color:rgba(34,197,94,.3)}
  .card-status.failed{color:#ef4444;border-color:rgba(239,68,68,.3)}
  .card-status.active{color:#C4541C;border-color:rgba(196,84,28,.3)}
  .card-status.unknown{color:#4A5060;border-color:#272D38}
  .card-meta{padding:0 1.25rem .75rem;font-size:.7rem;color:#4A5060;font-family:monospace;
    display:flex;gap:1.5rem}
  .dag-container{padding:.5rem 1.25rem 1.25rem;overflow-x:auto}
  .dag-svg{display:block;margin:0 auto}
  .dag-svg .node rect{rx:0;ry:0;stroke-width:1.5}
  .dag-svg .node text{font-family:monospace;font-size:11px;fill:#EDE8DF}
  .dag-svg .edge{stroke:#272D38;stroke-width:1.5;fill:none;marker-end:url(#arrow)}
  .dag-svg .node.done rect{fill:#15181F;stroke:#22c55e}
  .dag-svg .node.failed rect{fill:#1c1012;stroke:#ef4444}
  .dag-svg .node.pending rect{fill:#15181F;stroke:#272D38}
  .dag-svg .node.skipped rect{fill:#111318;stroke:#1E2229}
  .dag-svg .node.done text{fill:#22c55e}
  .dag-svg .node.failed text{fill:#ef4444}
  .dag-svg .node.pending text{fill:#4A5060}
  .dag-svg .node.skipped text{fill:#272D38}
  .empty{text-align:center;padding:4rem;color:#4A5060;font-family:monospace;font-size:.85rem}
  .refresh-btn{background:none;border:1px solid #272D38;color:#8A8F9E;padding:.3rem .8rem;
    font-family:monospace;font-size:.7rem;cursor:pointer;text-transform:uppercase;letter-spacing:.08em}
  .refresh-btn:hover{border-color:#C4541C;color:#C4541C}
</style>
</head>
<body>
<div class="header">
  <h1><span>stagehand</span> dashboard</h1>
  <div style="display:flex;align-items:center;gap:1rem">
    <span class="meta" id="count"></span>
    <button class="refresh-btn" onclick="loadData()">Refresh</button>
  </div>
</div>
<div class="pipeline-list" id="pipelines"></div>

<script>
const API = '/api/pipelines';

async function loadData() {
  const resp = await fetch(API);
  const pipelines = await resp.json();
  document.getElementById('count').textContent = pipelines.length + ' pipelines';
  const container = document.getElementById('pipelines');

  if (!pipelines.length) {
    container.innerHTML = '<div class="empty">No pipelines found.<br>Run a pipeline and refresh.</div>';
    return;
  }

  container.innerHTML = pipelines.map(p => renderPipeline(p)).join('');
}

function renderPipeline(p) {
  const status = p.status || 'unknown';
  const started = p.started_at ? p.started_at.substring(0, 19).replace('T', ' ') : '';
  const runLabel = p.run ? ' @ ' + p.run.substring(0, 15) : '';
  const source = p.source === 'active' ? ' (running)' : '';

  const stages = p.stages || {};
  const dag = p.dag || {};
  const stageNames = Object.keys(stages);
  const dagSvg = stageNames.length ? renderDAG(stageNames, dag, stages) : '<div class="empty" style="padding:1rem">No stages</div>';

  const counts = {done:0, failed:0, pending:0, skipped:0};
  Object.values(stages).forEach(s => { counts[s] = (counts[s]||0) + 1; });
  const statsStr = Object.entries(counts).filter(([_,v])=>v>0).map(([k,v])=>v+' '+k).join(' · ');

  return '<div class="pipeline-card ' + status + '">' +
    '<div class="card-header">' +
      '<span class="card-title">' + esc(p.id) + esc(runLabel) + '</span>' +
      '<span class="card-status ' + status + '">' + status + esc(source) + '</span>' +
    '</div>' +
    '<div class="card-meta">' +
      '<span>' + esc(started) + '</span>' +
      '<span>' + esc(statsStr) + '</span>' +
    '</div>' +
    '<div class="dag-container">' + dagSvg + '</div>' +
  '</div>';
}

function renderDAG(stageNames, dag, stages) {
  // Layout: assign each stage a column (depth) and row
  const depths = {};
  const maxIter = stageNames.length + 1;

  function getDepth(name, visited) {
    if (depths[name] !== undefined) return depths[name];
    if (visited.has(name)) return 0;
    visited.add(name);
    const deps = dag[name] || [];
    if (!deps.length) { depths[name] = 0; return 0; }
    const maxDep = Math.max(...deps.map(d => getDepth(d, visited)));
    depths[name] = maxDep + 1;
    return depths[name];
  }

  stageNames.forEach(n => getDepth(n, new Set()));

  // Group by depth
  const columns = {};
  stageNames.forEach(n => {
    const d = depths[n] || 0;
    if (!columns[d]) columns[d] = [];
    columns[d].push(n);
  });

  const colKeys = Object.keys(columns).map(Number).sort((a,b) => a-b);
  const nodeW = 130, nodeH = 32, padX = 50, padY = 16;
  const maxRows = Math.max(...colKeys.map(c => columns[c].length));
  const totalW = colKeys.length * (nodeW + padX) + padX;
  const totalH = maxRows * (nodeH + padY) + padY + 20;

  // Compute positions
  const pos = {};
  colKeys.forEach((col, ci) => {
    const rows = columns[col];
    const colH = rows.length * (nodeH + padY);
    const offsetY = (totalH - colH) / 2;
    rows.forEach((name, ri) => {
      pos[name] = {
        x: padX + ci * (nodeW + padX),
        y: offsetY + ri * (nodeH + padY),
      };
    });
  });

  // Build SVG
  let svg = '<svg class="dag-svg" width="' + totalW + '" height="' + totalH + '" viewBox="0 0 ' + totalW + ' ' + totalH + '">';
  svg += '<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#272D38"/></marker></defs>';

  // Edges
  stageNames.forEach(name => {
    const deps = dag[name] || [];
    deps.forEach(dep => {
      if (pos[dep] && pos[name]) {
        const x1 = pos[dep].x + nodeW, y1 = pos[dep].y + nodeH/2;
        const x2 = pos[name].x, y2 = pos[name].y + nodeH/2;
        const mx = (x1 + x2) / 2;
        svg += '<path class="edge" d="M' + x1 + ' ' + y1 + ' C' + mx + ' ' + y1 + ' ' + mx + ' ' + y2 + ' ' + x2 + ' ' + y2 + '"/>';
      }
    });
  });

  // Nodes
  stageNames.forEach(name => {
    const p = pos[name];
    const status = stages[name] || 'pending';
    const label = name.length > 16 ? name.substring(0,15) + '…' : name;
    svg += '<g class="node ' + status + '" transform="translate(' + p.x + ',' + p.y + ')">';
    svg += '<rect width="' + nodeW + '" height="' + nodeH + '"/>';
    svg += '<text x="' + (nodeW/2) + '" y="' + (nodeH/2 + 4) + '" text-anchor="middle">' + esc(label) + '</text>';
    svg += '</g>';
  });

  svg += '</svg>';
  return svg;
}

function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

loadData();
setInterval(loadData, 10000); // auto-refresh every 10s
</script>
</body>
</html>"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/pipelines":
            data = collect_all_pipelines()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())

    def log_message(self, format, *args):
        pass  # suppress access logs


def serve(port=7400, open_browser=True):
    """Start the dashboard server."""
    server = http.server.HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"[stagehand] Dashboard running at http://localhost:{port}")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[stagehand] Dashboard stopped.")
        server.shutdown()
