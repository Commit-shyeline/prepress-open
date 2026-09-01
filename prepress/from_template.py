"""The generator: a stored production template becomes the PDF a customer designs on.

The shop's rule for this, verbatim: *„Patrzymy co mamy, tworzymy czego nie mamy, generujemy szablon
dla klienta zgodnie z ustalonymi wcześniej zasadami."* — look at what we have, create what we do not,
generate to the rules agreed earlier. That ordering is the whole design:

    LOOK    an outline the shop marked always wins. It was drawn by someone who knows where the edge
            really is, and no computation improves on that.
    CREATE  only what is missing gets offset — the safe area from the safe base, the brutto from the
            cut — and an offset that cannot be done honestly is reported, never drawn wrong.
    DRAW    on the ORIGINAL page, at the original coordinates. No transform means nothing to get
            wrong: the customer's sheet is the production sheet.

Every other marked line — crease, perforation, sleeve, sewing — is drawn too, in its own colour, as
information. A customer who cannot see where the flag folds cannot keep their logo off the fold.
"""
from . import lines, offset, shape

PT_PER_MM = 72.0 / 25.4
GUIDE_WIDTH_PT = 0.75

# The three roles, and the colours they are drawn in. Brutto and netto keep the generator's existing
# convention so a customer who has seen one of our templates recognises the next.
BRUTTO_RGB = (0.55, 0.55, 0.58)
NETTO_RGB = (0.85, 0.10, 0.45)
SAFE_RGB = (0.18, 0.62, 0.32)


class TemplateError(ValueError):
    """Something about this template stops a usable customer sheet being drawn."""


def derive(template):
    """The three role outlines, plus the informational ones. Returns (drawing, notes).

    `drawing` holds real geometry ready to stroke. `notes` says, in the shop's own terms, which parts
    were taken from the template and which had to be computed — because "we made this one up from
    your margin" is exactly the sort of thing that should not be silent.
    """
    outlines = template.get("outlines") or []
    bleed_mm = float(template.get("bleed_mm") or 0.0)
    margins = margins_mm(template)
    marked = _by_type(outlines)

    cuts = [o for o in outlines if lines.defines_trim(o.get("type"))]
    if not cuts:
        raise TemplateError("Ten szablon nie ma linii cięcia — bez niej nie ma rozmiaru gotowego.")

    notes = []
    drawing = {"netto": [shape.deserialise(o) for o in cuts],
               "informational": [(o.get("type"), shape.deserialise(o))
                                 for o in outlines
                                 if o.get("type") in _INFORMATIONAL]}
    notes.append(f"netto: z {len(cuts)} " + ("linii cięcia" if len(cuts) > 1 else "linii cięcia"))

    # BRUTTO — drawn if the shop drew it, else the cut grown by the typed bleed.
    if marked.get("bleed"):
        drawing["brutto"] = [shape.deserialise(o) for o in marked["bleed"]]
        notes.append("brutto: wzięte z szablonu")
    elif bleed_mm > 0:
        grown, problem = _offset_all(cuts, +bleed_mm)
        if problem:
            raise TemplateError(f"Nie udało się dorobić spadu: {problem}")
        drawing["brutto"] = grown
        notes.append(f"brutto: dorobione — cięcie + {bleed_mm:g} mm")
    else:
        drawing["brutto"] = []
        notes.append("brutto: brak (spad 0 i nic nie narysowane)")

    # SAFE — drawn if the shop drew it, else the safe base taken in, else the cut taken in.
    if marked.get("safe"):
        drawing["safe"] = [shape.deserialise(o) for o in marked["safe"]]
        notes.append("obszar bezpieczny: wzięty z szablonu")
    elif max(margins.values()) > 0:
        # The margin is measured from whichever line the shop flagged, and from the cut only when
        # nothing was flagged. On a flag those are different lines: the tunnel hem is the cut, the
        # body edge is what the margin comes off.
        flagged = lines.safe_base_outlines(outlines)
        base = flagged or cuts
        # Offset by the SMALLEST margin first. That respects every curve and notch, which a per-side
        # number cannot — "110 mm from the left" is not a distance to a curve, it is a straight band
        # down one edge. The four numbers then trim what is left.
        smallest = min(margins.values())
        if smallest > 0:
            shrunk, problem = _offset_all(base, -smallest)
            if problem:
                raise TemplateError(f"Nie udało się wyznaczyć obszaru bezpiecznego: {problem}")
        else:
            shrunk = [shape.deserialise(o) for o in base]
        if max(margins.values()) > smallest:
            shrunk, problem = _clip_all(shrunk, _margin_box(base, margins))
            if problem:
                raise TemplateError(f"Nie udało się wyznaczyć obszaru bezpiecznego: {problem}")
        drawing["safe"] = shrunk
        origin = "zaznaczonej linii" if flagged else "cięcia"
        if len(set(margins.values())) == 1:
            notes.append(f"obszar bezpieczny: dorobiony — od {origin} minus {smallest:g} mm")
        else:
            sewn = sewn_mm(template)
            eaten = ", ".join(f"{_SIDE_NAMES[side]} {sewn[side]:g}"
                              for side in SAFE_SIDES if sewn[side])
            notes.append(f"obszar bezpieczny: dorobiony — od {origin} minus "
                         f"{float(template.get('safe_mm') or 0.0):g} mm, "
                         f"po odjęciu wykończenia ({eaten} mm)")
    else:
        drawing["safe"] = []
        notes.append("obszar bezpieczny: brak (margines 0 i nic nie narysowane)")
    return drawing, notes


