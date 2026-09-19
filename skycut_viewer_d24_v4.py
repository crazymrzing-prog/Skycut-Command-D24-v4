#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_viewer_d24_v4.py

Standalone SkyCut HPGL viewer: canvas-based, with zoom/pan, a grid,
adjustable animation speed, and toggleable travel/points display.

Input is the structured segment list from
skycut_hpgl_d24_v4.build_render_segments() - not the flat HPGL command
string - since that's the only place the cut/score action and
tool-side ("left"/"right") info exists once everything is merged
into one HPGL stream. Coordinates are already in SkyCut's
swapped/inverted axis convention and rounded to the same HPGL
integer units the real output uses, so what's drawn matches the
real output exactly, not an approximation of it.

Controls:
  - RENDER  - jump straight to the fully drawn path
  - ANIMATE - progressive plot animation (toggles to stop)
  - RESET   - clear back to empty
  - ALL / LEFT / RIGHT - filter by physical tool side
  - Travel / Grid / Points checkboxes, animation speed slider
  - Mouse wheel to zoom, drag to pan

Opens in a standalone, chromeless "app mode" browser window rather
than the system default browser (a new tab in whatever browser +
tabs the user already has running), falling back to the system
default browser if no suitable one is found.
"""

import json
import os
import platform
import shutil
import subprocess
import tempfile
import warnings
import webbrowser

from skycut_constants_d24_v4 import PREVIEW_COLORS, LAYER_DISPLAY_NAMES

_VIEWER_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
* { box-sizing: border-box; }

html, body {
    margin: 0;
    width: 100%;
    height: 100%;
    background: #080c14;
    color: #c8d8e8;
    font-family: monospace;
    overflow: hidden;
}

#toolbar {
    height: 45px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    background: #101826;
    border-bottom: 1px solid #223344;
    flex-wrap: wrap;
}

#legend {
    height: 26px;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 0 10px;
    background: #0c1220;
    border-bottom: 1px solid #1a2636;
    font-size: 11px;
    color: #8899aa;
}
#legend .swatch { display: inline-block; width: 14px; height: 3px; margin-right: 5px; vertical-align: middle; }
#legend .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }

button {
    background: #101820;
    color: #00eebb;
    border: 1px solid #00eebb;
    padding: 5px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-family: monospace;
}
button:hover { background: rgba(0,238,187,0.15); }
button.stop { color: #ff5555; border-color: #ff5555; }
button.active { background: #00eebb; color: #08111a; }

.mode-badge {
    background: #00eebb;
    color: #08111a;
    font-weight: bold;
    padding: 4px 10px;
    border-radius: 4px;
    letter-spacing: 0.5px;
}

label { font-size: 11px; color: #8899aa; }

#status { margin-left: auto; color: #00eebb; }

#canvasWrap { position: absolute; top: 71px; bottom: 0; left: 0; right: 0; }

canvas { width: 100%; height: 100%; display: block; background: #ffffff; }

#info {
    position: absolute;
    top: 81px;
    left: 10px;
    padding: 6px 10px;
    background: rgba(10,15,25,.8);
    border: 1px solid #223344;
    font-size: 11px;
}

.task-row { padding: 2px 0; white-space: nowrap; }
.task-row .side-tag { color: #00eebb; }
.task-row.dim { opacity: 0.35; }
#taskListHeader { margin-top: 6px; padding-top: 4px; border-top: 1px solid #223344; }
</style>
</head>
<body>

<div id="toolbar">
  <span id="modeBanner" class="mode-badge">__MODE_LABEL__</span>
  <button id="render">RENDER</button>
  <button id="animate">ANIMATE</button>
  <button id="reset">RESET</button>
  <button id="resetView">RESET VIEW</button>

  <button id="filterAll" class="active">ALL</button>
  <button id="filterLeft">LEFT</button>
  <button id="filterRight">RIGHT</button>

  <label><input type="checkbox" id="travel" checked> Travel</label>
  <label><input type="checkbox" id="grid" checked> Grid</label>
  <label><input type="checkbox" id="points" checked> Points</label>

  <label>Speed <input id="speed" type="range" min="5" max="100" value="40"></label>

  <div id="status">Ready</div>
</div>

<div id="legend">
  __SIDE_LEGEND__
  <span><span class="swatch" style="background:#ffb400"></span>travel</span>
  <span><span class="swatch" style="background:#5a7a9a;border-top:1px dashed #9ab"></span>page/reference</span>
  <span><span class="dot" style="background:#00ff00"></span>start of move</span>
  <span><span class="dot" style="background:#ff3333"></span>end of move</span>
  <span><span class="dot" style="background:#3388ff"></span>origin (0,0)</span>
  <span id="marksLegend" style="display:none"><span class="dot" style="background:#000000"></span>marks</span>
</div>

<div id="canvasWrap">
  <canvas id="canvas"></canvas>
  <div id="info"></div>
</div>

<script>

// SEGMENTS: [{kind:"cut"|"score"|"draw"|"crease", side:"left"|"right", points:[[x,y],...]}, ...]
// PAGE_BOUNDS: [minX, minY, maxX, maxY] or null - already in the same
// coordinate space as SEGMENTS' points.
// PAGE_SIZE: [width, height] of the reference box, for the on-canvas
// dimension label.
// ORIGIN_POINT: [x, y] or null - the reference-box corner that maps
// to machine (0,0) for the active cut_mode.
// MARKS: [[[x,y],...], ...] or null - one polyline per Contour Cut
// registration mark (its full path geometry, not just one point).
const SEGMENTS = __SEGMENTS_JSON__;
const PAGE_BOUNDS = __PAGE_BOUNDS_JSON__;
const PAGE_SIZE = __PAGE_SIZE_JSON__;
const ORIGIN_POINT = __ORIGIN_POINT_JSON__;
const MARKS = __MARKS_JSON__;

// Fixed preview colors by operation kind (see PREVIEW_COLORS in
// skycut_constants.py - not user-configurable, edit the values
// there). Two sides doing the same kind of work look the same.
const KIND_COLORS = __KIND_COLORS_JSON__;

// TASKS: [{label, side, side_label, pressure, speed, up_speed}, ...] -
// one row per active real-kind pass, for the task list rendered
// under the Commands count in #info. Wired to the ALL/LEFT/RIGHT
// filter buttons: a row dims when its side doesn't match the
// current filter.
const TASKS = __TASKS_JSON__;

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

let current = 0;
let animating = false;
let timer = null;

let zoom = 1;
let panX = 0;
let panY = 0;

let dragging = false;
let dragX = 0;
let dragY = 0;

let sideFilter = "all"; // "all" | "left" | "right"

// -------------------------------------------------
// Build a flat, chronologically-ordered move list (from -> to pairs)
// out of the structured segments, the same way the real HPGL stream
// is emitted: each segment's first point is a pen-up travel move
// from wherever the tool last was, the rest are pen-down draws.
// -------------------------------------------------
function buildMoves(segments) {
    // Start from the machine origin (blue dot), not a fake (0,0) -
    // the tool is assumed to begin at "home" before the job starts,
    // so the very first travel line should originate there instead
    // of from wherever the raw natural-space (0,0) happens to sit.
    let x = ORIGIN_POINT ? ORIGIN_POINT[0] : 0;
    let y = ORIGIN_POINT ? ORIGIN_POINT[1] : 0;
    const result = [];
    for (const seg of segments) {
        seg.points.forEach((pt, i) => {
            const [nx, ny] = pt;
            result.push({
                type: i === 0 ? "travel" : seg.kind, // "travel" | "cut" | "score"
                side: seg.side,
                fx: x, fy: y,
                x: nx, y: ny,
            });
            x = nx; y = ny;
        });
    }
    // The real HPGL stream always ends with a travel move back to
    // machine origin ("U0,0;"), regardless of which tool(s) did the
    // work - so it's tagged side:null and always shown, even when
    // filtering to Left/Right only.
    if (ORIGIN_POINT && result.length) {
        result.push({
            type: "travel",
            side: null,
            fx: x, fy: y,
            x: ORIGIN_POINT[0], y: ORIGIN_POINT[1],
        });
    }
    return result;
}

const allMoves = buildMoves(SEGMENTS);
let moves = allMoves;

// -------------------------------------------------
// Fixed view bounds computed once from the *unfiltered* geometry
// plus the page border, so switching ALL/LEFT/RIGHT doesn't rescale
// or jump the view.
// -------------------------------------------------
function computeBounds(allMovesList, pageBounds) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const m of allMovesList) {
        // Only the destination (x,y) of each move is a real point on
        // the artwork. fx/fy for every move after the first just
        // duplicates the previous move's destination anyway, and
        // fx/fy for the very first move is a fake (0,0) "wherever the
        // tool was before this job" placeholder - not a real point,
        // and including it would drag the view toward the coordinate
        // origin even when the actual artwork sits elsewhere on the
        // page entirely.
        minX = Math.min(minX, m.x);
        minY = Math.min(minY, m.y);
        maxX = Math.max(maxX, m.x);
        maxY = Math.max(maxY, m.y);
    }
    if (pageBounds) {
        const [pMinX, pMinY, pMaxX, pMaxY] = pageBounds;
        minX = Math.min(minX, pMinX); maxX = Math.max(maxX, pMaxX);
        minY = Math.min(minY, pMinY); maxY = Math.max(maxY, pMaxY);
    }
    if (ORIGIN_POINT) {
        minX = Math.min(minX, ORIGIN_POINT[0]); maxX = Math.max(maxX, ORIGIN_POINT[0]);
        minY = Math.min(minY, ORIGIN_POINT[1]); maxY = Math.max(maxY, ORIGIN_POINT[1]);
    }
    if (MARKS) {
        for (const markPath of MARKS) {
            for (const [mx, my] of markPath) {
                minX = Math.min(minX, mx); maxX = Math.max(maxX, mx);
                minY = Math.min(minY, my); maxY = Math.max(maxY, my);
            }
        }
    }
    if (!isFinite(minX)) { minX = 0; minY = 0; maxX = 1; maxY = 1; }
    return { minX, minY, maxX, maxY };
}

const VIEW = computeBounds(allMoves, PAGE_BOUNDS);

if (MARKS && MARKS.length) {
    document.getElementById("marksLegend").style.display = "";
}

function renderInfo() {
    let html = "Commands: " + moves.length;
    if (TASKS && TASKS.length) {
        html += '<div id="taskListHeader">';
        for (const t of TASKS) {
            const active = (sideFilter === "all" || sideFilter === t.side);
            html += '<div class="task-row' + (active ? "" : " dim") + '">'
                  + t.label + '&nbsp;&nbsp;<span class="side-tag">' + t.side_label + '</span>&nbsp;&nbsp;'
                  + 'Pressure ' + t.pressure + '&nbsp;&nbsp;'
                  + 'Speed ' + t.speed + '&nbsp;&nbsp;'
                  + 'Up Speed ' + t.up_speed
                  + '</div>';
        }
        html += '</div>';
    }
    document.getElementById("info").innerHTML = html;
}
renderInfo();

// -------------------------------------------------
// Drawing engine
// -------------------------------------------------
function resize() {
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
}

function transform(x, y) {
    const w = (VIEW.maxX - VIEW.minX) || 1;
    const h = (VIEW.maxY - VIEW.minY) || 1;

    const scale = Math.min(canvas.width / w, canvas.height / h) * 0.8 * zoom;

    // Segments are natural SVG-space coordinates (X right, Y down -
    // origin top-left), which is the same orientation canvas pixels
    // already use. No Y-flip needed here: that was only correct for
    // the old Y-up, bottom-left machine-space data this viewer used
    // to parse directly from raw HPGL.
    return {
        x: (x - VIEW.minX) * scale + panX + (canvas.width - w * scale) / 2,
        y: (y - VIEW.minY) * scale + panY + (canvas.height - h * scale) / 2,
    };
}

function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (document.getElementById("grid").checked) {
        ctx.strokeStyle = "rgba(0,40,80,.10)";
        ctx.lineWidth = 1;
        for (let x = 0; x < canvas.width; x += 50) {
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
        }
        for (let y = 0; y < canvas.height; y += 50) {
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
        }
    }

    // Page/reference border.
    if (PAGE_BOUNDS) {
        const [pMinX, pMinY, pMaxX, pMaxY] = PAGE_BOUNDS;
        const a = transform(pMinX, pMinY);
        const b = transform(pMaxX, pMaxY);
        ctx.strokeStyle = "rgba(60,90,120,.6)";
        ctx.setLineDash([4, 3]);
        ctx.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y),
                        Math.abs(b.x - a.x), Math.abs(b.y - a.y));
        ctx.setLineDash([]);

        if (PAGE_SIZE) {
            const [pw, ph] = PAGE_SIZE;
            ctx.fillStyle = "rgba(50,70,95,.9)";
            ctx.font = "22px monospace";

            // W label centered above the midpoint of the top edge.
            const topMid = transform((pMinX + pMaxX) / 2, pMinY);
            const wText = "W: " + pw.toFixed(1) + " mm";
            const wWidth = ctx.measureText(wText).width;
            ctx.fillText(wText, topMid.x - wWidth / 2, topMid.y - 12);

            // H label rotated 90°, running alongside the right edge
            // (reads bottom-to-top).
            const rightMid = transform(pMaxX, (pMinY + pMaxY) / 2);
            const hText = "H: " + ph.toFixed(1) + " mm";
            const hWidth = ctx.measureText(hText).width;
            ctx.save();
            ctx.translate(rightMid.x + 20, rightMid.y);
            ctx.rotate(-Math.PI / 2);
            ctx.fillText(hText, -hWidth / 2, 0);
            ctx.restore();
        }
    }

    // Contour Cut registration marks - drawn as their actual path
    // geometry (the "mark vector"), not reduced to a single point.
    if (MARKS) {
        ctx.strokeStyle = "#000000";
        ctx.lineWidth = 1.5;
        for (const markPath of MARKS) {
            if (markPath.length < 1) continue;
            ctx.beginPath();
            markPath.forEach(([mx, my], i) => {
                const p = transform(mx, my);
                if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
            });
            ctx.stroke();
        }
    }

    const limit = animating ? current : moves.length;
    const showTravel = document.getElementById("travel").checked;

    // Color by operation kind (Cut/Score/Draw) using the configured
    // Preview Style colors, rather than by physical side - two sides
    // doing the same kind of work should look identical.
    for (let i = 0; i < limit; i++) {
        const m = moves[i];
        const a = transform(m.fx, m.fy);
        const b = transform(m.x, m.y);

        if (m.type === "travel") {
            if (!showTravel) continue;
            ctx.strokeStyle = "rgba(255,180,0,.45)";
            ctx.setLineDash([5, 5]);
        } else {
            ctx.strokeStyle = KIND_COLORS[m.type] || "#000000";
            ctx.setLineDash([]);
        }

        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
    }
    ctx.setLineDash([]);

    if (document.getElementById("points").checked && moves.length) {
        const realMoves = moves.filter(m => m.side !== null);

        // moves[0].x/y is where the tool actually goes first (the
        // real start of the job). moves[0].fx/fy is a fake (0,0)
        // placeholder for "wherever the tool was before this job",
        // not a real point on the artwork.
        if (realMoves.length) {
            const s = transform(realMoves[0].x, realMoves[0].y);
            ctx.fillStyle = "#00ff00";
            ctx.beginPath(); ctx.arc(s.x, s.y, 5, 0, Math.PI * 2); ctx.fill();
        }

        // Red dot: end of actual cutting - the last real move BEFORE
        // the synthetic return-to-origin leg (which has side:null),
        // so it stays visually distinct from the blue origin dot
        // instead of landing on top of it.
        if (realMoves.length) {
            const last = realMoves[realMoves.length - 1];
            const e = transform(last.x, last.y);
            ctx.fillStyle = "#ff3333";
            ctx.beginPath(); ctx.arc(e.x, e.y, 5, 0, Math.PI * 2); ctx.fill();
        }

        // Blue dot: the reference-box corner that maps to machine
        // (0,0) for this cut_mode - distinct from the green "start of
        // move" dot, which is wherever cutting happens to begin.
        if (ORIGIN_POINT) {
            const o = transform(ORIGIN_POINT[0], ORIGIN_POINT[1]);
            ctx.fillStyle = "#3388ff";
            ctx.beginPath(); ctx.arc(o.x, o.y, 6, 0, Math.PI * 2); ctx.fill();
        }
    }

    document.getElementById("status").innerHTML =
        "Commands: " + moves.length + " | Step: " + current;
}

function redraw() { resize(); draw(); }

// -------------------------------------------------
// Animation
// -------------------------------------------------
function animate() {
    const btn = document.getElementById("animate");
    if (animating) {
        animating = false;
        clearTimeout(timer);
        btn.classList.remove("stop");
        btn.textContent = "ANIMATE";
        return;
    }

    animating = true;
    current = 0;
    btn.classList.add("stop");
    btn.textContent = "STOP";

    function step() {
        if (!animating) return;
        current++;
        draw();
        if (current < moves.length) {
            const speed = 105 - Number(document.getElementById("speed").value); // ms delay, inverted
            timer = setTimeout(step, Math.max(1, speed));
        } else {
            animating = false;
            btn.classList.remove("stop");
            btn.textContent = "ANIMATE";
        }
    }
    step();
}

// -------------------------------------------------
// Reset / filter
// -------------------------------------------------
function reset() {
    animating = false;
    clearTimeout(timer);
    const btn = document.getElementById("animate");
    btn.classList.remove("stop");
    btn.textContent = "ANIMATE";
    current = 0;
    draw();
}

// RESET VIEW is separate from RESET - it only clears zoom/pan back to
// the initial fit-to-screen view, it doesn't touch render/animation
// progress.
function resetView() {
    zoom = 1;
    panX = 0;
    panY = 0;
    draw();
}

function setFilter(which) {
    sideFilter = which;
    moves = which === "all" ? allMoves : allMoves.filter(m => m.side === which || m.side === null);
    renderInfo();
    ["filterAll", "filterLeft", "filterRight"].forEach(id =>
        document.getElementById(id).classList.remove("active"));
    document.getElementById({ all: "filterAll", left: "filterLeft", right: "filterRight" }[which])
        .classList.add("active");
    reset();
}

// -------------------------------------------------
// Mouse zoom / pan
// -------------------------------------------------
canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    zoom *= (e.deltaY < 0) ? 1.2 : (1 / 1.2);
    zoom = Math.max(.2, Math.min(20, zoom));
    draw();
}, { passive: false });

canvas.addEventListener("mousedown", function (e) {
    dragging = true;
    dragX = e.clientX - panX;
    dragY = e.clientY - panY;
});

window.addEventListener("mousemove", function (e) {
    if (!dragging) return;
    panX = e.clientX - dragX;
    panY = e.clientY - dragY;
    draw();
});

window.addEventListener("mouseup", function () { dragging = false; });

// -------------------------------------------------
// Buttons / checkboxes
// -------------------------------------------------
document.getElementById("render").onclick = function () {
    animating = false;
    clearTimeout(timer);
    document.getElementById("animate").classList.remove("stop");
    document.getElementById("animate").textContent = "ANIMATE";
    current = moves.length;
    draw();
};

document.getElementById("animate").onclick = animate;
document.getElementById("reset").onclick = reset;
document.getElementById("resetView").onclick = resetView;

document.getElementById("filterAll").onclick = () => setFilter("all");
document.getElementById("filterLeft").onclick = () => setFilter("left");
document.getElementById("filterRight").onclick = () => setFilter("right");

document.getElementById("travel").addEventListener("change", draw);
document.getElementById("grid").addEventListener("change", draw);
document.getElementById("points").addEventListener("change", draw);

window.addEventListener("resize", redraw);

redraw();
</script>
</body>
</html>
"""


