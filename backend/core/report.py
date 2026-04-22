"""
HTML report generator.

Queries the DB for a completed run and returns a self-contained HTML string:
  - Summary stats (suite, device, model, pass rate)
  - Per-case table with status badge, reason, steps, screenshot, log modal, step replay
  - Step replay modal: per-step screenshot + AI thought + action (Midscene-style)
  - Log modal: full-screen terminal view

Usage:
    html = await generate_html_report(run_id)
"""
from __future__ import annotations

import json
from datetime import datetime
from html import escape
from typing import Optional

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import TestCase, TestResult, TestRun, TestStepLog, TestSuite


# ── Status badge colors (inline styles — no external CSS) ────────────────────

_BADGE_STYLE = {
    "pass":      "background:#d1fae5;color:#065f46",
    "fail":      "background:#fee2e2;color:#991b1b",
    "error":     "background:#ffedd5;color:#9a3412",
    "skip":      "background:#f3f4f6;color:#6b7280",
    "pending":   "background:#fef9c3;color:#92400e",
    "running":   "background:#dbeafe;color:#1e40af",
    "cancelled": "background:#ede9fe;color:#5b21b6",
}


def _badge(status: str) -> str:
    style = _BADGE_STYLE.get(status, "background:#f3f4f6;color:#374151")
    return (
        f'<span style="display:inline-block;padding:1px 8px;border-radius:9999px;'
        f'font-size:11px;font-weight:600;{style}">{escape(status)}</span>'
    )


def _fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _duration(start: Optional[datetime], end: Optional[datetime]) -> str:
    if start is None or end is None:
        return "—"
    secs = int((end - start).total_seconds())
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


# ── Main generator ────────────────────────────────────────────────────────────