def build_pdf(template, title=None):
    """The customer's sheet: the production page, with the boxes on it, one page per printed side.

    A two-sided product gets two pages rather than one with a note on it. A designer handed a single
    sheet for a double-sided flag has to guess which half is which, and guessing is the thing this
    project exists to remove.
    """
    import io

    from reportlab.pdfgen import canvas

    from . import specblock

    drawing, notes = derive(template)
    page_w, page_h = (float(v) for v in template["page_mm"])
    side_labels = lines.SIDE_LABELS.get(sides_of(template), ("",))
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_w * PT_PER_MM, page_h * PT_PER_MM))
    pdf.setTitle(title or template.get("name") or "Szablon")
    pdf.setCreator("prepress-open")

    for index, side in enumerate(side_labels):
        # The back is a MIRROR of the front (shop rule, 2026-08-24). A flag's pole is on the left of
        # the front, so seen from behind it is on the right, and a designer given an unmirrored back
        # puts the artwork on the wrong edge. Only the outlines flip — the spec panel is drawn after
        # this and stays readable.
        mirror = page_w if index else None
        for entry in drawing["brutto"]:
            _stroke(pdf, entry, BRUTTO_RGB, mirror_x_mm=mirror)
        for entry in drawing["netto"]:
            _stroke(pdf, entry, NETTO_RGB, mirror_x_mm=mirror)
        for entry in drawing["safe"]:
            _stroke(pdf, entry, SAFE_RGB, mirror_x_mm=mirror)
        for kind, entry in drawing["informational"]:
            style = lines.get(kind) or {}
            _stroke(pdf, entry, style.get("colour") or (0.5, 0.5, 0.5), style.get("dash"),
                    mirror_x_mm=mirror)
        specblock.draw(pdf, _spec_entry(template, drawing, side, mirrored=bool(mirror)),
                       page_w, page_h, lambda e: e["label"])
        _draw_printed_token(pdf, template)
        pdf.showPage()
    pdf.save()
    if len(side_labels) > 1:
        notes.append(f"strony: {len(side_labels)} — {', '.join(side_labels)}, tył lustrzany")
    return _stamp(buffer.getvalue(), template, notes, side_labels)


def sides_of(template):
    """How many printed sides this product has. Anything unexpected reads as one."""
    try:
        return 2 if int(template.get("sides") or 1) == 2 else 1
    except (TypeError, ValueError):
        return 1


def _draw_printed_token(pdf, template):
    """The identity as DRAWN content, in the bleed strip's bottom-left corner.

    Design apps rebuild a PDF on export, which kills the page-dictionary stamp — but they keep
    the template's drawn objects, so this tiny grey line rides every export where the customer
    kept the template layer. 2.2 pt is unreadable to an eye and irrelevant to the product (the
    bleed is cut off in finishing), but text extraction reads it at any size.
    """
    token = (template.get("token") or "").strip()
    if not token:
        return
    pdf.saveState()
    pdf.setFillColorRGB(0.45, 0.45, 0.45)
    pdf.setFont("Helvetica", 2.2)
    pdf.drawString(2 * PT_PER_MM, 0.8 * PT_PER_MM, f"prepress-open:{token}")
    pdf.restoreState()


