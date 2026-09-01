"""Turning a read outline into something a human can look at, and something reportlab can redraw.

Two consumers, one geometry:

* the admin panel draws the candidate outlines OVER a render of the uploaded page, so the person
  picking the cut line sees the shape rather than a list of millimetres;
* the generator strokes the chosen outline onto the customer's template.

Both work from the exact cubic segments `outline.py` produced, in millimetres, so nothing is flattened
on the way through. The one conversion that happens here is the Y flip: PDF measures up from the
bottom-left, SVG measures down from the top-left, and getting that wrong mirrors the flag.
"""

# SVG coordinates are written in millimetres with a viewBox the size of the page, so the browser
# scales the overlay onto the preview image and no pixel arithmetic is needed anywhere.
COORDINATE_DECIMALS = 2

# For deriving a bounding box when the caller has none. `offset` imports only the standard library,
# so this direction cannot become a cycle.
from . import offset  # noqa: E402


def to_svg_path(entry, page_height_mm, closed=None):
    """One outline as an SVG `d` attribute, in page millimetres with the Y axis flipped."""
    parts = [f"M {_pair(entry['start'], page_height_mm)}"]
    for segment in entry["segments"]:
        if segment[0] == "l":
            parts.append(f"L {_pair(segment[1], page_height_mm)}")
        else:
            _kind, control_one, control_two, end = segment
            parts.append(f"C {_pair(control_one, page_height_mm)} "
                         f"{_pair(control_two, page_height_mm)} {_pair(end, page_height_mm)}")
    if entry.get("closed") if closed is None else closed:
        parts.append("Z")
    return " ".join(parts)


def draw_on_canvas(pdf, entry, offset_mm=(0.0, 0.0), mirror_x_mm=None):
    """Stroke one outline onto a reportlab canvas, curves kept as curves.

    reportlab draws in points from the bottom-left, which is the same orientation the segments were
    measured in, so there is no flip here — only the unit change.

    `mirror_x_mm` reflects the outline about a vertical axis at that coordinate, for the back of a
    double-sided product: the pole is on the left of the front, so from behind it is on the right.
    Only the GEOMETRY is mirrored — a caller that mirrors the whole canvas gets mirrored text too.
    """
    points_per_mm = 72.0 / 25.4
    shift_x, shift_y = offset_mm

    def at(point):
        x = (mirror_x_mm - point[0]) if mirror_x_mm is not None else point[0]
        return ((x + shift_x) * points_per_mm, (point[1] + shift_y) * points_per_mm)

    path = pdf.beginPath()
    path.moveTo(*at(entry["start"]))
    for segment in entry["segments"]:
        if segment[0] == "l":
            path.lineTo(*at(segment[1]))
        else:
            _kind, control_one, control_two, end = segment
            path.curveTo(*at(control_one), *at(control_two), *at(end))
    if entry.get("closed"):
        path.close()
    pdf.drawPath(path, stroke=1, fill=0)


def serialise(entry):
    """An outline as plain JSON: flat numbers, so a stored template needs no custom decoder.

    The bounding box is DERIVED when the caller did not supply one. It always could be — it is a
    function of the geometry — and requiring it made the function fail on any outline that had not
    come straight from `outline.candidates`.
    """
    entry = _with_bounds(entry)
    segments = []
    for segment in entry["segments"]:
        if segment[0] == "l":
            segments.append(["l", round(segment[1][0], 3), round(segment[1][1], 3)])
        else:
            _kind, control_one, control_two, end = segment
            segments.append(["c", round(control_one[0], 3), round(control_one[1], 3),
                             round(control_two[0], 3), round(control_two[1], 3),
                             round(end[0], 3), round(end[1], 3)])
    return {"start": [round(entry["start"][0], 3), round(entry["start"][1], 3)],
            "segments": segments, "closed": bool(entry.get("closed")),
            "width_mm": entry["width_mm"], "height_mm": entry["height_mm"],
            "origin_mm": list(entry["origin_mm"])}


def deserialise(stored):
    """The inverse, back into the tuple form the drawing and measuring code expects."""
    segments = []
    for segment in stored.get("segments") or []:
        if segment[0] == "l":
            segments.append(("l", (float(segment[1]), float(segment[2]))))
        else:
            segments.append(("c", (float(segment[1]), float(segment[2])),
                             (float(segment[3]), float(segment[4])),
                             (float(segment[5]), float(segment[6]))))
    return {"start": (float(stored["start"][0]), float(stored["start"][1])),
            "segments": segments, "closed": bool(stored.get("closed")),
            "width_mm": float(stored.get("width_mm") or 0.0),
            "height_mm": float(stored.get("height_mm") or 0.0),
            "origin_mm": tuple(stored.get("origin_mm") or (0.0, 0.0))}


def _with_bounds(entry):
    """The same outline, guaranteed to carry its size and origin."""
    if entry.get("width_mm") is not None and entry.get("origin_mm") is not None:
        return entry
    points = offset.flatten(entry)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {**entry,
            "width_mm": round(max(xs) - min(xs), 2),
            "height_mm": round(max(ys) - min(ys), 2),
            "origin_mm": (round(min(xs), 2), round(min(ys), 2))}


def render_preview(pdf_bytes, page_index=0, max_pixels=1400):
    """A PNG of the uploaded page, for the admin to see what they are pointing at.

    Rendered small on purpose: this is a picture to recognise a shape in, not a measurement. Every
    number the panel shows comes from the geometry, never from these pixels.
    """
    import io

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[page_index]
        width_pt, height_pt = page.get_size()
        longest_pt = max(width_pt, height_pt) or 1
        scale = min(2.0, max_pixels / longest_pt)
        image = page.render(scale=scale).to_pil().convert("RGB")
    finally:
        document.close()
    holder = io.BytesIO()
    image.save(holder, format="PNG", optimize=True)
    return holder.getvalue()


def _pair(point, page_height_mm):
    return (f"{round(point[0], COORDINATE_DECIMALS)},"
            f"{round(page_height_mm - point[1], COORDINATE_DECIMALS)}")
