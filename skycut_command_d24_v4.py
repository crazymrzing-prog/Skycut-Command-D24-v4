#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Skycut Command D24-v4 - CUT + SCORE + DRAW
# AKA T22
#
# This is the Inkscape entry point (the script an .inx file's
# <command> points to). All heavy lifting lives in the sibling
# skycut_*_d24_v4.py modules in this same directory:
#
#   skycut_constants_d24_v4.py  - shared master switches / numeric constants
#   skycut_geometry_d24_v4.py   - pure vector math + Bezier flattening
#   skycut_arrows_d24_v4.py     - debug direction-arrow polylines
#   skycut_pathbuild_d24_v4.py  - knife-offset corner overshoot/return/overcut
#   skycut_reference_d24_v4.py  - wysiwyg/origin/contourcut reference-box logic
#   skycut_layers_d24_v4.py     - SVG layer management + geometry collection (mixin)
#   skycut_hpgl_d24_v4.py        - HPGL command assembly
#   skycut_output_d24_v4.py      - save-to-file / send-to-cutter delivery
#   skycut_usb_d24_v4.py         - Windows USB-Printer device discovery + raw send,
#                                  used by skycut_output_d24_v4.py for Send to Machine USB
#
# This is a standalone d24_v4 extension - it does not share any module
# files with the earlier d24_1 extension, so both can be installed in
# Inkscape's extensions folder at the same time without collisions.
#
# TOOL MODEL
# ----------
# Settings (speed/up_speed/offset/overcut/passes) belong to a
# PHYSICAL SIDE - the Left Tool head or the Right Tool head -
# because that's what's actually mounted in the machine. Pressure is
# the one exception: each side now carries THREE pressures, Cut
# Pressure, Score Pressure, and Crease / Draw Pressure
# (left_cut_pressure/left_score_pressure/left_crease_draw_pressure,
# right_cut_pressure/right_score_pressure/right_crease_draw_pressure),
# since a single physical head can be asked to do a Full Cut pass and
# a Kiss Cut pass in the same run (see "cut_score" below) and those
# two passes may need different blade pressure even though they share
# the same offset, overcut, speed and up-speed. Crease and Draw share
# one pressure field between them (Crease / Draw Pressure) rather
# than each getting its own - a side is only ever assigned to one of
# the two at a time anyway.
#
# Each side is independently pointed at a source layer via
# left_tool_layer / right_tool_layer, one of:
#   cut       - Full Cut only
#   score     - Kiss Cut only
#   cut_score - Full Cut AND Kiss Cut, both on this one physical head:
#               Kiss Cut geometry is emitted first, and Full Cut data
#               is appended straight after it (same P0/P1 tool
#               select, no repeated IN;/TB; preamble - just fresh
#               US/VS/!FS blocks per real layer since Cut Pressure
#               and Score Pressure differ). See REAL_KINDS_FOR_TASK in
#               skycut_constants_d24_v4.py and _emit_side_block() in
#               skycut_hpgl_d24_v4.py.
#   crease    - Crease Tool
#   draw      - Draw
#   none      - unused
#
# Both sides must not include any of the same real layer (e.g. Left
# "cut_score" and Right "cut" both touch Full Cut) - effect() errors
# out and aborts in that case, the same as the old single-kind
# same-layer check.
#
# "Draw" and "Crease" are pen/blade-free passes - they reuse the same
# speed/pressure machinery as cut and score, but knife-offset
# compensation doesn't make physical sense for them, so effect()
# warns (without blocking the job) if a side assigned to either has a
# nonzero offset.

import inkex

from skycut_layers_d24_v4 import SkyCutLayerMixin
from skycut_pathbuild_d24_v4 import build_cut_path
from skycut_reference_d24_v4 import compute_reference_box, get_contour_mark_paths, ReferenceError
from skycut_hpgl_d24_v4 import assemble_hpgl, build_render_segments
from skycut_output_d24_v4 import save_hpgl_file, send_hpgl, send_hpgl_usb
from skycut_viewer_d24_v4 import open_hpgl_viewer
from skycut_layer_setup_d24_v4 import run_layer_setup
from skycut_add_marks_d24_v4 import run_add_marks
from skycut_constants_d24_v4 import SOURCE_LAYER_NAMES, LAYER_DISPLAY_NAMES, REAL_KINDS_FOR_TASK

# Actual SVG layer names to search the document for, per real kind -
# see SOURCE_LAYER_NAMES in skycut_constants_d24_v4.py (also used by
# skycut_layer_setup_d24_v4.py, so they stay in sync).
LAYER_LABELS = SOURCE_LAYER_NAMES


