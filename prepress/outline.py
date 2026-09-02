"""Read the outlines out of an uploaded production template, with pikepdf only.

The admin uploads a real production template — a flag, a leaflet, an aluminium panel, a glass panel, a
pneumatic arch — and has to be able to point at ONE of its outlines and say "that is the cut". This
module produces the list to point at.

Nothing here guesses which outline is which, and that is deliberate. Measured on this shop's own
templates, every automatic discriminator fails on real files:

* **layer names carry nothing.** The VENTO `.ai` templates declare exactly one optional content group,
  named `Layer 1`; two production PDFs from the same folder declare none at all.
* **colour carries nothing.** The in-house extractor's own note: "Roles come from nesting, not colour
  — every stroke in these templates is the same dark grey."
* **separation names are absent.** No `Cut` colorant in the flag templates, unlike the cut files.
* **area ratios are fragile.** A page frame drawn 0.4 mm inside a 950.4 mm page reads as the outermost
  outline and shifts every role by one — which is exactly the mistake this session made by hand.

So the machine lists candidates and a human assigns the role once per template.

WHY THIS READER EXISTS: this package may not link PyMuPDF, so it needs its own path reader whatever
the in-house one does. Beyond that, it keeps the segments as exact cubics rather than flattening them,
and it measures a curve by sampling the curve.

Two things were measured on 2026-08-24 against the VENTO source templates, and the difference between
them is the whole lesson of this module:

* **the geometry is not the problem.** The in-house index stores each outline flattened to 8 chords,
  and it agrees with this reader on all eleven VENTO entries to within **0.39 mm** — which is the
  precision of its own storage format (four decimals of a 3940 mm page). An earlier draft of this
  docstring claimed the index understated a cut by 12.6 mm. That was wrong: it compared the index's
  cut against an outline a probe of MINE had misidentified, because the probe never dropped the page
  frame.
* **the ROLE assignment is the problem**, and this session is its own evidence — I made the
  page-frame off-by-one twice in one afternoon, which is exactly how a bleed line gets used as a cut
  line. That is why nothing here decides which outline is which.

One narrower caveat worth keeping: a stroke's `rect`, as the in-house engine reads it, is the
control-point HULL — verified, it equals the bbox of the path's own points on every outline tested. A
cubic stays inside its hull and usually well inside, so on vento_xl_p0 two outlines read 16.7 mm and
16.5 mm wider than the curve while their heights agree to the millimetre. Nothing stores those rects,
but a cut line must never be taken from one.

This reader samples the curve itself, stable from 64 samples per segment upward (checked at 8, 64, 256
and 2048), so what is measured and what gets redrawn are the same geometry.
"""
import math

# A subpath smaller than this is furniture — a registration dot, a pole marker, a logo. The VENTO
# templates carry a 30 x 400 mm sleeve marker that must not be offered as a candidate cut line.
MIN_CANDIDATE_AREA_MM2 = 50_000.0        # 0.05 m², e.g. 250 x 200 mm

# How close to the page size counts as "this is the sheet, not a guide". Kept TIGHT and used only to
# LABEL, never to drop: measured on VENTO, a legitimate outline sits 0.4 mm inside the page, and a
# 5 mm tolerance silently ate it. A filter that hides a candidate is how the wrong outline gets
# picked; the list shows everything and says which entries look like the sheet.
PAGE_SIZED_TOLERANCE_MM = 1.0

# Samples per curve when measuring a bounding box. Cheap, and it puts the error far below the 0.5 mm
# tolerance the rules judge on.
BBOX_SAMPLES_PER_CURVE = 64

# A placed template can nest one level down in a form XObject; deeper is a malformed file looping.
MAX_FORM_DEPTH = 4

PT_TO_MM = 25.4 / 72


def candidates(pdf_bytes, page_index=0):
    """Every outline on the page big enough to be a cut line, biggest first.

    Each entry is what a human needs to recognise it, plus what the generator needs to redraw it:

        width_mm, height_mm  the real size, curves sampled rather than chorded
        area_mm2             for ordering, and for spotting the bleed/cut pair
        segments             exact geometry in mm: ("l", x, y) and ("c", x1, y1, x2, y2, x, y)
        closed               whether the subpath closed itself
        painted              "stroke", "fill", "both" or "clip"
    """
    found = []
    for subpath in _subpaths(pdf_bytes, page_index):
        box = _bbox(subpath["segments"], subpath["start"])
        if box is None:
            continue
        width_mm, height_mm = box[2] - box[0], box[3] - box[1]
        area = width_mm * height_mm
        if area < MIN_CANDIDATE_AREA_MM2:
            continue
        found.append({**subpath, "width_mm": round(width_mm, 2),
                      "height_mm": round(height_mm, 2), "area_mm2": round(area, 1),
                      "origin_mm": (round(box[0], 2), round(box[1], 2))})
    found.sort(key=lambda entry: entry["area_mm2"], reverse=True)
    return found


def page_size_mm(pdf_bytes, page_index=0):
    import pikepdf

    with pikepdf.open(_stream(pdf_bytes)) as pdf:
        page = pdf.pages[page_index]
        box = [float(v) for v in (page.obj.get("/CropBox") or page.mediabox)]
        return ((box[2] - box[0]) * PT_TO_MM, (box[3] - box[1]) * PT_TO_MM)


