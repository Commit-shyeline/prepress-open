"""The die line — what the customer's `Cut` separation actually draws, and the paper around it.

`structure.py` says WHETHER a cut colorant exists; this module says what it IS: its finished size,
how far the knife travels, how many contours, whether it was drawn as a stroke (right) or a filled
shape (wrong), and — the number a bounding box can never give — how much of its perimeter has bare
paper just outside it. A wave-shaped panel whose artwork stops exactly on the knife has the same
bounding box as one that bleeds properly; the in-house engine learned that on a real file, so the
outline itself is walked and the raster is asked, a bleed's width outside every point, whether there
is still ink out there.

Coordinates: `outline.py` reports paths in millimetres of PDF user space, y UP from the page's
bottom edge. The measurement raster is y DOWN from the top. `polylines_image_mm` flips them once.
"""
import math

from . import outline, structure

# How finely a curve is sampled when the die is walked. Fine enough for a corner radius.
SAMPLES_PER_CURVE = 12
# A sample this close to the line reads the stroke's own antialiasing, not the paper beyond it.
MIN_SAMPLE_STEP_PX = 2


def geometry(pdf_bytes, page_index=0):
    """The die on this page, or None when no path is painted in a cut colorant.

    {colorant, origin_mm (x, y from the page's top-left), size_mm, length_mm, contours, closed,
     filled, polylines: [[(x, y), …] in page-mm, y DOWN]}
    """
    subpaths = [s for s in outline._subpaths(pdf_bytes, page_index)
                if structure.cut_spots([c for c in (s.get("stroke_colorant"), s.get("fill_colorant")) if c])]
    if not subpaths:
        return None
    try:
        page_w, page_h = outline.page_size_mm(pdf_bytes, page_index)
    except Exception:                                # noqa: BLE001 — no page size, no frame to flip in
        return None
    polylines, length, boxes = [], 0.0, []
    for subpath in subpaths:
        points = _sample(subpath["segments"], subpath["start"])
        if len(points) < 2:
            continue
        length += sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
        if subpath.get("closed") and points[0] != points[-1]:
            length += math.dist(points[-1], points[0])
        boxes.append((min(p[0] for p in points), min(p[1] for p in points),
                      max(p[0] for p in points), max(p[1] for p in points)))
        polylines.append([(x, page_h - y) for x, y in points])
    if not boxes:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    colorants = sorted({c for s in subpaths for c in (s.get("stroke_colorant"), s.get("fill_colorant"))
                        if c and structure.cut_spots([c])})
    return {"colorant": ", ".join(colorants),
            "origin_mm": (round(x0, 2), round(page_h - y1, 2)),
            "size_mm": (round(x1 - x0, 2), round(y1 - y0, 2)),
            "page_mm": (round(page_w, 2), round(page_h, 2)),
            "length_mm": round(length, 1),
            "contours": len(polylines),
            "closed": all(s.get("closed") for s in subpaths),
            "filled": any(s.get("painted") in ("fill", "both") for s in subpaths),
            "polylines": polylines}


def bare_perimeter(array, px_per_mm, polylines, bleed_mm, paper_min_channel):
    """Share of the die's perimeter with bare paper a bleed's width OUTSIDE it, or None.

    Which way is out comes from each contour's winding, not from its centre: on a concave shape
    plenty of the outline points towards the centroid. The sign of the enclosed area is exact for
    any simple polygon.
    """
    import numpy

    ink = (array < paper_min_channel).any(axis=2)
    height_px, width_px = ink.shape
    step_mm = max(bleed_mm, MIN_SAMPLE_STEP_PX / px_per_mm)
    bare = taken = 0
    for polyline in polylines:
        points = polyline[:-1] if len(polyline) > 2 and polyline[0] == polyline[-1] else polyline
        if len(points) < 3:
            continue
        signed = sum(points[i][0] * points[(i + 1) % len(points)][1]
                     - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points)))
        # y runs DOWN here, which reverses the usual sign: positive area is clockwise on screen,
        # and for a clockwise contour the left-hand normal (-ty, tx) points INTO the shape.
        # Checked on a square, not reasoned about — the first sign put every sample inside.
        outward = -1.0 if signed > 0 else 1.0
        for index, (x, y) in enumerate(points):
            px_, py_ = points[index - 1]
            nx_, ny_ = points[(index + 1) % len(points)]
            tangent = (nx_ - px_, ny_ - py_)
            norm = math.hypot(*tangent)
            if norm < 1e-9:
                continue
            normal = (-tangent[1] / norm * outward, tangent[0] / norm * outward)
            sx = int(round((x + normal[0] * step_mm) * px_per_mm))
            sy = int(round((y + normal[1] * step_mm) * px_per_mm))
            if not (0 <= sx < width_px and 0 <= sy < height_px):
                continue                             # off the sheet: the margins rule says so
            taken += 1
            if not ink[sy, sx]:
                bare += 1
    if taken == 0:
        return None
    return round(bare / taken, 3)


def _sample(segments, start):
    points = [start]
    here = start
    for segment in segments:
        if segment[0] == "l":
            here = segment[1]
            points.append(here)
            continue
        _kind, control_one, control_two, end = segment
        for step in range(1, SAMPLES_PER_CURVE + 1):
            points.append(outline._bezier(here, control_one, control_two, end,
                                          step / SAMPLES_PER_CURVE))
        here = end
    return points