def _pressure_for(spec, real_kind):
    """
    Look up the pressure to use for one real layer kind within a
    side. Kiss Cut uses that side's Score Pressure; Crease and Draw
    both use that side's Crease / Draw Pressure; Full Cut uses that
    side's Cut Pressure.
    """
    if real_kind == "score":
        return spec["score_pressure"]
    if real_kind in ("crease", "draw"):
        return spec["crease_draw_pressure"]
    return spec["cut_pressure"]


class SkyCutCommand_D24_V4(SkyCutLayerMixin, inkex.EffectExtension):

    # ------------------------------------------------------
    def add_arguments(self, p):
        # Notebook tab selector (Settings/Help) - value itself isn't
        # used by effect(), but Inkscape always passes it as a CLI
        # arg for any <param type="notebook">, so argparse needs to
        # know about it or every run fails with "unrecognized
        # arguments: --tab=...".
        p.add_argument("--tab", default="settings")
        # Left Tool
        p.add_argument("--left_speed", type=int, default=5)
        p.add_argument("--left_up_speed", type=int, default=5)
        p.add_argument("--left_cut_pressure", type=int, default=5)
        p.add_argument("--left_score_pressure", type=int, default=5)
        p.add_argument("--left_crease-draw_pressure", type=int, default=5)
        p.add_argument("--left_offset_mm", type=float, default=0.30)
        p.add_argument("--left_overcut_mm", type=float, default=1.0)
        p.add_argument("--left_passes", type=int, default=1)
        p.add_argument("--left_tool_layer", default="cut")
        # Right Tool
        p.add_argument("--right_speed", type=int, default=5)
        p.add_argument("--right_up_speed", type=int, default=5)
        p.add_argument("--right_cut_pressure", type=int, default=5)
        p.add_argument("--right_score_pressure", type=int, default=5)
        p.add_argument("--right_crease-draw_pressure", type=int, default=5)
        p.add_argument("--right_offset_mm", type=float, default=0.0)
        p.add_argument("--right_overcut_mm", type=float, default=0.0)
        p.add_argument("--right_passes", type=int, default=1)
        p.add_argument("--right_tool_layer", default="score")
        # Modes
        p.add_argument("--cut_mode", default="origin")
        p.add_argument("--operation_mode", default="left_first")
        # Developer Use
        p.add_argument("--preview_enable", type=inkex.Boolean, default=False)
        # Marks Setting
        p.add_argument("--mark_mode", default="objects")
        p.add_argument("--mark_distance_mm", type=float, default=5.0)
        p.add_argument("--mark_arm_mm", type=float, default=10.0)
        p.add_argument("--mark_stroke_mm", type=float, default=1.0)
        # Action
        p.add_argument("--output_mode", default="send")
        p.add_argument("--ip", default="192.168.16.200")
        p.add_argument("--port", type=int, default=8080)
        p.add_argument("--usb_device_path", default="")

    # ======================================================
    def effect(self):
        # "Layer Setup" and "Add Marks" each run their own module
        # only, skipping the entire cut/score/draw/crease pipeline
        # below.
        if self.options.output_mode == "layer_setup":
            run_layer_setup(self.svg)
            return

        if self.options.output_mode == "add_marks":
            run_add_marks(self.svg, self.options.mark_mode,
                           self.options.mark_distance_mm,
                           self.options.mark_arm_mm,
                           self.options.mark_stroke_mm)
            return

        side_specs = [
            {
                "side": "left",
                "kind": self.options.left_tool_layer,
                "speed": self.options.left_speed,
                "up_speed": self.options.left_up_speed,
                "cut_pressure": self.options.left_cut_pressure,
                "score_pressure": self.options.left_score_pressure,
                "crease_draw_pressure": self.options.left_crease_draw_pressure,
                "offset": self.options.left_offset_mm,
                "overcut": self.options.left_overcut_mm,
            },
            {
                "side": "right",
                "kind": self.options.right_tool_layer,
                "speed": self.options.right_speed,
                "up_speed": self.options.right_up_speed,
                "cut_pressure": self.options.right_cut_pressure,
                "score_pressure": self.options.right_score_pressure,
                "crease_draw_pressure": self.options.right_crease_draw_pressure,
                "offset": self.options.right_offset_mm,
                "overcut": self.options.right_overcut_mm,
            },
        ]

        # Rect/circle/ellipse/etc drawn with Inkscape's shape tools
        # aren't <svg:path> elements, so collect_layer() below would
        # silently skip them - they'd just never get cut/scored, with
        # no visual difference on the canvas to explain why. Checked
        # across ALL FOUR source layers every run (not just whichever
        # one Left/Right Tool is currently assigned to) so a stray
        # shape sitting in an unused layer gets caught now rather
        # than surfacing as a surprise the next time someone runs
        # that layer. Convert them to real paths automatically (the
        # same result as Path > Object to Path), then stop so the
        # person can check the result before anything gets sent - do
        # NOT proceed straight into cutting off an automatic
        # conversion within the same run.
        offenders = self.find_unconverted_shapes(list(LAYER_LABELS.values()))
        if offenders:
            by_layer = {}
            for el, source_layer in offenders:
                by_layer.setdefault(source_layer, []).append(el)

            breakdown = "\n".join(
                f"  {source_layer}: {len(els)} shape(s) "
                f"({', '.join(sorted({e.tag.split('}')[-1] for e in els}))})"
                for source_layer, els in by_layer.items()
            )

            self.convert_shapes_to_paths([el for el, _ in offenders])

            inkex.errormsg(
                "Found shapes that weren't paths yet, so they would have been "
                "skipped when cutting:\n\n"
                f"{breakdown}\n\n"
                "They've been converted to paths now. Check the result on the "
                "canvas - if it looks right, run this again to send the job."
            )
            return

        # Expand each side's Task Selection into the real source-layer
        # kind(s) it actually touches - "cut_score" is two (score then
        # cut), everything else is one (or none, for "none").
        for spec in side_specs:
            spec["real_kinds"] = list(REAL_KINDS_FOR_TASK.get(spec["kind"], [spec["kind"]]))
            spec["pressures"] = {rk: _pressure_for(spec, rk) for rk in spec["real_kinds"]}

        # Left and Right must not share any real layer - ambiguous
        # (which side's offset/overcut applies?) and almost certainly
        # a misconfiguration. Both set to None is fine and caught
        # later by the "no geometry found" check instead.
        overlap = set(side_specs[0]["real_kinds"]) & set(side_specs[1]["real_kinds"])
        if overlap:
            names = ", ".join(
                LAYER_DISPLAY_NAMES.get(k, k.title())
                for k in ("cut", "score", "crease", "draw") if k in overlap
            )
            inkex.errormsg(
                f"Left Tool and Right Tool both include {names}; "
                "assign them to non-overlapping layers."
            )
            return

        # Draw and Crease are pen/blade-free passes (no knife-offset
        # compensation makes physical sense) - warn but keep going;
        # this isn't fatal the way a missing layer or reference box is.
        for spec in side_specs:
            if spec["kind"] in ("draw", "crease") and spec["offset"] != 0:
                dup = LAYER_DISPLAY_NAMES.get(spec["kind"], spec["kind"].title())
                inkex.errormsg(
                    f"Warning: {spec['side'].title()} Tool is assigned to "
                    f"{dup} but its offset is {spec['offset']}mm "
                    "(not 0). Offset should normally be 0 for Draw/Crease - continuing anyway."
                )

        # Collect each real source layer once, shared by any side(s)
        # that touch it (including both real kinds within one side's
        # own "cut_score"). A side assigned to None contributes
        # nothing and is never collected.
        layer_cache = {}

        def layer_paths(real_kind):
            if real_kind not in layer_cache:
                layer_cache[real_kind] = self.collect_layer(LAYER_LABELS[real_kind], real_kind)
            return layer_cache[real_kind]

        for spec in side_specs:
            spec["raw_paths"] = [p for rk in spec["real_kinds"] for p in layer_paths(rk)]

        if not any(spec["raw_paths"] for spec in side_specs):
            used_kinds = sorted({rk for spec in side_specs for rk in spec["real_kinds"]})
            lines = []
            for rk in used_kinds:
                layer_name = LAYER_LABELS[rk]
                if self.layer_exists(layer_name):
                    lines.append(f'  "{layer_name}" - layer exists, but has no cuttable path geometry in it')
                else:
                    lines.append(f'  "{layer_name}" - no layer with this name found in the document')
            inkex.errormsg(
                "No geometry found for the current Left/Right Tool selection; "
                "aborting.\n\n"
                + "\n".join(lines) +
                "\n\nLayer names are matched case-insensitively, but must otherwise "
                "match exactly. If a layer is missing, run Output \u2192 Layer Setup "
                "to create the standard set, or check for a typo/rename. If a "
                "layer exists but is empty, make sure your artwork was actually "
                "drawn inside it (and not just visually overlapping it from "
                "another layer)."
            )
            return

        all_raw_paths = [p for spec in side_specs for p in spec["raw_paths"]]

        # Bounding reference box (wysiwyg / origin / contourcut) - see
        # skycut_reference_d24_v4.py for the coordinate-system explanation.
        try:
            min_x, min_y, max_x, max_y = compute_reference_box(
                self.svg, all_raw_paths, self.options.cut_mode
            )
        except ReferenceError as e:
            inkex.errormsg(str(e))
            return

        # Debug capture before overshoot (no-ops unless CAPTURE_ENABLE_MASTER)
        debug = self.new_layer("Debug")
        if debug is not None:
            self.capture(all_raw_paths, "Capture After Flatten", parent=debug)
            self.capture(all_raw_paths, "Capture After Dedup", parent=debug)
            self.capture(all_raw_paths, "Capture After Clean", parent=debug)
            self.capture(all_raw_paths, "Capture Before Force CW", parent=debug)
            self.capture(all_raw_paths, "Capture After Force CW", parent=debug)

        # Overshoot / return / overcut, per side (each side has its
        # own offset/overcut, shared across both real kinds when the
        # side is "cut_score" - only pressure differs between them)
        for spec in side_specs:
            spec["paths"] = [
                (build_cut_path(pts, spec["offset"], spec["overcut"]), kind)
                for pts, kind in spec["raw_paths"]
            ]

        if debug is not None:
            for spec in side_specs:
                self.capture(
                    spec["paths"],
                    f"Capture After Overshoot_Returns_Overcuts - {spec['side'].title()}",
                    parent=debug,
                )

        # Developer Use preview layer
        self.preview_paths(side_specs)

        if self.options.preview_enable:
            return

        output_mode = self.options.output_mode

        # Preview Path / Preview Path in Web Browser are QC-only: show
        # them, but don't also save or send. They get structured
        # segments directly (not the flat HPGL text), since that's
        # where the cut/score/draw and tool-side info lives. Unlike
        # the real HPGL output, the viewer intentionally shows the
        # artwork in natural (unrotated) Inkscape orientation -
        # SkyCut's axis swap is a machine-orientation detail that
        # only needs to apply to what's actually sent to the cutter.
        if output_mode in ("preview_path", "preview_web"):
            segments = build_render_segments(side_specs, self.options)

            # The blue "origin" dot marks the reference-box corner
            # that machine (0,0) corresponds to for this mode - NOT
            # necessarily wherever the first cut point happens to
            # land (that's the green dot, and depends on shape sort
            # order). All three modes use bottom-right (max_x, max_y) -
            # this also matches the real HPGL swap formula
            # (hpgl_x = max_y - y, hpgl_y = max_x - x), which only
            # ever sends a reference box's bottom-right corner to
            # machine (0,0), regardless of mode.
            origin_point = (max_x, max_y)

            marks = None
            if self.options.cut_mode == "contourcut":
                try:
                    marks = list(get_contour_mark_paths(self.svg).values())
                except ReferenceError:
                    marks = None

            mode_labels = {
                "origin": "Origin",
                "wysiwyg": "WYSIWYG",
                "contourcut": "Contour Cut",
            }
            mode_label = mode_labels.get(self.options.cut_mode, self.options.cut_mode)

            # Each physical tool side does one or two real-kind
            # passes ("cut_score" is two) - so the viewer legend
            # describes what LEFT and RIGHT actually do in terms of
            # real kinds ("Left: Full Cut" / "Left: Kiss Cut" /
            # "Right: Score"), colored using the configured Preview
            # Style colors. A side assigned to None contributes no
            # entries.
            left_kinds = side_specs[0]["real_kinds"]
            right_kinds = side_specs[1]["real_kinds"]

            # One task-list row per active real-kind pass - shown
            # under the Commands count in the viewer, wired to the
            # ALL/LEFT/RIGHT filter buttons via each row's "side".
            tasks = [
                {
                    "label": LAYER_DISPLAY_NAMES.get(real_kind, real_kind.title()),
                    "side": spec["side"],
                    "side_label": f"{spec['side'].title()} tool",
                    "pressure": spec["pressures"][real_kind],
                    "speed": spec["speed"],
                    "up_speed": spec["up_speed"],
                }
                for spec in side_specs
                for real_kind in spec["real_kinds"]
            ]

            open_hpgl_viewer(segments, page_bounds=(min_x, min_y, max_x, max_y),
                              origin_point=origin_point, marks=marks,
                              mode_label=mode_label,
                              page_size=(max_x - min_x, max_y - min_y),
                              left_kinds=left_kinds, right_kinds=right_kinds,
                              tasks=tasks,
                              force_browser_tab=(output_mode == "preview_web"))
            return

        # HPGL emission
        data = assemble_hpgl(side_specs, self.options, min_x, min_y, max_x, max_y)

        if output_mode == "save_plt":
            saved_path = save_hpgl_file(data, self.options.cut_mode)
            inkex.errormsg(f"PLT file saved successfully:\n{saved_path}")
        elif output_mode == "send_usb":
            success, message = send_hpgl_usb(data, self.options.usb_device_path)
            if not success:
                inkex.errormsg(f"Send failed:\n{message}")
        else:  # "send" (WiFi)
            success, message = send_hpgl(data, self.options.ip, self.options.port)
            if not success:
                inkex.errormsg(f"Send failed:\n{message}")


# ==========================================================
# RUN
# ==========================================================
if __name__ == "__main__":
    SkyCutCommand_D24_V4().run()