def _stroke(pdf, entry, colour, dash=None, mirror_x_mm=None):
    pdf.setStrokeColorRGB(*colour)
    pdf.setLineWidth(GUIDE_WIDTH_PT)
    pdf.setDash(dash or (), 0)
    shape.draw_on_canvas(pdf, entry, mirror_x_mm=mirror_x_mm)
    pdf.setDash((), 0)


def _spec_entry(template, drawing, side="", mirrored=False):
    """What the spec panel needs: the real sizes, so a scaled page still states the truth."""
    boxes = lines.role_boxes(template.get("outlines") or [],
                             float(template.get("bleed_mm") or 0.0),
                             float(template.get("safe_mm") or 0.0),
                             template.get("page_mm"))
    brutto = boxes["brutto_mm"] or template["page_mm"]
    # From the shape that was actually drawn, not from a parallel recompute: with per-side margins
    # the uniform number is no longer the truth, and two answers to one question is how a spec panel
    # ends up stating a width the guides do not have.
    drawn_safe = _union_box(drawing.get("safe") or [])
    safe = ((drawn_safe[2] - drawn_safe[0], drawn_safe[3] - drawn_safe[1]) if drawn_safe
            else (boxes["safe_mm"] or boxes["netto_mm"] or brutto))
    # Where the panel may live: the largest rectangle that fits INSIDE the outlines drawn as the
    # safe area. That is only the same as their bounding box on a rectangle — a drop flag's safe
    # area is a teardrop, and a panel centred in its bbox hangs off the fabric, which is exactly
    # what it did (shop rule, 2026-08-31). Falls back to the bbox when the shape cannot be read.
    guides = drawing.get("safe") or drawing.get("netto") or []
    home = (offset.largest_inscribed_box([offset.flatten(entry) for entry in guides])
            or _union_box(guides))
    if home and mirrored:
        # The outlines on this page are reflected but the panel is not, so its home has to be
        # reflected with them or it sits where the FRONT's safe area was — on top of the guides.
        page_w = float(template["page_mm"][0])
        home = (page_w - home[2], home[1], page_w - home[0], home[3])
    name = template.get("name") or "Szablon"
    if mirrored:
        # Said on the sheet, not only in the metadata: a designer has to know the guides they are
        # looking at are reflected before they place anything against them.
        side = f"{side} (LUSTRO)" if side else "LUSTRO"
    return {"material_name": f"{name} — {side}" if side else name,
            "label": side,
            "safe_box_mm": home,
            "scale": 1,
            "brutto_mm": (float(brutto[0]), float(brutto[1])),
            "safe_mm_box": (float(safe[0]), float(safe[1])),
            "bleed_mm": float(template.get("bleed_mm") or 0.0),
            "safe_mm": float(template.get("safe_mm") or 0.0),
            "spec_position": template.get("spec_position") or "panel",
            "spec_strip_mm": 0.0}


def _union_box(entries):
    """The bounding box every one of these outlines fits in, or None."""
    boxes = [(e["origin_mm"][0], e["origin_mm"][1],
              e["origin_mm"][0] + e["width_mm"], e["origin_mm"][1] + e["height_mm"])
             for e in entries if e.get("origin_mm") and e.get("width_mm")]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def stamp_payload(template, notes=(), sides=1):
    """The stamp's content for one template — also WITHOUT a stamp.

    Split out of _stamp for the backwards-compatibility path: a customer file made on the OLD
    Illustrator templates carries no stamp, so once a human confirms which template it belongs to,
    this payload IS the geometry the stamp would have carried. One builder for both roads — a
    second copy is how the two would drift.
    """
    from .generate import STAMP_VERSION

    boxes = lines.role_boxes(template.get("outlines") or [],
                             float(template.get("bleed_mm") or 0.0),
                             float(template.get("safe_mm") or 0.0),
                             template.get("page_mm"))
    return {
        "v": STAMP_VERSION,
        "template": template.get("token"),
        "material": template.get("material") or "",
        "page_mm": [round(float(v), 2) for v in template["page_mm"]],
        "netto_mm": [round(v, 2) for v in (boxes["netto_mm"] or template["page_mm"])],
        "bleed_mm": round(float(template.get("bleed_mm") or 0.0), 2),
        "safe_mm": round(float(template.get("safe_mm") or 0.0), 2),
        # Carried even though the checker still reads only `safe_mm`: the sides are what the shop
        # actually set, and a returning file that lost them cannot be judged against the template it
        # was issued from.
        "sewn_sides_mm": {side: round(value, 2)
                          for side, value in sewn_mm(template).items()},
        "safe_total_sides_mm": {side: round(value, 2)
                                for side, value in margins_mm(template).items()},
        "scale": 1,
        "label": template.get("name") or "",
        "strip_mm": 0.0,
        "sides": sides,
        "notes": list(notes),
    }


