"""What a line on a production template MEANS, as data.

A template carries several outlines, and WHAT a line is and WHAT IT IS USED FOR are two different
questions. A real VENTO S page 1, in the shop's own words (2026-08-24):

    800.8 x 2400.8    the sheet
    738.9 x 2336.3    a drawn BLEED line — the graphic area
    722.9 x 2320.2    a CUT line — the tunnel hem
    633.0 x 2230.6    a CUT line as well, AND the edge the safe margin is measured from

An earlier version of this table had "tunnel" and "safe base" as line types of their own. Both were
wrong. A tunnel hem is cut like anything else, and the body edge is a cut line that happens to carry a
second job. Modelling them as types forced a choice between two true statements.

So: `type` says what the line IS, and `safe_base` is a FLAG any outline may carry, saying the margin is
measured from that one. A cut may be several strokes — the finished size is their union — and the
template's own drawn safe area, when it has one, beats both.

The type decides three separate things: whether it defines the finished size, how it is drawn on the
customer's template, and what separation name it gets in a machine file later.

`separation` is the name a cutter or a RIP looks for. Those spellings are load-bearing — a plotter
matching on `CutContour` does not recognise `Cut contour` — which is why they are written out here
rather than derived from the label.

These labels are ADMIN-facing, so they live here rather than in `messages.py`, which is specifically
the text a customer reads.
"""

# Ordered as an admin thinks about them: what to cut first, then what to fold, then the guides.
LINE_TYPES = (
    {"id": "cut", "label": "Linia cięcia", "separation": "Cut",
     "defines_trim": True, "colour": (0.85, 0.10, 0.45), "dash": None,
     "hint": "Krawędź, po której nóż albo nożyce idą. Może być kilka — np. tunel i korpus."},
    {"id": "cutcontour", "label": "CutContour", "separation": "CutContour",
     "defines_trim": True, "colour": (0.85, 0.10, 0.45), "dash": None,
     "hint": "Ta sama rola co „Cut”, ale nazwa separacji, której szuka ploter Esko/Illustrator."},
    {"id": "kisscut", "label": "Kiss cut (nacięcie)", "separation": "Kiss Cut",
     "defines_trim": False, "colour": (0.90, 0.45, 0.10), "dash": (4, 2),
     "hint": "Nacięcie samej folii, podkład zostaje cały."},
    {"id": "crease", "label": "Biga (zagięcie)", "separation": "Crease",
     "defines_trim": False, "colour": (0.20, 0.45, 0.85), "dash": (6, 3),
     "hint": "Linia zagięcia. Treść trzymaj od niej z daleka — na zgięciu farba pęka."},
    {"id": "reverse_crease", "label": "Biga odwrotna", "separation": "Reverse Crease",
     "defines_trim": False, "colour": (0.20, 0.45, 0.85), "dash": (6, 3, 2, 3),
     "hint": "Zagięcie w drugą stronę."},
    {"id": "perforation", "label": "Perforacja", "separation": "Perforation",
     "defines_trim": False, "colour": (0.55, 0.30, 0.75), "dash": (1.5, 2.5),
     "hint": "Linia perforacji."},
    {"id": "sew", "label": "Linia szycia", "separation": "LINIA SZYCIA",
     "defines_trim": False, "colour": (0.30, 0.65, 0.35), "dash": (3, 2),
     "hint": "Gdzie idzie ścieg. Szycie zjada kilka milimetrów w każdą stronę."},
    {"id": "safe", "label": "Obszar bezpieczny (narysowany)", "separation": None,
     "defines_trim": False, "colour": (0.18, 0.62, 0.32), "dash": None,
     "hint": "Jeśli szablon RYSUJE gotowy obszar bezpieczny, wskaż go — użyjemy go jak stoi."},
    {"id": "bleed", "label": "Linia spadu (narysowana)", "separation": None,
     "defines_trim": False, "colour": (0.55, 0.55, 0.58), "dash": (2, 2),
     "hint": "Jeśli szablon rysuje spad, wskaż go zamiast wpisywać milimetry."},
)

BY_ID = {entry["id"]: entry for entry in LINE_TYPES}

# How many sides a product is printed on. Two-sided flags and boards are ordinary, and the customer
# needs a sheet per side — not one sheet they have to guess about.
SIDE_LABELS = {1: ("",), 2: ("PRZÓD", "TYŁ")}