def build_viewer_html(segments, page_bounds=None, origin_point=None, marks=None,
                       mode_label="", page_size=None, left_kinds=None, right_kinds=None,
                       tasks=None, title="SkyCut HPGL Preview"):
    """
    Return a standalone HTML document rendering `segments` (from
    skycut_hpgl_d24_v4.build_render_segments) with RENDER/ANIMATE/RESET,
    ALL/LEFT/RIGHT tool-side filter controls, zoom/pan, and a grid.

    page_bounds, if given, is (min_x, min_y, max_x, max_y) in the same
    coordinate space as the points inside `segments`.

    origin_point, if given, is (x, y) - the reference-box corner that
    machine (0,0) maps to for the active cut_mode, drawn as a blue
    dot. This is distinct from the green "start of move" dot, which
    is wherever cutting actually happens to begin.

    marks, if given, is a list of mark polylines - each mark's own
    full path geometry as a list of (x, y) points - drawn as its
    actual shape rather than reduced to a single point.

    mode_label is shown as a banner badge (e.g. "Origin", "WYSIWYG",
    "Contour Cut"). page_size, if given, is (width, height) shown as
    a dimension label near the page/reference border.

    left_kinds/right_kinds, if given, are lists of real kinds
    ("cut"/"score"/"draw"/"crease" - empty or None if that side is
    unassigned) - one legend entry is built per entry, so a
    "cut_score" side (real_kinds ["score", "cut"]) shows two entries,
    e.g. "Left: Kiss Cut" and "Left: Full Cut", each in its own
    color. Colors come from the fixed PREVIEW_COLORS constant, not
    from a per-call style argument - edit skycut_constants_d24_v4.py
    to change them.

    tasks, if given, is a list of dicts - one per active real-kind
    pass - each with keys label ("Full Cut"), side ("left"/"right",
    matching each segment's own side value so it lines up with the
    ALL/LEFT/RIGHT filter buttons), side_label ("Left tool"),
    pressure, speed, up_speed. Rendered as a small task list just
    under the Commands count in the #info panel; a row dims when the
    active filter doesn't match its side.
    """
    def color_for(kind):
        return PREVIEW_COLORS.get(kind, "#000000")

    def entries_for(side_label, kinds):
        parts = []
        for kind in (kinds or []):
            parts.append(
                f'<span><span class="swatch" style="background:{color_for(kind)}"></span>'
                f'{side_label}: {LAYER_DISPLAY_NAMES.get(kind, kind.title())}</span>'
            )
        return parts

    side_legend_parts = entries_for("Left", left_kinds) + entries_for("Right", right_kinds)
    side_legend_html = "\n  ".join(side_legend_parts)

    kind_colors = {k: PREVIEW_COLORS[k] for k in ("cut", "score", "draw", "crease")}

    return (
        _VIEWER_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__MODE_LABEL__", mode_label or "")
        .replace("__SIDE_LEGEND__", side_legend_html)
        .replace("__SEGMENTS_JSON__", json.dumps(segments))
        .replace("__PAGE_BOUNDS_JSON__", json.dumps(list(page_bounds) if page_bounds else None))
        .replace("__PAGE_SIZE_JSON__", json.dumps(list(page_size) if page_size else None))
        .replace("__ORIGIN_POINT_JSON__", json.dumps(list(origin_point) if origin_point else None))
        .replace("__MARKS_JSON__",
                 json.dumps([[list(pt) for pt in m] for m in marks] if marks else None))
        .replace("__KIND_COLORS_JSON__", json.dumps(kind_colors))
        .replace("__TASKS_JSON__", json.dumps(tasks or []))
    )


