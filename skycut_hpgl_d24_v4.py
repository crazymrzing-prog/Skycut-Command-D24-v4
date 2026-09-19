#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_hpgl_d24_v4.py

Turns flattened cut/score/draw point lists into an HPGL command stream
for the SkyCut cutter, including per-tool speed/pressure setup and the
ContourCut ("TB"/"CT1") registration-mark preamble.

SkyCut uses a different coordinate convention from normal SVG/HPGL:
SVG:
    X right, Y down

SkyCut HPGL:
    axes are swapped and inverted.

Conversion:
    hpgl_x = max_y - y
    hpgl_y = max_x - x

Settings (speed/offset/overcut) live per PHYSICAL SIDE (left tool
head / right tool head) rather than per operation - each side is
independently assigned (via Task Selection) to work the Full Cut
layer, the Kiss Cut layer, BOTH ("cut_score"), the Crease layer, the
Draw layer, or nothing (None). Pressure is the one setting that can
differ within a single side's own block: a side has a Cut Pressure
and a Score Pressure, looked up per real layer kind. A "side" here
is a dict with keys:
    side       - "left" | "right"
    kind       - "cut" | "score" | "cut_score" | "crease" | "draw" | "none"
    real_kinds - ordered list of the real layer kind(s) this side
                 actually emits - e.g. ["score", "cut"] for
                 "cut_score" (Kiss Cut emitted first, Full Cut data
                 appended after it on the same physical head)
    pressures  - {real_kind: pressure} for each entry in real_kinds
    paths      - list of (pts, real_kind) tuples, already run through
                 build_cut_path with this side's offset/overcut
    up_speed, speed, offset - this side's shared settings

Each real_kind within a side gets its own US/VS/!FS block (up_speed
and speed are the same across a side's real_kinds; pressure is not),
so a P0/P1 tool-select is only emitted once per side, but the
US/VS/!FS trio repeats for every real_kind group that side has -
this is how a "cut_score" side runs a Kiss Cut pass first and then
appends a Full Cut pass right after it, each under its own pressure,
without re-selecting the tool head or repeating the job's global
IN;/TB; preamble (that preamble is only ever emitted once, at the
very top of the whole HPGL stream).

operation_mode picks which physical side's block is emitted first
("left_first" / "right_first"); a side whose kind is "none" is
skipped entirely, regardless of order.
"""

from skycut_constants_d24_v4 import SCALE

_SIDE_ORDER = {
    "left_first": ["left", "right"],
    "right_first": ["right", "left"],
}


def emit_polyline(hpgl, pts, max_x, max_y, scale=SCALE):
    """
    Append U/D HPGL commands for one polyline.

    Applies SkyCut coordinate conversion.

    pts have already been through build_cut_path() (see
    skycut_pathbuild.py), which is where knife/score offset actually
    gets applied - as direction-aware overshoot/return excursions at
    each corner, matching a swiveling drag-knife's real behavior
    (blade trails behind the pivot along the current direction of
    travel). On straight runs between corners that trailing model
    needs no correction at all, so no offset is applied here - doing
    so a second time distorted the corner geometry that build_cut_path
    already places precisely.
    """
    for i, (x, y) in enumerate(pts):
        hpgl_x = max_y - y
        hpgl_y = max_x - x

        hpgl.append(
            ("U" if i == 0 else "D") +
            f"{int(round(hpgl_x * scale))},{int(round(hpgl_y * scale))};"
        )


def _tool_command(side):
    """
    SkyCut tool-select command: P0 selects the left tool head, P1
    selects the right tool head. Selected per-block (each side can be
    doing different work), rather than once globally - so left/right
    settings actually take effect independently.
    """
    return "P0;" if side == "left" else "P1;"


def _rounded_points_natural(pts, scale=SCALE):
    """
    Same HPGL-unit rounding as the real output, but WITHOUT the
    SkyCut axis-swap conversion.

    The physical cutter needs the swap (SkyCut's X/Y differ from
    other cutters), but that's a machine-orientation detail, not
    something the person looking at the viewer needs to mentally
    undo - the viewer shows the artwork in its natural Inkscape
    orientation instead. Corner overshoots, overcuts, offset (already
    baked into pts via build_cut_path), and rounding all still match
    the real output exactly - only the final swap step (a display/
    machine-orientation concern, not a geometry one) is skipped here.
    """
    out = []
    for x, y in pts:
        out.append((
            round(x * scale) / scale,
            round(y * scale) / scale,
        ))
    return out


def _emit_side_block(hpgl, side_data, max_x, max_y, scale=SCALE):
    """
    Emit one physical side's block: a single P0/P1 tool-select,
    followed by one US/VS/!FS + polylines group per real kind that
    side touches (in side_data["real_kinds"] order - for "cut_score"
    that's Kiss Cut first, then Full Cut appended right after it).
    up_speed/speed are constant across a side; pressure is looked up
    per real kind from side_data["pressures"], since Cut Pressure and
    Score Pressure can differ.
    """
    hpgl.append(_tool_command(side_data["side"]))
    for real_kind in side_data["real_kinds"]:
        kind_paths = [(pts, k) for pts, k in side_data["paths"] if k == real_kind]
        if not kind_paths:
            continue
        pressure = side_data["pressures"][real_kind]
        hpgl.extend([
            f"US{side_data['up_speed']};",
            f"VS{side_data['speed']};",
            f"!FS{pressure};",
        ])
        for pts, _kind in kind_paths:
            emit_polyline(hpgl, pts, max_x, max_y, scale)


def _side_order(operation_mode):
    """Physical side order (['left','right'] or ['right','left']) for this operation_mode."""
    return list(_SIDE_ORDER.get(operation_mode, ["left", "right"]))


def build_render_segments(sides, options, scale=SCALE):
    """
    Build viewer geometry in natural (unrotated) Inkscape orientation.

    The real HPGL output (assemble_hpgl/emit_polyline) still applies
    SkyCut's axis-swap conversion, since the physical cutter needs
    it. The viewer intentionally does NOT apply that swap - it shows
    the artwork the way it looks in Inkscape, so what's on screen is
    recognizable at a glance, while offsets/overcuts/corner handling
    still match the real output exactly.
    """
    segments = []
    sides_by_id = {s["side"]: s for s in sides}
    for side_name in _side_order(options.operation_mode):
        s = sides_by_id.get(side_name)
        if s is None or s["kind"] == "none":
            continue
        for pts, k in s["paths"]:
            segments.append({
                "kind": k,
                "side": s["side"],
                "points": _rounded_points_natural(pts, scale),
            })
    return segments


def assemble_hpgl(sides, options, min_x, min_y, max_x, max_y, scale=SCALE):
    """
    Build complete HPGL stream for SkyCut.

    `sides` is the list of per-tool dicts described at the top of
    this module (one entry per physical side that has geometry to
    emit).
    """

    hpgl = ["IN;"]

    if options.cut_mode == "contourcut":
        width_units = int(round((max_x - min_x) * scale))
        height_units = int(round((max_y - min_y) * scale))

        hpgl.append(
            f"TB25,{height_units},{width_units};"
        )
        hpgl.append("CT1;")

    hpgl.append("PA;")

    sides_by_id = {s["side"]: s for s in sides}
    for side_name in _side_order(options.operation_mode):
        s = sides_by_id.get(side_name)
        if s is None or s["kind"] == "none":
            continue
        _emit_side_block(hpgl, s, max_x, max_y, scale)

    hpgl.extend([
        "U0,0;",
        "@;"
    ])

    if options.cut_mode == "contourcut":
        hpgl.append("@;")

    return "\n".join(hpgl)
