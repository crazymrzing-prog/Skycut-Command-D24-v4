#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_add_marks_d24_v4.py

Generates L-shaped registration marks - either offset from the
bounding box of all vector objects, or inset from the page border -
plus one orientation indicator line, on a fresh, locked "Marks"
layer. This is the same "Marks" layer skycut_reference.py reads for
ContourCut mode (get_contour_marks/get_contour_mark_paths).

Callable directly as run_add_marks(svg, ...) - e.g. from
skycut_command_d24_v4.py's effect() when Output is set to "Add
Marks" - or run standalone as its own Inkscape effect extension.
"""

import inkex
from inkex import PathElement, Layer, Style, Transform, Group


# ------------------------------------------------------
# Bake group transforms down onto their children, recursively, so
# each object's own bounding_box() reflects its true on-canvas
# geometry.
# ------------------------------------------------------
def _apply_recursive(el):
    if isinstance(el, Group) and el.transform:
        t = el.transform
        for child in el:
            child.transform = t @ child.transform
        el.transform = Transform()
    if hasattr(el, "apply_transform"):
        el.apply_transform()
    for child in el:
        _apply_recursive(child)


# ------------------------------------------------------
# Main
# ------------------------------------------------------
def run_add_marks(svg, mode="objects", distance_mm=5.0, arm_mm=10.0, stroke_mm=1.0):
    """
    Replace the "Marks" layer with fresh L-shaped registration marks
    at each corner (plus one orientation indicator).

    mode="objects": marks sit `distance_mm` outside the bounding box
    of every vector object on the canvas.
    mode="page": marks sit `distance_mm` inside the page border.

    Returns True on success. Returns False (after already showing an
    inkex.errormsg) if mode="objects" and no vector objects exist to
    measure from.
    """
    # ---------- REMOVE EXISTING MARKS LAYER ----------
    for layer in svg.xpath("//svg:g[@inkscape:groupmode='layer']"):
        if layer.label == "Marks":
            layer.delete()

    # ---------- UNITS ----------
    distance = svg.unittouu(f"{distance_mm}mm")
    arm = svg.unittouu(f"{arm_mm}mm")
    stroke = svg.unittouu(f"{stroke_mm}mm")

    indicator_len = svg.unittouu("5mm")
    indicator_gap = svg.unittouu("5mm")

    # ---------- DETERMINE BOUNDING BOX ----------
    if mode == "page":
        bbox = svg.get_page_bbox()
        minx = bbox.left + distance
        miny = bbox.top + distance
        maxx = bbox.right - distance
        maxy = bbox.bottom - distance
    else:
        objects = [el for el in svg.descendants() if isinstance(el, inkex.ShapeElement)]
        if not objects:
            inkex.errormsg("No vector objects found")
            return False
        for el in objects:
            _apply_recursive(el)
        bboxes = [el.bounding_box() for el in objects if el.bounding_box()]
        if not bboxes:
            inkex.errormsg("No vector objects found")
            return False
        minx = min(b.left for b in bboxes) - distance
        maxx = max(b.right for b in bboxes) + distance
        miny = min(b.top for b in bboxes) - distance
        maxy = max(b.bottom for b in bboxes) + distance

    # ---------- CREATE MARKS LAYER ----------
    marks_layer = Layer.new("Marks")
    svg.add(marks_layer)
    marks_layer.set("sodipodi:insensitive", "true")

    # ---------- STYLE ----------
    style = Style({
        "stroke": "#000000",
        "stroke-width": stroke,
        "fill": "none",
        "stroke-linecap": "square"
    })

    # ---------- DRAW CORNER MARKS WITH LABELS ----------
    corners = [
        ("TopLeft", [(minx, miny + arm), (minx, miny), (minx + arm, miny)]),
        ("TopRight", [(maxx - arm, miny), (maxx, miny), (maxx, miny + arm)]),
        ("BottomRight", [(maxx, maxy - arm), (maxx, maxy), (maxx - arm, maxy)]),
        ("BottomLeft", [(minx + arm, maxy), (minx, maxy), (minx, maxy - arm)]),
    ]
    for name, pts in corners:
        path = PathElement()
        path.path = inkex.Path([('M', pts[0]), ('L', pts[1]), ('L', pts[2])])
        path.style = style
        path.set("inkscape:label", name)  # set internal label
        marks_layer.add(path)

    # ---------- ORIENTATION INDICATOR ----------
    x_end = maxx - arm - indicator_gap
    x_start = x_end - indicator_len
    orient = PathElement()
    orient.path = inkex.Path([('M', (x_start, maxy)), ('L', (x_end, maxy))])
    orient.style = style
    orient.set("inkscape:label", "Orientation")  # label the extra horizontal line
    marks_layer.add(orient)

    return True


# ==========================================================
# STANDALONE EXTENSION (optional - Output = "Add Marks" in
# skycut_command_d24_v4.py calls run_add_marks() directly instead)
# ==========================================================
class AddMarks(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--mode", default="objects")
        pars.add_argument("--distance_mm", type=float, default=5.0)
        pars.add_argument("--arm_mm", type=float, default=10.0)
        pars.add_argument("--stroke_mm", type=float, default=1.0)

    def effect(self):
        run_add_marks(self.svg, self.options.mode, self.options.distance_mm,
                       self.options.arm_mm, self.options.stroke_mm)


if __name__ == "__main__":
    AddMarks().run()