async def generate_html_report(run_id: str) -> str:
    async with AsyncSessionLocal() as db:
        run = await db.get(TestRun, run_id)
        if run is None:
            raise ValueError(f"Run {run_id!r} not found")

        suite = await db.get(TestSuite, run.suite_id)
        suite_name = suite.name if suite else run.suite_id

        res = await db.execute(
            select(TestResult, TestCase)
            .join(TestCase, TestResult.case_id == TestCase.id)
            .where(TestResult.run_id == run_id)
            .order_by(TestCase.order)
        )
        rows = res.all()

        # Fetch step logs keyed by result_id
        step_logs_map: dict[str, list] = {}
        if rows:
            result_ids = [r.id for r, _ in rows]
            sl_res = await db.execute(
                select(TestStepLog)
                .where(TestStepLog.result_id.in_(result_ids))
                .order_by(TestStepLog.result_id, TestStepLog.step)
            )
            for sl in sl_res.scalars().all():
                step_logs_map.setdefault(sl.result_id, []).append({
                    "step": sl.step,
                    "thought": sl.thought,
                    "action": sl.action,
                    "action_result": sl.action_result,
                    "screenshot_b64": sl.screenshot_b64,
                })

    # ── Stats ────────────────────────────────────────────────────────────────
    total = len(rows)
    counts: dict[str, int] = {}
    run_total_tokens = 0
    for r, _ in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
        run_total_tokens += r.total_tokens or 0

    passed = counts.get("pass", 0)
    failed = counts.get("fail", 0)
    errored = counts.get("error", 0)
    skipped = counts.get("skip", 0)
    pass_rate = f"{passed / total * 100:.0f}%" if total else "—"
    tokens_display = f"{run_total_tokens / 1000:.1f}k" if run_total_tokens else "—"

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── Embed all step data as a JS object (lazy rendering — not in DOM until modal opens) ──
    # Keys are result row indices (0-based) to keep it compact
    steps_js_data: dict[str, list] = {}
    log_js_data: dict[str, str] = {}

    # ── Per-case rows ────────────────────────────────────────────────────────
    case_rows_html = ""
    for idx, (r, c) in enumerate(rows):
        shot_id = f"shot_{idx}"
        key = str(idx)

        steps_js_data[key] = step_logs_map.get(r.id, [])
        log_js_data[key] = r.log or "(no log)"

        # Screenshot thumbnail (click to open image modal)
        if r.screenshot_b64:
            thumb = (
                f'<img src="data:image/png;base64,{r.screenshot_b64}" '
                f'alt="screenshot" '
                f'style="max-height:80px;border-radius:4px;border:1px solid #e5e7eb;cursor:pointer;" '
                f'onclick="openImgModal(\'{shot_id}\')" />'
                f'<div id="{shot_id}" style="display:none">'
                f'  <img src="data:image/png;base64,{r.screenshot_b64}" style="max-width:88vw;max-height:88vh;border-radius:8px;" />'
                f'</div>'
            )
        else:
            thumb = '<span style="color:#9ca3af;font-size:11px;">—</span>'

        # Replay button (only if step logs exist)
        n_steps = len(steps_js_data[key])
        if n_steps:
            replay_btn = (
                f'<button onclick="openReplay({key})" '
                f'style="font-size:11px;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;'
                f'border-radius:4px;padding:2px 8px;cursor:pointer;">▶ {n_steps} 步</button>'
            )
        else:
            replay_btn = '<span style="color:#9ca3af;font-size:11px;">—</span>'

        # Log button
        log_btn = (
            f'<button onclick="openLog({key})" '
            f'style="font-size:11px;background:#f8fafc;color:#475569;border:1px solid #e2e8f0;'
            f'border-radius:4px;padding:2px 8px;cursor:pointer;">日志</button>'
        )

        duration = _duration(r.started_at, r.finished_at)

        case_rows_html += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:10px 8px;text-align:center;color:#9ca3af;font-size:12px;">{idx + 1}</td>
          <td style="padding:10px 8px;">
            <div style="font-size:12px;color:#6b7280;">{escape(c.path)}</div>
            <div style="font-size:13px;margin-top:2px;">{escape(c.expected)}</div>
          </td>
          <td style="padding:10px 8px;">{_badge(r.status)}</td>
          <td style="padding:10px 8px;font-size:12px;color:#374151;max-width:280px;">{escape(r.reason or "—")}</td>
          <td style="padding:10px 8px;text-align:center;font-size:12px;color:#6b7280;">{r.steps}</td>
          <td style="padding:10px 8px;text-align:center;font-size:12px;color:#6b7280;">{duration}</td>
          <td style="padding:10px 8px;text-align:center;font-size:11px;color:#86198f;">{f"{(r.total_tokens or 0) / 1000:.1f}k" if r.total_tokens else "—"}</td>
          <td style="padding:10px 8px;">{thumb}</td>
          <td style="padding:10px 8px;">{replay_btn}</td>
          <td style="padding:10px 8px;">{log_btn}</td>
        </tr>"""

    # ── Summary cards ────────────────────────────────────────────────────────
    stat_cards = f"""
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px;">
      <div style="background:#d1fae5;border-radius:8px;padding:12px 20px;text-align:center;">
        <div style="font-size:24px;font-weight:700;color:#065f46;">{passed}</div>
        <div style="font-size:12px;color:#065f46;">Pass</div>
      </div>
      <div style="background:#fee2e2;border-radius:8px;padding:12px 20px;text-align:center;">
        <div style="font-size:24px;font-weight:700;color:#991b1b;">{failed}</div>
        <div style="font-size:12px;color:#991b1b;">Fail</div>
      </div>
      <div style="background:#ffedd5;border-radius:8px;padding:12px 20px;text-align:center;">
        <div style="font-size:24px;font-weight:700;color:#9a3412;">{errored}</div>
        <div style="font-size:12px;color:#9a3412;">Error</div>
      </div>
      <div style="background:#f3f4f6;border-radius:8px;padding:12px 20px;text-align:center;">
        <div style="font-size:24px;font-weight:700;color:#6b7280;">{skipped}</div>
        <div style="font-size:12px;color:#6b7280;">Skip</div>
      </div>
      <div style="background:#dbeafe;border-radius:8px;padding:12px 20px;text-align:center;">
        <div style="font-size:24px;font-weight:700;color:#1e40af;">{total}</div>
        <div style="font-size:12px;color:#1e40af;">Total</div>
      </div>
      <div style="background:#ede9fe;border-radius:8px;padding:12px 20px;text-align:center;">
        <div style="font-size:24px;font-weight:700;color:#5b21b6;">{pass_rate}</div>
        <div style="font-size:12px;color:#5b21b6;">Pass Rate</div>
      </div>
      <div style="background:#fdf4ff;border-radius:8px;padding:12px 20px;text-align:center;">
        <div style="font-size:24px;font-weight:700;color:#86198f;">{tokens_display}</div>
        <div style="font-size:12px;color:#86198f;">Tokens</div>
      </div>
    </div>"""

    # Serialise JS data (screenshots are already base64 strings — safe to embed in JS)
    steps_json = json.dumps(steps_js_data, ensure_ascii=False)
    logs_json  = json.dumps(log_js_data,   ensure_ascii=False)

    # ── Full HTML ────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Test Report — {escape(suite_name)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f9fafb; color: #111827; padding: 24px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border-radius: 8px; overflow: hidden;
           box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  th {{ background: #f3f4f6; font-size: 11px; font-weight: 600; color: #6b7280;
        text-transform: uppercase; letter-spacing: 0.05em;
        padding: 10px 8px; text-align: left; }}
  tr:hover td {{ background: #fafafa; }}

  /* ── Shared modal overlay ── */
  .modal-overlay {{
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.82); z-index: 9999;
    align-items: flex-start; justify-content: center;
    overflow-y: auto; padding: 24px;
  }}
  .modal-overlay.active {{ display: flex; }}
  .modal-box {{
    background: #fff; border-radius: 12px; position: relative;
    width: 100%; max-width: 960px; margin: auto;
    box-shadow: 0 20px 60px rgba(0,0,0,0.4);
  }}
  .modal-close {{
    position: sticky; top: 0; float: right;
    background: #374151; color: #fff; border: none; border-radius: 0 12px 0 8px;
    padding: 6px 14px; font-size: 14px; cursor: pointer; z-index: 1;
  }}
  .modal-close:hover {{ background: #1f2937; }}

  /* ── Step replay modal ── */
  #replay-modal .modal-box {{ max-width: 1080px; }}
  .step-grid {{
    display: flex; flex-wrap: wrap; gap: 16px;
    padding: 20px;
  }}
  .step-card {{
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    overflow: hidden; width: 260px; flex-shrink: 0;
  }}
  .step-card-header {{
    display: flex; align-items: center; gap: 8px;
    padding: 8px 12px; background: #f1f5f9; border-bottom: 1px solid #e2e8f0;
  }}
  .step-num {{
    background: #3b82f6; color: #fff; border-radius: 50%;
    width: 20px; height: 20px; display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700; flex-shrink: 0;
  }}
  .step-fn {{ font-size: 11px; font-family: monospace; color: #1d4ed8; font-weight: 600; }}
  .step-card img {{ width: 100%; display: block; }}
  .step-thought {{
    font-size: 11px; color: #64748b; font-style: italic;
    padding: 8px 12px 4px; line-height: 1.5;
  }}
  .step-action {{
    font-size: 11px; font-family: monospace; color: #374151;
    padding: 4px 12px; word-break: break-all;
  }}
  .step-result {{
    font-size: 11px; font-family: monospace; color: #16a34a;
    padding: 2px 12px 10px; word-break: break-all;
  }}
  .step-card-final .step-card-header {{ background: #f0fdf4; }}
  .step-num-final {{
    background: #16a34a; color: #fff; border-radius: 50%;
    width: 20px; height: 20px; display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 700; flex-shrink: 0;
  }}

  /* ── Log modal ── */
  #log-modal .modal-box {{ background: #0f172a; max-width: 1200px; }}
  #log-modal .modal-close {{ background: #475569; border-radius: 0 12px 0 8px; }}
  #log-modal .modal-close:hover {{ background: #334155; }}
  #log-modal .modal-title {{
    color: #94a3b8; font-size: 12px; font-weight: 600; text-transform: uppercase;
    padding: 14px 20px 0; letter-spacing: 0.05em;
  }}
  #log-content {{
    font-family: "SF Mono", "Fira Code", Consolas, monospace;
    font-size: 12px; color: #86efac; white-space: pre-wrap; word-break: break-all;
    padding: 12px 20px 20px; line-height: 1.6; max-height: 80vh; overflow-y: auto;
  }}

  /* ── Image modal ── */
  #img-modal {{ align-items: center; justify-content: center; padding: 24px; }}
  #img-modal .modal-box {{ background: transparent; box-shadow: none; max-width: none; width: auto; }}
  #img-modal img {{ border-radius: 8px; max-width: 90vw; max-height: 90vh; display: block; }}
</style>
</head>
<body>

<!-- Image modal -->
<div id="img-modal" class="modal-overlay" onclick="closeAll()">
  <div class="modal-box" onclick="event.stopPropagation()">
    <button class="modal-close" onclick="closeAll()">✕</button>
    <div id="img-content"></div>
  </div>
</div>

<!-- Step replay modal -->
<div id="replay-modal" class="modal-overlay" onclick="closeAll()">
  <div class="modal-box" onclick="event.stopPropagation()">
    <button class="modal-close" onclick="closeAll()">✕</button>
    <div id="replay-title" style="padding:16px 20px 0;font-size:14px;font-weight:600;color:#374151;"></div>
    <div id="replay-content" class="step-grid"></div>
  </div>
</div>

<!-- Log modal -->
<div id="log-modal" class="modal-overlay" onclick="closeAll()">
  <div class="modal-box" onclick="event.stopPropagation()">
    <button class="modal-close" onclick="closeAll()">✕</button>
    <div class="modal-title">Run Log</div>
    <pre id="log-content"></pre>
  </div>
</div>

<div style="max-width:1400px;margin:0 auto;">
  <div style="margin-bottom:20px;">
    <h1 style="font-size:22px;font-weight:700;margin-bottom:6px;">
      Test Report — {escape(suite_name)}
    </h1>
    <div style="font-size:13px;color:#6b7280;display:flex;gap:24px;flex-wrap:wrap;">
      <span>Run ID: <code style="font-size:11px;">{escape(run_id)}</code></span>
      <span>Device: <strong>{escape(run.device_id)}</strong></span>
      <span>Model: <strong>{escape(run.provider)}/{escape(run.model)}</strong></span>
      <span>Started: <strong>{_fmt_dt(run.created_at)}</strong></span>
      <span>Finished: <strong>{_fmt_dt(run.finished_at)}</strong></span>
      <span>Status: {_badge(run.status)}</span>
    </div>
    <div style="font-size:11px;color:#9ca3af;margin-top:4px;">Generated {generated_at}</div>
  </div>

  {stat_cards}

  <table>
    <thead>
      <tr>
        <th style="width:36px;">#</th>
        <th>测试路径 / 期望结果</th>
        <th style="width:70px;">状态</th>
        <th>原因</th>
        <th style="width:50px;text-align:center;">步数</th>
        <th style="width:60px;text-align:center;">耗时</th>
        <th style="width:60px;text-align:center;">Tokens</th>
        <th style="width:100px;">截图</th>
        <th style="width:80px;">回放</th>
        <th style="width:60px;">日志</th>
      </tr>
    </thead>
    <tbody>{case_rows_html}
    </tbody>
  </table>
</div>

<script>
var STEPS = {steps_json};
var LOGS  = {logs_json};

function closeAll() {{
  document.querySelectorAll('.modal-overlay').forEach(function(m) {{
    m.classList.remove('active');
  }});
  document.getElementById('img-content').innerHTML   = '';
  document.getElementById('replay-content').innerHTML = '';
  document.getElementById('log-content').textContent  = '';
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeAll();
}});

/* ── Image modal ── */
function openImgModal(shotId) {{
  var el = document.getElementById(shotId);
  if (!el) return;
  var img = el.querySelector('img');
  if (!img) return;
  document.getElementById('img-content').innerHTML = '';
  document.getElementById('img-content').appendChild(img.cloneNode(true));
  document.getElementById('img-modal').classList.add('active');
}}

/* ── Step replay modal ── */
function openReplay(key) {{
  var steps = STEPS[String(key)];
  if (!steps || !steps.length) return;
  var container = document.getElementById('replay-content');
  container.innerHTML = '';

  steps.forEach(function(sl) {{
    var fnMatch = sl.action.match(/^(\\w+)/);
    var fnName  = fnMatch ? fnMatch[1] : sl.action;

    var card = document.createElement('div');
    card.className = 'step-card';

    var hdr = '<div class="step-card-header">'
      + '<div class="step-num">' + sl.step + '</div>'
      + '<div class="step-fn">' + escH(fnName) + '</div>'
      + '</div>';

    var shot = sl.screenshot_b64
      ? '<img src="data:image/png;base64,' + sl.screenshot_b64 + '" alt="step ' + sl.step + '" loading="lazy" />'
      : '';

    var thought = sl.thought
      ? '<div class="step-thought">&#x1F4AD; ' + escH(sl.thought) + '</div>'
      : '';

    var action = sl.action
      ? '<div class="step-action"><span style="color:#3b82f6">&rarr;</span> ' + escH(sl.action) + '</div>'
      : '';

    var result = sl.action_result
      ? '<div class="step-result"><span style="color:#16a34a">&#x21B3;</span> ' + escH(sl.action_result) + '</div>'
      : '';

    card.innerHTML = hdr + shot + thought + action + result;
    container.appendChild(card);
  }});

  document.getElementById('replay-title').textContent = 'Step Replay — ' + steps.length + ' steps';
  document.getElementById('replay-modal').classList.add('active');
}}

/* ── Log modal ── */
function openLog(key) {{
  var log = LOGS[String(key)] || '(no log)';
  document.getElementById('log-content').textContent = log;
  document.getElementById('log-modal').classList.add('active');
  // Scroll to bottom of log
  var el = document.getElementById('log-content');
  setTimeout(function() {{ el.scrollTop = el.scrollHeight; }}, 50);
}}

function escH(s) {{
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}}
</script>
</body>
</html>"""