# The types that answer "how big is the finished product". At least one outline must carry one of
# these, or there is no netto to check a returned file against.
TRIM_TYPES = tuple(entry["id"] for entry in LINE_TYPES if entry["defines_trim"])


def get(type_id):
    return BY_ID.get(str(type_id or "").strip().lower())


def is_known(type_id):
    return get(type_id) is not None


def defines_trim(type_id):
    entry = get(type_id)
    return bool(entry and entry["defines_trim"])


def for_browser():
    """The table the admin panel builds its per-outline dropdown from."""
    return [{"id": e["id"], "label": e["label"], "hint": e["hint"],
             "defines_trim": e["defines_trim"],
             "stroke": "#%02x%02x%02x" % tuple(round(c * 255) for c in e["colour"]),
             "dash": " ".join(str(v) for v in e["dash"]) if e["dash"] else None}
            for e in LINE_TYPES]


# Which role each box comes from, in the order they are looked for. A drawn outline always beats a
# typed number: the shop drew it because that is where the edge really is.
def role_boxes(outlines, bleed_mm=0.0, safe_mm=0.0, page_mm=None):
    """The three boxes, as BOUNDING BOXES, from the roles the admin assigned.

    Returned as boxes because that is what the panel shows back and what a check compares against.
    The final SAFE outline on a curved shape still has to be offset properly when the customer's
    template is drawn — a rectangle inset from a bounding box is not a sail inset from a sail — so
    this reports the extent, not the shape.

    The rules, in the order a real template answers them (measured on VENTO S page 1):

        brutto  the outline marked `bleed` if there is one (738.9 x 2336.3 there), else the cut grown
                by the typed bleed, else the page
        netto   the union of everything marked as a cut (722.9 x 2320.2 — the tunnel hem)
        safe    the outline marked `safe` if the template draws one, else the outline marked
                `safe_base` shrunk by the typed margin (633.0 minus the margin), else the cut shrunk
                by it
    """
    marked = {}
    for entry in outlines or []:
        kind = str(entry.get("type") or "").lower()
        box = _box(entry)
        if not box:
            continue
        marked.setdefault(kind, []).append(box)

    bases = safe_base_boxes(outlines)
    netto = trim_box_mm(outlines)
    brutto = (_union(marked.get("bleed"))
              or (_grow(netto, bleed_mm) if netto else None)
              or ((0.0, 0.0, page_mm[0], page_mm[1]) if page_mm else None))
    safe = (_union(marked.get("safe"))
            or _grow(_union(bases), -safe_mm)
            or _grow(netto, -safe_mm))
    return {"brutto_mm": _size(brutto), "netto_mm": _size(netto), "safe_mm": _size(safe),
            "safe_from": ("narysowany" if marked.get("safe")
                          else "od zaznaczonej linii minus margines" if bases
                          else "od cięcia minus margines" if netto else None)}


def safe_base_boxes(outlines):
    """Boxes of the outlines flagged as the edge the margin is measured from."""
    return [b for b in (_box(o) for o in outlines or [] if o.get("safe_base")) if b]


def safe_base_outlines(outlines):
    """The outlines themselves, for the generator, which needs geometry rather than extents."""
    return [o for o in outlines or [] if o.get("safe_base")]


def _union(boxes):
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _grow(box, by):
    """A box grown (or shrunk, for a negative value) on every side. Never past nothing."""
    if not box:
        return None
    x0, y0, x1, y1 = box[0] - by, box[1] - by, box[2] + by, box[3] + by
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _size(box):
    return [round(box[2] - box[0], 2), round(box[3] - box[1], 2)] if box else None


def trim_box_mm(outlines):
    """The finished size: the union of every outline that defines the trim, or None.

    A union, not the largest one — that is the whole reason multi-select exists. On VENTO S the hem
    reaches 722.9 mm across while the body reaches 633.0 mm, and the flag that leaves the shop is as
    wide as the hem.
    """
    boxes = [_box(o) for o in outlines or [] if defines_trim(o.get("type"))]
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _box(stored):
    origin = stored.get("origin_mm")
    width, height = stored.get("width_mm"), stored.get("height_mm")
    if not origin or width is None or height is None:
        return None
    return (float(origin[0]), float(origin[1]),
            float(origin[0]) + float(width), float(origin[1]) + float(height))
