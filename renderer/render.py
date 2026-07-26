#!/usr/bin/env python3
"""
TaxAnn reference renderer.

Consumes a TaxAnn annotation set (the reusable form description) plus one
taxpayer's data (the instance), and stamps the values onto the source PDF by
drawing an overlay and merging it. This is intentionally small (~1 file) to
prove the annotation spec is complete enough for a stranger to build against.

  python render.py <annotation.json> <data.json> <source.pdf> <out.pdf>

Design contract honored here:
  - The annotation carries positioning, formatting, and references only.
  - The renderer NEVER computes tax values; it only fetches and prints them.
  - Coordinates are authored top-left; we flip y to PDF bottom-left per page.
  - Value refs must resolve to exactly ONE node (else: error).
  - Transforms come from a fixed enum; no code is ever evaluated.
"""

import io
import json
import re
import sys

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


# ----------------------------------------------------------------------------
# Value resolution: a deliberately restricted JSONPath ($.a.b[0].c) that must
# resolve to exactly one node. No wildcards/filters in v1.0 (see SPEC.md).
# ----------------------------------------------------------------------------
_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def resolve_ref(ref, data):
    if not ref.startswith("$"):
        raise ValueError(f"ref must start with '$': {ref!r}")
    node = data
    pos = 1
    while pos < len(ref):
        m = _TOKEN.match(ref, pos)
        if not m:
            raise ValueError(f"bad ref syntax near {ref[pos:]!r} in {ref!r}")
        key, idx = m.group(1), m.group(2)
        if key is not None:
            if not isinstance(node, dict) or key not in node:
                return _MISSING
            node = node[key]
        else:
            i = int(idx)
            if not isinstance(node, list) or i >= len(node):
                return _MISSING
            node = node[i]
        pos = m.end()
    return node


class _Missing:
    def __repr__(self):
        return "<missing>"


_MISSING = _Missing()


_TEMPLATE = re.compile(r"\{(\$[^}]*)\}")


def resolve_binding(binding, data):
    """Return the raw (pre-format) value for a value-binding block."""
    if binding is None:
        return _MISSING

    if "const" in binding:
        value = binding["const"]
    elif "template" in binding:
        def sub(m):
            v = resolve_ref(m.group(1), data)
            return "" if v is _MISSING or v is None else str(v)
        value = _TEMPLATE.sub(sub, binding["template"])
    elif "ref" in binding:
        value = resolve_ref(binding["ref"], data)
    else:
        raise ValueError("value binding needs one of ref/template/const")

    if value is _MISSING or value is None:
        if "default" in binding:
            value = binding["default"]
        else:
            return _MISSING

    if "transform" in binding:
        value = apply_transform(binding["transform"], value)
    return value


def apply_transform(name, value):
    if name == "digitsOnly":
        return re.sub(r"\D", "", str(value))
    if name == "roundWholeDollar":
        return round(float(value))
    if name == "upper":
        return str(value).upper()
    if name == "lower":
        return str(value).lower()
    if name == "trim":
        return str(value).strip()
    raise ValueError(f"unknown transform {name!r}")


# ----------------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------------
def format_number(value, fmt):
    fmt = fmt or {}
    decimals = fmt.get("decimals", 0)
    n = float(value)
    negative = n < 0
    body = f"{abs(n):,.{decimals}f}" if fmt.get("thousands", True) else f"{abs(n):.{decimals}f}"
    if negative:
        return f"({body})" if fmt.get("negative", "parens") == "parens" else f"-{body}"
    return body


def render_string(field, raw):
    """Turn a resolved raw value into the display string for this field type."""
    t = field["type"]
    if t in ("currency", "number"):
        return format_number(raw, field.get("format"))
    return str(raw)


# ----------------------------------------------------------------------------
# Drawing helpers (all in top-left space; convert to bottom-left at draw time)
# ----------------------------------------------------------------------------
def fit_font_size(c, text, font, size, max_width, overflow):
    """Return a (possibly reduced) font size, or raise/truncate per policy."""
    if not text:
        return size, text
    w = c.stringWidth(text, font, size)
    if w <= max_width:
        return size, text
    if overflow == "error":
        raise ValueError(f"value {text!r} overflows box (width {max_width}pt)")
    if overflow in ("truncate", "clip"):
        while text and c.stringWidth(text, font, size) > max_width:
            text = text[:-1]
        return size, text
    # default: shrink
    s = size
    while s > 4 and c.stringWidth(text, font, s) > max_width:
        s -= 0.5
    return s, text


