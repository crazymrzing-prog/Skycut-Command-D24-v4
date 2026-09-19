#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_layers_d24_v4.py

SVG layer management: creating/removing debug and preview layers,
capturing polylines into them for visual inspection, and collecting
+ flattening path geometry out of the "Cut" / "Score" source layers.

Implemented as a mixin (SkyCutLayerMixin) so the main extension class
can inherit both this and inkex.EffectExtension, giving these methods
access to self.svg / self.options exactly as before.
"""

import os
import tempfile

import inkex
from inkex import PathElement, Group
from inkex.paths import CubicSuperPath, Path
from inkex.styles import Style
from inkex.command import inkscape as _inkscape_cli

from skycut_geometry_d24_v4 import flatten_superpath_to_subpaths, dedupe, is_cw
from skycut_arrows_d24_v4 import direction_arrows
from skycut_constants_d24_v4 import CAPTURE_ENABLE_MASTER, CAPTURE_STROKE_WIDTH, FLATTEN_TOLERANCE_MM, PREVIEW_COLORS, LAYER_DISPLAY_NAMES


class SkyCutLayerMixin:
    """Layer/geometry helpers shared by the SkyCut effect extension."""

    # ------------------------------------------------------
    # Tags Inkscape's shape tools (rectangle/ellipse/star/etc.) can
    # leave behind that are NOT <svg:path> elements. collect_layer()
    # only ever looks for svg:path, so anything matching these tags
    # is silently invisible to it - it just won't be cut/scored, with
    # no error and no visual difference on the canvas. (Star/spiral
    # shapes ARE svg:path underneath - sodipodi:type="star"/"spiral" -
    # so they're already picked up fine and aren't included here.)
    # "text" is included too: live text objects (typed with the Text
    # tool, not yet converted via Text > Object to Path) are <svg:text>
    # elements, same blind spot - to_path_element() outlines the
    # glyphs into real path geometry, the same result Inkscape's own
    # Text > Object to Path menu command produces.
    UNCONVERTED_SHAPE_TAGS = ("rect", "circle", "ellipse", "polygon", "polyline", "line", "text")

    def find_unconverted_shapes(self, labels):
        """
        Scan the SVG layers named in `labels` (case-insensitive match,
        same convention as collect_layer) for elements matching
        UNCONVERTED_SHAPE_TAGS.

        Returns a list of (element, source_layer_label) tuples -
        source_layer_label is the layer's actual inkscape:label text
        (original case, not the lowercased match key) so callers can
        report which layer each offender came from. Does not modify
        the document.
        """
        nsmap = {'svg': self.svg.nsmap.get(None),
                 'inkscape': self.svg.nsmap.get('inkscape')}
        targets = {label.strip().lower() for label in labels}
        offenders = []
        for layer_el in self.svg.xpath(".//svg:g[@inkscape:groupmode='layer']", namespaces=nsmap):
            raw_label = layer_el.get("inkscape:label", "") or ""
            if raw_label.strip().lower() not in targets:
                continue
            for tag in self.UNCONVERTED_SHAPE_TAGS:
                for el in layer_el.xpath(f".//svg:{tag}", namespaces=nsmap):
                    offenders.append((el, raw_label))
        return offenders

    # ------------------------------------------------------
    def convert_shapes_to_paths(self, elements):
        """
        Replace each element in `elements` (rect/circle/ellipse/etc,
        or text, as returned by find_unconverted_shapes()) with the
        equivalent PathElement, in place at the same position in the
        document - the same conversion Inkscape's own Path > Object
        to Path / Text > Object to Path performs, done here
        automatically instead of requiring the person to trigger it
        by hand first.

        True parametric shapes (rect/circle/ellipse/polygon/polyline/
        line) go through inkex's own to_path_element() - see
        _convert_shape_element(). <svg:text> elements are routed
        separately to convert_text_to_paths(), since to_path_element()
        does not reliably outline text glyphs into real path geometry
        the way Inkscape's own Text > Object to Path does.

        Returns the list of new PathElement objects, in the same
        order as `elements`. If the text-to-path conversion fails, the
        offending text element(s) are left in place (unconverted) and
        a warning is shown via inkex.errormsg rather than aborting the
        whole batch - any shapes in the same batch are still converted.
        """
        text_els = [el for el in elements if el.tag.split('}')[-1] == 'text']
        shape_els = [el for el in elements if el.tag.split('}')[-1] != 'text']

        converted_map = {}
        for el in shape_els:
            converted_map[el] = self._convert_shape_element(el)

        if text_els:
            try:
                for old_el, new_el in zip(text_els, self.convert_text_to_paths(text_els)):
                    converted_map[old_el] = new_el
            except RuntimeError as e:
                inkex.errormsg(
                    "Couldn't convert one or more text objects to paths "
                    f"automatically: {e}\n\n"
                    "Select the text object(s) in Inkscape and run "
                    "Text \u2192 Object to Path by hand, then run this again."
                )
                for el in text_els:
                    converted_map[el] = el  # left unconverted

        return [converted_map[el] for el in elements]

    # ------------------------------------------------------
    def _convert_shape_element(self, el):
        """
        Convert one rect/circle/ellipse/polygon/polyline/line element
        to the equivalent PathElement in place, via inkex's own
        to_path_element() - see convert_shapes_to_paths() for why this
        path is only used for true parametric shapes, not text.

        to_path_element() bakes the element's own `transform`
        attribute into the returned path data (not discarded),
        matching how collect_layer() already handles plain <path>
        elements via apply_transform() - same level of correctness,
        just automatic. id and inkscape:label are copied across
        manually since to_path_element() doesn't carry them.
        """
        new_el = el.to_path_element()
        old_id = el.get("id")
        label = el.get("inkscape:label")
        parent = el.getparent()
        parent.insert(parent.index(el), new_el)
        parent.remove(el)
        if old_id:
            new_el.set("id", old_id)
        if label:
            new_el.set("inkscape:label", label)
        return new_el

    # ------------------------------------------------------
    def convert_text_to_paths(self, elements):
        """
        Convert <svg:text> elements to real path geometry using
        Inkscape's own Text > Object to Path conversion, invoked via
        the actual `inkscape` binary (inkex.command.inkscape) rather
        than inkex's to_path_element() - see convert_shapes_to_paths()
        docstring for why.

        Mechanics: this needs a real file on disk, since it's shelling
        out to a separate inkscape process. The current in-memory
        document is written to a temp .svg file; each target element
        is given a stable id if it doesn't already have one; Inkscape
        is invoked against that file with the
        "select-by-id;object-to-path;export-do" action chain, which
        overwrites the same temp file with the converted result; that
        result is re-parsed, and the now-path version of each
        requested id is copied back into the LIVE in-memory document,
        replacing the original <svg:text> element in place (same
        parent, same position). The temp file is always cleaned up.

        Returns the list of new PathElement objects, in the same
        order as `elements`. Raises RuntimeError (with the underlying
        error folded in) if the inkscape binary can't be found, the
        action chain fails, or the converted document is missing an
        expected id - callers should catch this rather than assume
        text objects were converted.

        NOTE: this method depends on inkex.command.inkscape() and the
        exact action-chain syntax Inkscape's CLI expects, which can
        vary between Inkscape point releases. It has NOT been
        confirmed end-to-end against a real Inkscape installation from
        this environment - test against your actual Inkscape version
        before relying on it, and adjust the action string below if it
        errors.
        """
        ids = []
        for el in elements:
            if not el.get("id"):
                el.set("id", self.svg.get_unique_id("text"))
            ids.append(el.get("id"))

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
                tmp_path = tmp.name
            self.svg.getroottree().write(tmp_path)

            _inkscape_cli(
                tmp_path,
                actions=(
                    f"select-by-id:{','.join(ids)};"
                    "object-to-path;"
                    f"export-filename:{tmp_path};export-do"
                ),
            )

            converted_tree = inkex.load_svg(tmp_path)
            converted_root = converted_tree.getroot()
        except Exception as e:
            raise RuntimeError(str(e)) from e
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        converted = []
        for el, target_id in zip(elements, ids):
            new_el = converted_root.getElementById(target_id)
            if new_el is None:
                raise RuntimeError(f"converted document is missing id {target_id!r}")
            parent = el.getparent()
            parent.insert(parent.index(el), new_el)
            parent.remove(el)
            converted.append(new_el)
        return converted

    # ------------------------------------------------------
    def new_layer(self, name, parent=None):
        if parent is None:
            parent = self.svg
        nsmap = {'svg': self.svg.nsmap.get(None),
                 'inkscape': self.svg.nsmap.get('inkscape')}
        for g in parent.xpath(f"./svg:g[@inkscape:label='{name}']", namespaces=nsmap):
            g.delete()
        if not CAPTURE_ENABLE_MASTER:
            return None
        layer = Group()
        layer.label = name
        parent.add(layer)
        return layer

    # ------------------------------------------------------
    def delete_layer(self, name, parent=None):
        if parent is None:
            parent = self.svg
        nsmap = {'svg': self.svg.nsmap.get(None),
                 'inkscape': self.svg.nsmap.get('inkscape')}
        for g in parent.xpath(f"./svg:g[@inkscape:label='{name}']", namespaces=nsmap):
            g.delete()

    # ------------------------------------------------------
    def capture(self, paths, name, arrows=True, parent=None):
        if not CAPTURE_ENABLE_MASTER:
            return
        layer = self.new_layer(name, parent)
        if layer is None:
            return
        styles = {
            "cut": Style({"fill": "none", "stroke": "#ff0000", "stroke-width": str(CAPTURE_STROKE_WIDTH)}),
            "score": Style({"fill": "none", "stroke": "#0000ff", "stroke-width": str(CAPTURE_STROKE_WIDTH)}),
            "draw": Style({"fill": "none", "stroke": "#00cc44", "stroke-width": str(CAPTURE_STROKE_WIDTH)}),
            "crease": Style({"fill": "none", "stroke": "#00b7eb", "stroke-width": str(CAPTURE_STROKE_WIDTH)}),
        }
        for pts, kind in paths:
            if len(pts) < 2:
                continue
            pe = PathElement()
            pe.path = Path([["M", pts[0]]] + [["L", p] for p in pts[1:]])
            pe.style = styles.get(kind, styles["cut"])
            layer.add(pe)
            if arrows:
                for a in direction_arrows(pts):
                    ae = PathElement()
                    ae.path = Path([["M", a[0]], ["L", a[1]], ["L", a[2]]])
                    ae.style = pe.style
                    layer.add(ae)

    # ------------------------------------------------------
    def preview_paths(self, sides):
        """
        sides is the list of per-tool dicts built in effect() - each
        with 'side' ('left'/'right'), 'kind' ('cut'/'score'/
        'cut_score'/'draw'/'crease'/'none'), 'real_kinds' (ordered
        list of the actual layer kind(s) that side emits - two
        entries, ['score', 'cut'], for 'cut_score'), and 'paths'
        (list of (pts, real_kind) tuples already run through
        build_cut_path). This is the "Developer Use" preview layer,
        gated by preview_enable - unrelated to CAPTURE_ENABLE_MASTER.

        Grouping is by each path's own real_kind rather than the
        side's (possibly compound) kind, so a "cut_score" side gets
        two separate preview groups - Full Cut and Kiss Cut - each in
        its own PREVIEW_COLORS color, instead of one group with an
        undefined/compound color.
        """
        if not self.options.preview_enable:
            self.delete_layer("Preview")
            return

        self.delete_layer("Preview")
        preview = Group()
        preview.label = "Preview"
        self.svg.add(preview)

        labels = LAYER_DISPLAY_NAMES

        def style_for(kind):
            return Style({"fill": "none", "stroke": PREVIEW_COLORS.get(kind, "#ff0000"),
                          "stroke-width": str(CAPTURE_STROKE_WIDTH)})

        for s in sides:
            if s["kind"] == "none":
                continue
            side_letter = "L" if s["side"] == "left" else "R"
            for real_kind in s.get("real_kinds", [s["kind"]]):
                kind_paths = [(pts, k) for pts, k in s["paths"] if k == real_kind]
                if not kind_paths:
                    continue
                group = Group()
                group.label = f"{labels.get(real_kind, real_kind.title())} Preview ({side_letter})"
                preview.add(group)

                path_style = style_for(real_kind)
                for idx, (pts, _) in enumerate(kind_paths):
                    if len(pts) > 1:
                        pe = PathElement()
                        pe.path = Path([["M", pts[0]]] + [["L", p] for p in pts[1:]])
                        pe.style = path_style
                        pe.label = f"{labels.get(real_kind, real_kind.title())}-{idx+1}"
                        group.add(pe)

    # ------------------------------------------------------
    def collect_layer(self, label, kind):
        """Flatten every path in the SVG layer named `label` into point lists,
        force clockwise winding, and sort near-to-far from the lower-right corner.

        Layer name matching is case-insensitive ("Full Cut" matches a
        layer labeled "full cut" or "FULL CUT").

        `kind` is a tag carried through with each path - 'cut', 'score',
        or 'draw' - identifying which operation this layer's geometry
        is for; it doesn't affect the geometry itself.

        Each subpath of a compound <path> (e.g. an outer contour plus
        an inner hole, "M...Z M...Z") is kept as its own separate
        entry - not merged with its siblings - so each one gets its
        own independent pen-lift at emission time instead of being
        cut straight through into the next."""
        paths = []
        nsmap = {'svg': self.svg.nsmap.get(None),
                 'inkscape': self.svg.nsmap.get('inkscape')}
        target = label.strip().lower()
        for layer_el in self.svg.xpath(".//svg:g[@inkscape:groupmode='layer']", namespaces=nsmap):
            layer_label = (layer_el.get("inkscape:label", "") or "").strip().lower()
            if layer_label != target:
                continue
            for p in layer_el.xpath(".//svg:path", namespaces=nsmap):
                p = p.copy()
                p.apply_transform()
                superpath = CubicSuperPath(p.path.to_absolute())
                for pts in flatten_superpath_to_subpaths(superpath, FLATTEN_TOLERANCE_MM):
                    pts = dedupe(pts)
                    if len(pts) < 2:
                        continue
                    if not is_cw(pts):
                        pts.reverse()
                    # store original center for sorting
                    cx = sum(x for x, y in pts) / len(pts)
                    cy = sum(y for x, y in pts) / len(pts)
                    paths.append((pts, kind, cx, cy))
        # sort by closest to lower-right (max x + max y)
        paths.sort(key=lambda x: -(x[2] + x[3]))
        # remove cx, cy before returning
        return [(pts, kind) for pts, kind, cx, cy in paths]