def _stamp(pdf_bytes, template, notes, side_labels=("",)):
    """Write the template's token into the page, so a returned file identifies itself.

    Carries the real PAGE size as well: on a shaped template the sheet is not netto plus bleed on
    each side, and a checker that assumed it were would fail every correct return.
    """
    import io
    import json

    import pikepdf

    from .generate import STAMP_KEY

    payload = stamp_payload(template, notes, sides=len(side_labels))
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        # Stamped per page, each saying WHICH side it is, so a returned back cannot be mistaken for a
        # returned front.
        for index, page in enumerate(pdf.pages):
            page.obj[pikepdf.Name(STAMP_KEY)] = pikepdf.String(json.dumps(
                {**payload,
                 "side": side_labels[index] if index < len(side_labels) else "",
                 "mirrored": index > 0},
                ensure_ascii=False))
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = f"prepress-open template {template.get('token') or ''}".strip()
            meta["pdf:Producer"] = "prepress-open"
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


# Lines that are neither a box nor a margin: they tell the designer what the product DOES.
_INFORMATIONAL = ("kisscut", "crease", "reverse_crease", "perforation", "sew")


def _by_type(outlines):
    grouped = {}
    for entry in outlines:
        grouped.setdefault(str(entry.get("type") or "").lower(), []).append(entry)
    return grouped


# Order matters nowhere except in the notes, where it reads as the shop says it: lewa, góra, prawa, dół.
SAFE_SIDES = ("left", "top", "right", "bottom")
_SIDE_NAMES = {"left": "lewa", "top": "góra", "right": "prawa", "bottom": "dół"}


def margins_mm(template):
    """How far in from the cut the safe area sits, per side: material lost to finishing PLUS margin.

    Two different things, and modelling them as one was the mistake. A mast tunnel is material that
    is folded and SEWN — after that it is gone, it is not a cautious margin. The safe margin is then
    measured from the edge that remains visible. Adding them means one field per side says what the
    finishing eats, the shared margin still applies everywhere, and changing the margin moves all
    four correctly instead of needing four sums redone by hand.

    On a Vento Regular: 110 mm of sleeve down the left and across the top, then the shop's 30 mm, so
    the safe area starts 140 mm in on those two sides and 30 mm in on the other two.
    """
    margin = float(template.get("safe_mm") or 0.0)
    sewn = template.get("sewn_sides_mm") or {}
    return {side: float(sewn.get(side) or 0.0) + margin for side in SAFE_SIDES}


def sewn_mm(template):
    """What finishing eats per side, before the margin. Zero everywhere unless the shop said so."""
    sewn = template.get("sewn_sides_mm") or {}
    return {side: float(sewn.get(side) or 0.0) for side in SAFE_SIDES}


def _margin_box(entries, margins):
    """The rectangle four margins describe, measured from the base outline's own extent."""
    left = min(o["origin_mm"][0] for o in entries)
    bottom = min(o["origin_mm"][1] for o in entries)
    right = max(o["origin_mm"][0] + o["width_mm"] for o in entries)
    top = max(o["origin_mm"][1] + o["height_mm"] for o in entries)
    return (left + margins["left"], bottom + margins["bottom"],
            right - margins["right"], top - margins["top"])


def _clip_all(entries, box):
    """Trim every outline to the box, or report the first honest failure."""
    out = []
    for entry in entries:
        trimmed, problem = offset.clip_to_box(entry, box)
        if problem:
            return None, problem
        out.append(trimmed)
    return out, None


def _offset_all(entries, distance_mm):
    """Offset every outline by the same distance, or report the first honest failure."""
    out = []
    for entry in entries:
        moved, problem = offset.offset_outline(shape.deserialise(entry), distance_mm)
        if problem:
            return None, problem
        out.append(moved)
    return out, None