def draw_in_box(c, text, style, bounds, pad, page_h, overflow="shrink"):
    """Draw text aligned inside a top-left box, converting to PDF space."""
    font = style.get("font", "Helvetica")
    size = style.get("size", 10)
    color = style.get("color", "#000000")
    align = style.get("align", "left")
    valign = style.get("valign", "middle")

    pl = pad.get("left", 0); pr = pad.get("right", 0)
    pt = pad.get("top", 0);  pb = pad.get("bottom", 0)

    inner_x = bounds["x"] + pl
    inner_w = bounds["width"] - pl - pr

    size, text = fit_font_size(c, text, font, size, inner_w, overflow)

    c.setFont(font, size)
    c.setFillColor(_hex(color))

    # horizontal position
    if align == "right":
        tx = inner_x + inner_w
        draw = c.drawRightString
    elif align == "center":
        tx = inner_x + inner_w / 2.0
        draw = c.drawCentredString
    else:
        tx = inner_x
        draw = c.drawString

    # vertical: baseline. Approx cap-height ~0.70*size for centering.
    top = bounds["y"] + pt
    bottom = bounds["y"] + bounds["height"] - pb
    if valign == "top":
        baseline_from_top = pt + size * 0.80
    elif valign == "bottom":
        baseline_from_top = (bounds["height"] - pb) - size * 0.20
    else:  # middle
        box_h = bottom - top
        baseline_from_top = pt + box_h / 2.0 + size * 0.30

    ty_pdf = page_h - (bounds["y"] + baseline_from_top)
    draw(tx, ty_pdf, text)


def draw_comb(c, digits, style, bounds, comb, page_h):
    """Distribute characters across N evenly spaced cells with optional gaps."""
    cells = comb["cells"]
    gaps = {int(k): v for k, v in comb.get("groupGaps", {}).items()}
    total_gap = sum(gaps.values())
    cell_w = (bounds["width"] - total_gap) / cells

    font = style.get("font", "Courier")
    size = style.get("size", 11)
    c.setFont(font, size)
    c.setFillColor(_hex(style.get("color", "#000000")))

    x = bounds["x"]
    box_h = bounds["height"]
    baseline_from_top = box_h / 2.0 + size * 0.30
    ty_pdf = page_h - (bounds["y"] + baseline_from_top)

    for i in range(cells):
        if i in gaps:
            x += gaps[i]
        ch = digits[i] if i < len(digits) else ""
        if ch:
            c.drawCentredString(x + cell_w / 2.0, ty_pdf, ch)
        x += cell_w


def _hex(h):
    from reportlab.lib.colors import HexColor
    return HexColor(h)


# ----------------------------------------------------------------------------
# Main render
# ----------------------------------------------------------------------------
def render(annotation, data, source_pdf, out_pdf):
    if annotation["coordinateSystem"]["origin"] != "top-left":
        raise NotImplementedError("this reference renderer expects top-left origin")

    styles = annotation.get("styles", {})
    pages = {p["number"]: p for p in annotation["pages"]}

    reader = PdfReader(source_pdf)
    writer = PdfWriter()

    # Build one overlay canvas per annotated page, then merge.
    overlays = {}
    buffers = {}
    for num, p in pages.items():
        buf = io.BytesIO()
        overlays[num] = canvas.Canvas(buf, pagesize=(p["width"], p["height"]))
        buffers[num] = buf

    for field in annotation["fields"]:
        page_num = field["page"]
        page_h = pages[page_num]["height"]
        c = overlays[page_num]
        style = styles.get(field.get("style"), {})
        pad = field.get("padding", {})
        overflow = field.get("overflow", "shrink")

        if field["type"] == "choiceGroup":
            selected = resolve_binding(field["value"], data)
            if selected is _MISSING:
                continue
            mark = field.get("mark", "X")
            for opt in field["options"]:
                if opt["when"] == selected:
                    draw_in_box(c, mark, style, opt["bounds"],
                                {}, page_h, overflow)
            continue

        raw = resolve_binding(field.get("value"), data)
        if raw is _MISSING:
            continue  # nothing to print; leave the box blank

        if field["type"] == "comb":
            digits = str(raw)
            draw_comb(c, digits, style, field["bounds"], field["comb"], page_h)
            continue

        text = render_string(field, raw)
        draw_in_box(c, text, style, field["bounds"], pad, page_h, overflow)

    for c in overlays.values():
        c.save()

    overlay_readers = {num: PdfReader(buf) for num, buf in
                       ((n, _seek0(b)) for n, b in buffers.items())}

    for i, page in enumerate(reader.pages):
        num = i + 1
        if num in overlay_readers:
            page.merge_page(overlay_readers[num].pages[0])
        writer.add_page(page)

    with open(out_pdf, "wb") as fh:
        writer.write(fh)
    print(f"Wrote {out_pdf} ({len(reader.pages)} pages, "
          f"{len(annotation['fields'])} fields stamped).")


def _seek0(buf):
    buf.seek(0)
    return buf


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(1)
    ann = json.load(open(sys.argv[1]))
    dat = json.load(open(sys.argv[2]))
    render(ann, dat, sys.argv[3], sys.argv[4])