def _find_app_mode_browser():
    """
    Look for an installed Chromium-based browser that supports
    --app= (a chromeless standalone window), so the viewer opens as
    its own dedicated tool window instead of just another tab in
    whatever full browser + tab set the user already has running.
    Returns the executable path, or None if none was found.
    """
    system = platform.system()

    if system == "Windows":
        program_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LocalAppData", ""),
        ]
        relative_paths = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for base in program_files:
            for rel in relative_paths:
                candidate = os.path.join(base, rel)
                if os.path.isfile(candidate):
                    return candidate
        for exe in ("chrome.exe", "msedge.exe", "brave.exe"):
            path = shutil.which(exe)
            if path:
                return path
        return None

    if system == "Darwin":
        mac_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        for path in mac_paths:
            if os.path.isfile(path):
                return path
        return None

    # Linux and everything else.
    for exe in ("google-chrome", "google-chrome-stable", "chromium",
                "chromium-browser", "microsoft-edge", "microsoft-edge-stable",
                "brave-browser"):
        path = shutil.which(exe)
        if path:
            return path
    return None


def _open_standalone(file_path):
    """
    Try to open `file_path` in a chromeless app-mode browser window.
    Returns True on success, False if no suitable browser was found
    or launching it failed - caller should fall back to
    webbrowser.open() in that case.

    The browser window is meant to outlive this script (Inkscape's
    extension process exits right after this call returns, while the
    viewer window should stay open). That's normal and fine, but
    Python's subprocess module raises a ResourceWarning when a Popen
    object is garbage-collected while its child is still running,
    since it can't tell "intentionally detached" apart from "forgot
    to wait() on this" - Inkscape then surfaces that warning to the
    user as if something had gone wrong. We suppress it here since we
    know exactly which case this is.
    """
    browser = _find_app_mode_browser()
    if not browser:
        return False

    kwargs = {}
    if platform.system() == "Windows":
        # Fully detach from Inkscape's console/process group so the
        # viewer window isn't tied to the extension process's lifetime.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            subprocess.Popen(
                [browser, f"--app=file://{file_path}",
                 "--window-size=1280,860", "--new-window"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        return True
    except Exception:
        return False


def open_hpgl_viewer(segments, page_bounds=None, origin_point=None, marks=None,
                      mode_label="", page_size=None, left_kinds=None, right_kinds=None,
                      tasks=None, title="SkyCut HPGL Preview", force_browser_tab=False):
    """
    Write the viewer HTML to a temp file and open it.

    left_kinds/right_kinds are lists of real kinds (see
    build_viewer_html) - a "cut_score" side passes both of its real
    kinds so the legend shows an entry for each.

    tasks, if given, is passed straight through to build_viewer_html
    (see its docstring) to populate the task list under the Commands
    count.

    By default this tries a standalone, chromeless app-mode browser
    window first, falling back to the system default browser if none
    is found. Pass force_browser_tab=True (the "Preview Path in Web
    Browser" output option) to skip the app-mode attempt entirely and
    always open a normal tab in the system default browser instead.

    Returns the path of the temp file written.
    """
    html_doc = build_viewer_html(segments, page_bounds=page_bounds,
                                  origin_point=origin_point, marks=marks,
                                  mode_label=mode_label, page_size=page_size,
                                  left_kinds=left_kinds, right_kinds=right_kinds,
                                  tasks=tasks, title=title)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".html",
                                       delete=False, encoding="utf-8")
    try:
        tmp.write(html_doc)
    finally:
        tmp.close()

    if force_browser_tab or not _open_standalone(tmp.name):
        webbrowser.open(f"file://{tmp.name}")

    return tmp.name