def mark_page_sized(entries, page_mm):
    """Flag the entries that are the sheet itself, so the UI can grey them out.

    Deliberately NOT a filter. An earlier version dropped anything within 5 mm of the page and threw
    away a real outline drawn 0.4 mm inside it — the admin then picks from a list that is quietly
    missing the answer, which is worse than a list with two obvious decoys in it.
    """
    for entry in entries:
        entry["page_sized"] = (abs(entry["width_mm"] - page_mm[0]) <= PAGE_SIZED_TOLERANCE_MM
                               and abs(entry["height_mm"] - page_mm[1]) <= PAGE_SIZED_TOLERANCE_MM)
    return entries


def _subpaths(pdf_bytes, page_index):
    """Walk the content stream and yield each painted subpath in millimetres."""
    import pikepdf

    try:
        with pikepdf.open(_stream(pdf_bytes)) as pdf:
            if page_index >= len(pdf.pages):
                return []
            page = pdf.pages[page_index]
            collected = []
            _walk(page.obj, page.obj.get("/Resources"), pikepdf.Matrix(), 0, set(), collected,
                  [0])
            return collected
    except Exception:                               # noqa: BLE001 — an unreadable file has no paths
        return []


def _walk(container, resources, ctm, depth, walking, collected, group, colour=(None, None)):
    """One content stream: track the CTM, build paths, and record them when they are painted.

    `group` is a one-element counter shared down the whole walk, so every subpath knows WHICH painting
    operation drew it. That matters twice: an outline with a hole is several subpaths of one operation
    and must be offered as one candidate, and comparing against a reader that unions per operation is
    otherwise apples to oranges.

    `colour` is (stroke colorant, fill colorant): the NAME of the Separation/DeviceN a path is painted
    in, or None for a process colour. It is part of the graphics state (saved by q, restored by Q,
    inherited by a form) and is what lets `die.py` pick the paths drawn in `Cut` out of the artwork.
    """
    import pikepdf

    try:
        instructions = pikepdf.parse_content_stream(container)
    except Exception:                               # noqa: BLE001
        return

    stack = []
    current = ctm
    stroke_colorant, fill_colorant = colour
    builder = _PathBuilder()
    clip_pending = False

    for operands, operator in instructions:
        name = str(operator)
        try:
            if name == "q":
                stack.append((current, stroke_colorant, fill_colorant))
            elif name == "Q":
                current, stroke_colorant, fill_colorant = stack.pop() if stack else (ctm, *colour)
            elif name == "CS" and operands:
                stroke_colorant = _colorant_of(resources, operands[0])
            elif name == "cs" and operands:
                fill_colorant = _colorant_of(resources, operands[0])
            elif name in ("G", "RG", "K"):
                stroke_colorant = None                # a process colour: no longer a separation
            elif name in ("g", "rg", "k"):
                fill_colorant = None
            elif name == "cm" and len(operands) == 6:
                current = pikepdf.Matrix(*[float(v) for v in operands]) @ current
            elif name == "m" and len(operands) == 2:
                builder.move(_point(operands, 0, current))
            elif name == "l" and len(operands) == 2:
                builder.line(_point(operands, 0, current))
            elif name == "c" and len(operands) == 6:
                builder.curve(_point(operands, 0, current), _point(operands, 2, current),
                              _point(operands, 4, current))
            elif name == "v" and len(operands) == 4:
                # The first control point is the current point.
                builder.curve(builder.here, _point(operands, 0, current),
                              _point(operands, 2, current))
            elif name == "y" and len(operands) == 4:
                # The second control point is the endpoint.
                end = _point(operands, 2, current)
                builder.curve(_point(operands, 0, current), end, end)
            elif name == "re" and len(operands) == 4:
                builder.rectangle([float(v) for v in operands], current)
            elif name == "h":
                builder.close()
            elif name in ("W", "W*"):
                clip_pending = True
            elif name in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"):
                painted = _painted_as(name, clip_pending)
                if painted:
                    group[0] += 1
                    for subpath in builder.finish(painted, group[0],
                                                  close=name in ("s", "b", "b*")):
                        subpath["stroke_colorant"] = stroke_colorant if "stroke" in painted or painted == "both" else None
                        subpath["fill_colorant"] = fill_colorant if painted in ("fill", "both") else None
                        collected.append(subpath)
                builder = _PathBuilder()
                clip_pending = False
            elif name == "Do" and operands and depth < MAX_FORM_DEPTH:
                _enter_form(resources, operands[0], current, depth, walking, collected, group,
                            (stroke_colorant, fill_colorant))
        except Exception:                           # noqa: BLE001 — one bad operator is not a page
            continue


def _painted_as(operator, clip_pending):
    """What this painting operator means for us, or None when the path is discarded."""
    if operator == "n":
        # A no-op paint. It still matters when it follows W: that is a clipping path, and in a
        # template the clip is sometimes the only record of the shape.
        return "clip" if clip_pending else None
    strokes = operator in ("S", "s", "B", "B*", "b", "b*")
    fills = operator in ("f", "F", "f*", "B", "B*", "b", "b*")
    if strokes and fills:
        return "both"
    return "stroke" if strokes else "fill"


def _colorant_of(resources, name):
    """The colorant a named colour space resource stands for, or None for anything process.

    `/Separation /Cut …` → "Cut"; `/DeviceN [/Cut /Regmark] …` → "Cut, Regmark". Deliberately the
    NAME only: the alternate space and tint transform say how to display it, not what it is.
    """
    try:
        spaces = resources.get("/ColorSpace") if resources is not None else None
        space = spaces.get(str(name)) if spaces is not None else None
        if space is None or not hasattr(space, "__len__") or len(space) < 2:
            return None
        family = str(space[0])
        if family == "/Separation":
            return str(space[1]).lstrip("/")
        if family == "/DeviceN":
            return ", ".join(str(n).lstrip("/") for n in space[1])
    except Exception:                               # noqa: BLE001 — an odd resource is not a colorant
        return None
    return None


def _enter_form(resources, name, ctm, depth, walking, collected, group, colour=(None, None)):
    import pikepdf

    form = None
    try:
        entry = resources.get("/XObject") if resources is not None else None
        form = entry.get(str(name)) if entry is not None else None
        if form is None or str(form.get("/Subtype")) != "/Form":
            return
    except Exception:                               # noqa: BLE001
        return
    key = _identity(form)
    if key in walking:
        return
    inner = ctm
    matrix = form.get("/Matrix")
    if matrix is not None:
        try:
            inner = pikepdf.Matrix(*[float(v) for v in matrix]) @ ctm
        except (TypeError, ValueError):
            inner = ctm
    walking.add(key)
    try:
        _walk(form, form.get("/Resources") or resources, inner, depth + 1, walking, collected,
              group, colour)
    finally:
        walking.discard(key)


class _PathBuilder:
    """Accumulates subpaths in millimetres until a painting operator decides their fate."""

    def __init__(self):
        self.subpaths = []
        self.current = None
        self.here = (0.0, 0.0)

    def move(self, point):
        self.current = {"start": point, "segments": [], "closed": False}
        self.subpaths.append(self.current)
        self.here = point

    def line(self, point):
        if self.current is None:
            self.move(point)
            return
        self.current["segments"].append(("l", point))
        self.here = point

    def curve(self, control_one, control_two, end):
        if self.current is None:
            self.move(end)
            return
        self.current["segments"].append(("c", control_one, control_two, end))
        self.here = end

    def rectangle(self, values, ctm):
        x, y, width, height = values
        corners = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        self.move(_transform(corners[0], ctm))
        for corner in corners[1:]:
            self.line(_transform(corner, ctm))
        self.close()

    def close(self):
        if self.current is not None:
            self.current["closed"] = True

    def finish(self, painted, group, close=False):
        out = []
        for subpath in self.subpaths:
            if not subpath["segments"]:
                continue
            out.append({"start": subpath["start"], "segments": subpath["segments"],
                        "closed": subpath["closed"] or close, "painted": painted,
                        "group": group})
        return out


def _point(operands, index, ctm):
    return _transform((float(operands[index]), float(operands[index + 1])), ctm)


def _transform(point, ctm):
    """PDF user space → millimetres, through the current transformation matrix."""
    x, y = point
    return ((ctm.a * x + ctm.c * y + ctm.e) * PT_TO_MM,
            (ctm.b * x + ctm.d * y + ctm.f) * PT_TO_MM)


def _bbox(segments, start):
    """Bounding box in mm, with curves SAMPLED rather than chorded.

    The chord shortcut is what cost the in-house index up to 12.6 mm at a sail's widest point.
    """
    xs, ys = [start[0]], [start[1]]
    here = start
    for segment in segments:
        if segment[0] == "l":
            here = segment[1]
            xs.append(here[0])
            ys.append(here[1])
            continue
        _kind, control_one, control_two, end = segment
        for step in range(1, BBOX_SAMPLES_PER_CURVE + 1):
            t = step / BBOX_SAMPLES_PER_CURVE
            x, y = _bezier(here, control_one, control_two, end, t)
            xs.append(x)
            ys.append(y)
        here = end
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _bezier(start, control_one, control_two, end, t):
    inverse = 1.0 - t
    a = inverse ** 3
    b = 3 * inverse ** 2 * t
    c = 3 * inverse * t ** 2
    d = t ** 3
    return (a * start[0] + b * control_one[0] + c * control_two[0] + d * end[0],
            a * start[1] + b * control_one[1] + c * control_two[1] + d * end[1])


def perimeter_mm(segments, start):
    """Rough path length, for telling a long thin marker from a real outline."""
    total = 0.0
    here = start
    for segment in segments:
        end = segment[1] if segment[0] == "l" else segment[3]
        total += math.dist(here, end)
        here = end
    return total


def _identity(obj):
    try:
        return (obj.objgen[0], obj.objgen[1])
    except Exception:                               # noqa: BLE001
        return id(obj)


def _stream(data):
    import io

    return io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
