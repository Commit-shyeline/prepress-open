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

    from . import materials, specblock

    # A sheet of panels is issued AS its production page: the die-lines, folds and labels the graphic
    # team drew, with the spec panel and the printed token laid over, never redrawn from an outline.
    if template.get("kind") == "sheet":
        source = materials.load_template_source(template.get("token"))
        if source:
            return _sheet_from_source(template, source, title)

    drawing, notes = derive(template)
    page_w, page_h = (float(v) for v in template["page_mm"])
    side_labels = lines.SIDE_LABELS.get(sides_of(template), ("",))
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_w * PT_PER_MM, page_h * PT_PER_MM))
    pdf.setTitle(title or template.get("name") or "Szablon")
    pdf.setCreator("prepress-open")

    mirror_back = back_mirrored(template)
    for index, side in enumerate(side_labels):
        # The back is a MIRROR of the front (shop rule, 2026-08-24). A flag's pole is on the left of
        # the front, so seen from behind it is on the right, and a designer given an unmirrored back
        # puts the artwork on the wrong edge. Only the outlines flip — the spec panel is drawn after
        # this and stays readable. A product whose back is a print of its own (a tension-fabric wall:
        # `back_mirrored: false`) keeps the back as drawn.
        mirror = page_w if index and mirror_back else None
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
        spec = _spec_entry(template, drawing, side, mirrored=bool(mirror))
        spec["legend"] = _legend(per_face=False)
        specblock.draw(pdf, spec, page_w, page_h, lambda e: e["label"])
        _draw_printed_token(pdf, template)
        pdf.showPage()
    pdf.save()
    if len(side_labels) > 1:
        notes.append(f"strony: {len(side_labels)} — {', '.join(side_labels)}, " + ("tył lustrzany" if mirror_back else "tył bez lustra"))
    return _stamp(buffer.getvalue(), template, notes, side_labels)


def _sheet_from_source(template, source, title=None):
    """The production page itself as the customer sheet: its first page, the spec panel and the printed
    token drawn over it, stamped. A spec panel needs the page's own size, taken from the file — the
    stored page_mm came from the same file, but the file is the truth."""
    import io

    import pikepdf
    from reportlab.pdfgen import canvas

    from . import specblock

    # The panels the admin marked as cuts get their brutto and safe lines derived like a flat
    # template's; a sheet whose only cut is the page draws no lines of its own.
    try:
        drawing, notes = derive(template)
    except TemplateError as problem:
        cuts = [o for o in (template.get("outlines") or []) if lines.defines_trim(o.get("type"))]
        drawing = {"netto": [shape.deserialise(o) for o in cuts], "brutto": [], "safe": [], "informational": []}
        notes = [f"linie: tylko cięcie — {problem}"]
    page_only = len(drawing["netto"]) == 1 and _union_box(drawing["netto"]) == (0.0, 0.0, *map(float, template["page_mm"]))
    outlines = template.get("outlines") or []
    # The page's own texts (a title across the top of a siatka) count as taken too — they are not
    # outlines, and the spec panel's heading was drawn across one (2026-10-18).
    page_w_mm, page_h_mm = (float(v) for v in template["page_mm"])
    # Every drawn path of the page counts as taken, not only the panels marked as cuts: a balloon's
    # gore grid is a hundred small cells no inspector lists, and the panel sat across it (2026-10-18).
    ink = _ink_cells(source, page_w_mm, page_h_mm)
    # A title set as live text is no path: its boxes are taken too (the frame tents' „NAMIOT
    # STELAŻOWY 3X3" had the spec panel laid across it, 2026-10-18).
    _mark_boxes(ink, _text_boxes_mm(source), page_w_mm, page_h_mm)
    # The spec panel belongs at the TOP of the sheet, above the shop's own drawing of the product
    # (Shyeline 2026-10-18: "place the spec boxes above it"): the top band first, the whole page
    # when the band has no room, the outline boxes when the page has no paths at all.
    top_band = (0.0, page_h_mm * (1 - SPEC_TOP_BAND_SHARE), page_w_mm, page_h_mm)
    free_box = (_free_rect_in(top_band, ink, page_w_mm, page_h_mm,
                              clearance_cells=SPEC_CLEARANCE_CELLS, least_share=0.08)
                or _free_rect_in((0.0, 0.0, page_w_mm, page_h_mm), ink, page_w_mm, page_h_mm,
                                 clearance_cells=SPEC_CLEARANCE_CELLS, least_share=0.02)
                or _free_box_mm(page_w_mm, page_h_mm, outlines, extra_boxes=_text_boxes_mm(source)))
    faces = _face_boxes_mm(template)
    with pikepdf.open(io.BytesIO(source)) as production:
        sheet = pikepdf.new()
        sheet.pages.append(production.pages[0])
        page = sheet.pages[0]
        box = page.mediabox
        page_w = float(box[2] - box[0]) / PT_PER_MM
        page_h = float(box[3] - box[1]) / PT_PER_MM
        page_pt = (page_w * PT_PER_MM, page_h * PT_PER_MM)
        # Everything laid over the production page is GUIDANCE and goes into one optional content
        # group — one layer in Affinity or Illustrator, removed with one click before the file goes
        # to print. A designer who exported the sheet with the magenta PRZÓD boxes still on it saw
        # them printed on the 3D gate (2026-10-18). The printed token is the one exception: it is
        # identity, not guidance, so it rides outside the layer and survives its deletion.
        guides_buffer = io.BytesIO()
        guides = canvas.Canvas(guides_buffer, pagesize=page_pt)
        guides.setTitle(title or template.get("name") or "Szablon")
        guides.setCreator("prepress-open")
        if not page_only:
            for entry in drawing["brutto"]:
                _stroke(guides, entry, BRUTTO_RGB)
            for entry in drawing["netto"]:
                _stroke(guides, entry, NETTO_RGB)
            for entry in drawing["safe"]:
                _stroke(guides, entry, SAFE_RGB)
        # The spec panel goes where the drawing is not: the largest empty rectangle of the page, so it
        # never covers a panel (it sat in the middle of the gate's arch, Shyeline 2026-10-18).
        spec = _spec_entry(template, drawing, "", mirrored=False)
        spec["safe_box_mm"] = free_box
        spec["zone_watermark"] = False               # a sheet of panels has no one safe zone to name
        spec["per_face"] = bool(faces)               # the areas are stated in each face, not for the page
        spec["legend"] = _legend(per_face=bool(faces))
        specblock.draw(guides, spec, page_w, page_h, lambda e: e["label"])
        # A siatka carries BOTH faces of the product on its one page, and a panel wraps round the
        # product, so one panel's halves face different ways (the arch band: lower half front, upper
        # half the back, upside down — read off a real filled template, 2026-10-18). The template's
        # `faces` name those regions; each is drawn as a labelled box with ITS OWN graphic and safe
        # area, so a customer knows where the PRZÓD and the TYŁ are and how big each really is.
        # Nothing drawn for a face may cover a line of the die (Shyeline 2026-10-18): the box sits
        # just inside the face, and its name and areas go into the largest empty spot inside it.
        for face, label, region_box in _face_entries(template):
            areas = face_areas_mm(region_box, template, face)
            if face.get("polygon_mm"):
                # A diagonal strip (a knee): its own outline, dashed, and the text turned along it,
                # centred — there is no axis-aligned empty rectangle to look for inside a 45° strip.
                # `frame: false` keeps the text and drops the outline (a tent leg's label strip).
                if face.get("frame", True):
                    _draw_face_polygon(guides, face["polygon_mm"], page_w, page_h)
                _draw_face_text_along(guides, label, face["polygon_mm"], float(face.get("rotation") or 0),
                                      min(page_w, page_h), areas)
                continue
            if face.get("frame", True):
                _draw_face_box(guides, region_box, page_w, page_h)
            _place_face_text(guides, face, label, region_box, template, ink, page_w, page_h, areas)
        guides.showPage()
        guides.save()
        token_buffer = io.BytesIO()
        token_only = canvas.Canvas(token_buffer, pagesize=page_pt)
        _draw_printed_token(token_only, template)
        token_only.showPage()
        token_only.save()
        _lay_over(sheet, page, guides_buffer.getvalue(), layer=GUIDES_LAYER_NAME)
        _lay_over(sheet, page, token_buffer.getvalue())
        out = io.BytesIO()
        sheet.save(out)
    notes = ["arkusz: strona produkcyjna z pliku (bryty, zagięcia i opisy bez zmian)", *notes]
    face_names = sorted({label for label, _box in faces})
    if face_names:
        notes.append("strony na arkuszu: " + ", ".join(face_names))
    return _stamp(out.getvalue(), template, notes)


GUIDES_LAYER_NAME = "Szablon — linie pomocnicze (usuń przed drukiem)"


def _lay_over(sheet, page, overlay_bytes, layer=None):
    """Draw a one-page PDF over `page` as one form XObject; with `layer`, that XObject is marked as
    optional content under a group of that name, which design apps show as a layer."""
    import io

    import pikepdf

    with pikepdf.open(io.BytesIO(overlay_bytes)) as overlay_pdf:
        name = page.add_overlay(overlay_pdf.pages[0])
    if layer:
        page.Resources.XObject[name].OC = _optional_content_group(sheet, layer)


def _optional_content_group(sheet, name):
    """The document's one optional content group of this name — created on first use, listed in the
    catalog's /OCProperties (and its default configuration), so viewers offer it as a layer."""
    import pikepdf

    properties = sheet.Root.get("/OCProperties")
    if properties is not None:
        for group in properties.OCGs:
            if str(group.get("/Name", "")) == name:
                return group
    group = sheet.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(name)))
    if properties is None:
        sheet.Root.OCProperties = pikepdf.Dictionary(
            OCGs=pikepdf.Array([group]),
            D=pikepdf.Dictionary(Order=pikepdf.Array([group]), ON=pikepdf.Array([group])))
    else:
        properties.OCGs.append(group)
        properties.D.Order.append(group)
        properties.D.ON.append(group)
    return group


FACE_BOX_RGB = (0.85, 0.10, 0.45)


def _face_boxes_mm(template):
    """[(label, (left, bottom, right, top)), …] from the template's `faces`: regions of the sheet in
    page millimetres, y up, each with the name a customer reads (`PRZÓD`, `TYŁ (obrócony o 180°)`).
    Data the shop sets per template; a template without `faces` gets no boxes."""
    boxes = []
    for face in template.get("faces") or []:
        if not isinstance(face, dict):
            continue
        box = face.get("box_mm")
        label = str(face.get("label") or "").strip()
        if label and isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                boxes.append((label, tuple(float(v) for v in box)))
            except (TypeError, ValueError):
                continue
    return boxes


def _face_entries(template):
    """The faces as stored, paired with their validated (label, box) — so the drawing can read a
    face's rotation and explicit areas next to its box."""
    valid = dict(_face_boxes_mm(template))
    for face in template.get("faces") or []:
        if isinstance(face, dict) and str(face.get("label") or "").strip() in valid:
            label = str(face.get("label")).strip()
            yield face, label, tuple(float(v) for v in face["box_mm"])


FACE_BOX_INSET_MM = 3.0


def _draw_face_box(pdf, box_mm, page_w, page_h):
    """A dashed box just INSIDE one face's region, sized to the page so it reads with the whole sheet
    on screen. Inset, because the region's edge usually IS a cut line, and a guide drawn on top of a
    cut line hides the one line the designer must see."""
    inset = FACE_BOX_INSET_MM
    left, bottom = max(0.0, box_mm[0]) + inset, max(0.0, box_mm[1]) + inset
    right, top = min(page_w, box_mm[2]) - inset, min(page_h, box_mm[3]) - inset
    if right <= left or top <= bottom:
        return
    short = min(page_w, page_h)
    pdf.saveState()
    pdf.setStrokeColorRGB(*FACE_BOX_RGB)
    pdf.setLineWidth(max(0.75, short * 0.0012))
    pdf.setDash((short * 0.01, short * 0.005), 0)
    pdf.rect(left * PT_PER_MM, bottom * PT_PER_MM, (right - left) * PT_PER_MM, (top - bottom) * PT_PER_MM, stroke=1, fill=0)
    pdf.restoreState()


def _draw_face_text(pdf, label, box_mm, template, home_mm, page_short_mm, rotation=0, areas=None):
    """The face's name and its two area lines, top-left in `home_mm` — the empty rectangle found
    inside the face — with no background, so even a tight fit never paints over a drawn line. Sizes
    follow the home: the name takes up to a third of its height, the lines share the rest. With a
    `rotation` the block is turned so its top points where the face's top does (a tent wall drawn
    on its side reads the right way up), the home's width and height swapping roles."""
    from . import specblock

    (graphic_w, graphic_h), (safe_w, safe_h) = areas or face_areas_mm(box_mm, template)
    # Short captions: the home can be one gore cell of a balloon, and a long caption there shrinks
    # the numbers below reading size.
    lines_of_text = [f"grafika / graphic: {graphic_w:.0f} x {graphic_h:.0f} mm",
                     f"bezpieczny / safe: {safe_w:.0f} x {safe_h:.0f} mm"]
    home_w, home_h = home_mm[2] - home_mm[0], home_mm[3] - home_mm[1]
    if home_w <= 0 or home_h <= 0:
        return
    rotation = rotation if rotation in (90, 180, 270) else 0
    along, across = (home_h, home_w) if rotation in (90, 270) else (home_w, home_h)
    # The name: never more than 2 % of the page (a 60 mm PRZÓD across a 176 mm band, 2026-10-18),
    # never more than a third of the home's height, and it must fit the home's width.
    label_pt = specblock._pick_size(label, specblock.BOLD, along * 0.95, _name_cap_pt(across, page_short_mm), 4.0)
    # The lines: half the name, and never taller than what the remaining height holds.
    spec_pt = min(specblock._pick_size(line, specblock.FONT, along * 0.95,
                                       min(label_pt * 0.5, page_short_mm * 0.006 * PT_PER_MM), 3.0)
                  for line in lines_of_text)
    spec_pt = min(spec_pt, (across * PT_PER_MM - label_pt * 1.2) / (len(lines_of_text) * 1.3))
    pdf.saveState()
    # The block is written unrotated in a frame whose origin is the home's corner that is its
    # top-left once turned, so the same code serves every rotation.
    corner = {0: (home_mm[0], home_mm[3]), 90: (home_mm[2], home_mm[3]),
              180: (home_mm[2], home_mm[1]), 270: (home_mm[0], home_mm[1])}[rotation]
    pdf.translate(corner[0] * PT_PER_MM, corner[1] * PT_PER_MM)
    pdf.rotate(rotation)
    pdf.setFillColorRGB(*FACE_BOX_RGB)
    pdf.setFont(specblock.BOLD, label_pt)
    baseline_pt = -label_pt
    pdf.drawString(0, baseline_pt, label)
    if spec_pt >= 3.0:
        pdf.setFillColorRGB(*specblock.PANEL_TEXT)
        pdf.setFont(specblock.FONT, spec_pt)
        for line in lines_of_text:
            baseline_pt -= spec_pt * 1.3
            pdf.drawString(0, baseline_pt, line)
    pdf.restoreState()


def _name_cap_pt(across_mm, page_short_mm):
    """The largest a face's name may be: 2 % of the page, and a third of the home's height — ONE cap for the
    readability test (_place_face_text) and the drawing (_draw_face_text), or a name judged readable at 6 pt was
    drawn smaller inside a low home (audit 2026-10-28)."""
    return min(page_short_mm * 0.02, across_mm * 0.33) * PT_PER_MM


FACE_TEXT_READABLE_PT = 6.0     # a face's name smaller than this is unreadable on screen: it goes outside the face, with a leader
FACE_TEXT_REACH_SHARE = 0.25    # how far round a face its text may sit, as a share of the page's short side


def _place_face_text(pdf, face, label, region_box, template, ink, page_w, page_h, areas):
    """The face's name and areas inside the face where there is room — inside its `within_mm` outline
    when it has one, since a tent wall's box has empty corners OUTSIDE the wall and the name landed
    there (2026-10-23) — turned by its rotation at any angle; and where the room inside would make the
    name unreadable (a balloon's one gore cell), in the nearest empty spot outside the face, with a
    leader line to it, so nothing is ever written over a line of the die."""
    from . import specblock

    rotation = float(face.get("rotation") or 0) % 360
    page_short = min(page_w, page_h)
    face_ink = _outside_taken(ink, face["within_mm"], page_w, page_h) if face.get("within_mm") else ink
    home = _free_rect_in(region_box, face_ink, page_w, page_h) or region_box
    along, across = ((home[3] - home[1], home[2] - home[0]) if rotation in (90, 270)
                     else (home[2] - home[0], home[3] - home[1]))
    label_pt = specblock._pick_size(label, specblock.BOLD, along * 0.95, _name_cap_pt(across, page_short), 1.0)
    if label_pt < FACE_TEXT_READABLE_PT:
        outside = _free_rect_near(region_box, ink, page_w, page_h)
        if outside:
            _draw_leader(pdf, outside, region_box, page_short)
            _draw_face_text(pdf, label, region_box, template, outside, page_short, rotation=0, areas=areas)
            _mark_boxes(ink, [outside], page_w, page_h)      # the next face looks for its own spot
            return
    # The spot is taken once written: two faces of one region (a chwiej's head band and its half) or
    # two faces sent outside (the Flash balloon's) were written over each other (2026-10-23).
    _mark_boxes(ink, [home], page_w, page_h)
    if rotation % 90:
        corners = [[home[0], home[1]], [home[2], home[1]], [home[2], home[3]], [home[0], home[3]]]
        _draw_face_text_along(pdf, label, corners, rotation, page_short, areas)
        return
    _draw_face_text(pdf, label, region_box, template, home, page_short, rotation=int(rotation), areas=areas)


def _outside_taken(ink, polygon_mm, page_w, page_h):
    """A copy of the ink grid with every cell whose centre lies outside `polygon_mm` taken as well."""
    if not ink:
        return ink
    rows, columns = len(ink), len(ink[0])
    cell_w, cell_h = page_w / columns, page_h / rows
    return [[ink[row][column] or not _inside_polygon(((column + 0.5) * cell_w, (row + 0.5) * cell_h), polygon_mm)
             for column in range(columns)] for row in range(rows)]


def _inside_polygon(point, polygon):
    """Even-odd point-in-polygon (a ray cast to +x)."""
    x, y = point
    inside = False
    for index in range(len(polygon)):
        (x0, y0), (x1, y1) = polygon[index], polygon[(index + 1) % len(polygon)]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _free_rect_near(box_mm, ink, page_w, page_h):
    """The largest empty rectangle in the bands just outside `box_mm` — below, above, left, right, each
    reaching FACE_TEXT_REACH_SHARE of the page's short side — or None."""
    reach = min(page_w, page_h) * FACE_TEXT_REACH_SHARE
    left, bottom, right, top = box_mm
    bands = [(left - reach, bottom - reach, right + reach, bottom), (left - reach, top, right + reach, top + reach),
             (left - reach, bottom, left, top), (right, bottom, right + reach, top)]
    found = []
    for band in bands:
        clipped = (max(0.0, band[0]), max(0.0, band[1]), min(page_w, band[2]), min(page_h, band[3]))
        if clipped[2] - clipped[0] <= 0 or clipped[3] - clipped[1] <= 0:
            continue
        rect = _free_rect_in(clipped, ink, page_w, page_h)
        if rect:
            found.append(rect)
    return max(found, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), default=None)


def _draw_leader(pdf, text_rect, box_mm, page_short_mm):
    """A thin line from the text's edge to the nearest point of the face it names."""
    cx, cy = (text_rect[0] + text_rect[2]) / 2, (text_rect[1] + text_rect[3]) / 2
    target = (min(max(cx, box_mm[0]), box_mm[2]), min(max(cy, box_mm[1]), box_mm[3]))
    edges = [((text_rect[0] + text_rect[2]) / 2, text_rect[1]), ((text_rect[0] + text_rect[2]) / 2, text_rect[3]),
             (text_rect[0], (text_rect[1] + text_rect[3]) / 2), (text_rect[2], (text_rect[1] + text_rect[3]) / 2)]
    start = min(edges, key=lambda e: (e[0] - target[0]) ** 2 + (e[1] - target[1]) ** 2)
    pdf.saveState()
    pdf.setStrokeColorRGB(*FACE_BOX_RGB)
    pdf.setLineWidth(max(0.5, page_short_mm * 0.0008))
    pdf.setDash((), 0)
    pdf.line(start[0] * PT_PER_MM, start[1] * PT_PER_MM, target[0] * PT_PER_MM, target[1] * PT_PER_MM)
    pdf.restoreState()


def _legend(per_face):
    """What each guide line means, for the spec panel's legend — the colours as this module draws them
    (every sheet left the shop without a key until 2026-10-23)."""
    entries = [{"label": "cięcie / cut", "word": "różowa ciągła", "colour": NETTO_RGB, "dash": False},
               {"label": "spad / bleed", "word": "szara", "colour": BRUTTO_RGB, "dash": False},
               {"label": "bezpieczny / safe", "word": "zielona", "colour": SAFE_RGB, "dash": False}]
    if per_face:
        entries.append({"label": "pole / face", "word": "różowa kreskowana", "colour": FACE_BOX_RGB, "dash": True})
    return entries


INK_CELL_MM = 4.0           # the grid the page's paths are walked on when looking for empty space
INK_CLEARANCE_CELLS = 1     # cells kept between a face's text and any drawn line
SPEC_CLEARANCE_CELLS = 4    # ~15 mm kept round the spec panel, as the outline-box placement kept
SPEC_TOP_BAND_SHARE = 0.3   # the spec panel looks for room in the top 30 % of the sheet first


def _ink_cells(pdf_bytes, page_w, page_h, page_index=0):
    """Which cells of a page grid a drawn path runs through: every painted subpath of the page — the
    reader behind the admin inspector, no size floor, so a cut, a fold, a centre line and the shop's
    outlined captions all count — flattened and walked cell by cell. A fill's inside stays free (a
    balloon's yellow band is room, its edges are not). Rows bottom-first; None when the page holds
    no paths."""
    from . import offset, outline

    columns, rows = max(1, int(page_w / INK_CELL_MM)), max(1, int(page_h / INK_CELL_MM))
    cell_w, cell_h = page_w / columns, page_h / rows
    taken = [[False] * columns for _ in range(rows)]
    seen = False
    for subpath in outline._subpaths(pdf_bytes, page_index):
        points = offset.flatten(subpath)
        if subpath.get("closed") and len(points) > 1:
            points.append(points[0])
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            seen = True
            steps = max(1, int(max(abs(x1 - x0) / cell_w, abs(y1 - y0) / cell_h) * 2))
            for step in range(steps + 1):
                x, y = x0 + (x1 - x0) * step / steps, y0 + (y1 - y0) * step / steps
                column, row = int(x / cell_w), int(y / cell_h)
                if 0 <= column < columns and 0 <= row < rows:
                    taken[row][column] = True
    return taken if seen else None


def _mark_boxes(ink, boxes, page_w, page_h):
    """Take the cells under these (left, bottom, right, top) mm boxes — text blocks, which the path
    walk never sees. A None grid (a page without paths) is left as it is."""
    if not ink:
        return
    rows, columns = len(ink), len(ink[0])
    cell_w, cell_h = page_w / columns, page_h / rows
    for left, bottom, right, top in boxes:
        for row in range(max(0, int(bottom / cell_h)), min(rows, int(top / cell_h) + 1)):
            for column in range(max(0, int(left / cell_w)), min(columns, int(right / cell_w) + 1)):
                ink[row][column] = True


def _free_rect_in(box_mm, ink, page_w, page_h, clearance_cells=INK_CLEARANCE_CELLS, least_share=0.0):
    """The largest empty rectangle inside `box_mm` (page mm, y up), clear of every inked cell plus a
    clearance, or None when there is no room (less than `least_share` of the box) or no paths."""
    if not ink:
        return None
    rows, columns = len(ink), len(ink[0])
    cell_w, cell_h = page_w / columns, page_h / rows
    col_from, col_to = max(0, int(box_mm[0] / cell_w) + 1), min(columns, int(box_mm[2] / cell_w))
    row_from, row_to = max(0, int(box_mm[1] / cell_h) + 1), min(rows, int(box_mm[3] / cell_h))
    if col_to - col_from < 3 or row_to - row_from < 3:
        return None
    clearance = clearance_cells
    taken = [[any(ink[r][c] for r in range(max(0, row - clearance), min(rows, row + clearance + 1))
                  for c in range(max(0, column - clearance), min(columns, column + clearance + 1)))
              for column in range(col_from, col_to)] for row in range(row_from, row_to)]
    best = _largest_free_rectangle(taken)
    if not best or (best[2] - best[0]) * (best[3] - best[1]) < least_share * (col_to - col_from) * (row_to - row_from):
        return None
    c0, r0, c1, r1 = best
    return ((col_from + c0) * cell_w, (row_from + r0) * cell_h, (col_from + c1) * cell_w, (row_from + r1) * cell_h)


def _largest_free_rectangle(taken):
    """(col_from, row_from, col_to, row_to) of the biggest all-free rectangle in a grid of booleans
    (histogram method), or None when every cell is taken."""
    if not taken or not taken[0]:
        return None
    rows, columns = len(taken), len(taken[0])
    best, best_area = None, 0
    heights = [0] * columns
    for row in range(rows):
        heights = [0 if taken[row][col] else heights[col] + 1 for col in range(columns)]
        stack = []
        for col in range(columns + 1):
            height = heights[col] if col < columns else 0
            start = col
            while stack and stack[-1][1] >= height:
                start, stacked = stack.pop()
                area = stacked * (col - start)
                if area > best_area:
                    best_area = area
                    best = (start, row - stacked + 1, col, row + 1)
            stack.append((start, height))
    return best


def _draw_face_polygon(pdf, polygon_mm, page_w, page_h):
    """A dashed outline of a polygon face, pulled FACE_BOX_INSET_MM towards its centroid so it does
    not sit on the knee's own cut line."""
    import math

    cx = sum(p[0] for p in polygon_mm) / len(polygon_mm)
    cy = sum(p[1] for p in polygon_mm) / len(polygon_mm)
    points = []
    for x, y in polygon_mm:
        distance = math.hypot(x - cx, y - cy) or 1.0
        pull = min(FACE_BOX_INSET_MM, distance * 0.5)
        points.append(((x - (x - cx) / distance * pull) * PT_PER_MM, (y - (y - cy) / distance * pull) * PT_PER_MM))
    short = min(page_w, page_h)
    pdf.saveState()
    pdf.setStrokeColorRGB(*FACE_BOX_RGB)
    pdf.setLineWidth(max(0.75, short * 0.0012))
    pdf.setDash((short * 0.01, short * 0.005), 0)
    path = pdf.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.close()
    pdf.drawPath(path, stroke=1, fill=0)
    pdf.restoreState()


def _draw_face_text_along(pdf, label, polygon_mm, rotation, page_short_mm, areas):
    """The face's name and two area lines turned by `rotation` degrees and centred on the polygon:
    the text runs along a diagonal strip, sized to the strip's length along and width across."""
    import math

    from . import specblock

    (graphic_w, graphic_h), (safe_w, safe_h) = areas
    lines_of_text = [f"grafika / graphic: {graphic_w:.0f} x {graphic_h:.0f} mm",
                     f"bezpieczny / safe: {safe_w:.0f} x {safe_h:.0f} mm"]
    cx = sum(p[0] for p in polygon_mm) / len(polygon_mm)
    cy = sum(p[1] for p in polygon_mm) / len(polygon_mm)
    # The strip's extents in the text's own frame: along the baseline and across it.
    angle = math.radians(rotation)
    along = [(p[0] - cx) * math.cos(angle) + (p[1] - cy) * math.sin(angle) for p in polygon_mm]
    across = [-(p[0] - cx) * math.sin(angle) + (p[1] - cy) * math.cos(angle) for p in polygon_mm]
    along_mm, across_mm = max(along) - min(along), max(across) - min(across)
    if along_mm <= 0 or across_mm <= 0:
        return
    label_cap_pt = min(page_short_mm * 0.02, across_mm * 0.3) * PT_PER_MM
    label_lines = _label_lines(label, along_mm * 0.8, label_cap_pt)
    label_pt = min(specblock._pick_size(line, specblock.BOLD, along_mm * 0.8, label_cap_pt, 4.0) for line in label_lines)
    spec_pt = min(specblock._pick_size(line, specblock.FONT, along_mm * 0.8,
                                       min(label_pt * 0.5, page_short_mm * 0.006 * PT_PER_MM), 3.0)
                  for line in lines_of_text)
    block_pt = label_pt * 1.2 * len(label_lines) + spec_pt * 1.3 * len(lines_of_text)
    pdf.saveState()
    pdf.translate(cx * PT_PER_MM, cy * PT_PER_MM)
    pdf.rotate(rotation)
    pdf.setFillColorRGB(*FACE_BOX_RGB)
    pdf.setFont(specblock.BOLD, label_pt)
    baseline_pt = block_pt / 2 - label_pt
    for index, line in enumerate(label_lines):
        if index:
            baseline_pt -= label_pt * 1.2
        pdf.drawCentredString(0, baseline_pt, line)
    if spec_pt >= 3.0:
        pdf.setFillColorRGB(*specblock.PANEL_TEXT)
        pdf.setFont(specblock.FONT, spec_pt)
        for line in lines_of_text:
            baseline_pt -= spec_pt * 1.3
            pdf.drawCentredString(0, baseline_pt, line)
    pdf.restoreState()


LABEL_WRAP_BELOW_PT = 8.0


def _label_lines(label, width_mm, largest_pt):
    """The label as one line — or split at a comma into two when one line would have to shrink below
    LABEL_WRAP_BELOW_PT to fit `width_mm` (a tent leg's 56-character label beside a two-square blank
    came out at 4.5 pt, 2026-10-19). The split falls at the last comma before the label's middle."""
    from . import specblock

    if ", " not in label or specblock._pick_size(label, specblock.BOLD, width_mm, largest_pt, 4.0) >= LABEL_WRAP_BELOW_PT:
        return [label]
    cut = label.rfind(", ", 0, len(label) // 2 + 8)
    if cut < 0:
        cut = label.find(", ")
    return [label[:cut + 1], label[cut + 2:]]


def face_areas_mm(box_mm, template, face=None):
    """One face's TRUE sizes: its graphic area is the stored box at full size, its safe area that box
    taken in by the template's safe margin on every side — unless the face states them itself
    (`graphic_mm` / `safe_mm`: a tent leg's squares lie at 45°, so their box says nothing true). The
    page-level numbers lie on a siatka — the union of a gate's panels is not a thing anyone designs to
    (Shyeline 2026-10-18)."""
    scale = scale_of(template)
    safe = float(template.get("safe_mm") or 0.0)
    width, height = box_mm[2] - box_mm[0], box_mm[3] - box_mm[1]
    graphic = (width * scale, height * scale)
    safe_area = (max(0.0, width - 2 * safe) * scale, max(0.0, height - 2 * safe) * scale)
    if face:
        if face.get("graphic_mm"):
            graphic = tuple(float(v) for v in face["graphic_mm"])
        if face.get("safe_mm"):
            safe_area = tuple(float(v) for v in face["safe_mm"])
    return graphic, safe_area


def pdfmetrics_string_width(text, font, size_pt):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, font, size_pt)


FREE_BOX_GRID = 64            # cells per side when looking for the empty rectangle
FREE_BOX_CLEARANCE_MM = 15.0  # kept between the spec panel's home and any outline


def _text_boxes_mm(pdf_bytes, page_index=0):
    """The page's text blocks as (left, bottom, right, top) in millimetres, y up — what a siatka's
    title occupies; [] when the page has no text or cannot be read."""
    import io

    try:
        import pypdfium2

        document = pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))
        page = document[page_index]
        textpage = page.get_textpage()
        count = textpage.count_rects()
        return [tuple(float(v) / PT_PER_MM for v in textpage.get_rect(index)) for index in range(count)]
    except Exception:                               # noqa: BLE001 — a page without readable text has no text boxes
        return []


def _free_box_mm(page_w, page_h, outlines, extra_boxes=()):
    """The largest empty axis-aligned rectangle of the page, clear of every outline's box (and of
    `extra_boxes`, the page's texts), or None when nothing usable is free (the caller then centres the
    panel as on a flat sheet).

    Coarse on purpose: the page is cut into a grid, cells touched by an outline's bounding box (plus a
    clearance) are taken, and the biggest free rectangle of cells is read off with the histogram
    method — a 64 × 64 grid on a 2.3 m sheet is 36 mm cells, plenty for placing a panel."""
    boxes = [lines._box(o) for o in outlines]
    boxes = [b for b in boxes if b] + [tuple(b) for b in extra_boxes if b and len(b) == 4]
    if not boxes or page_w <= 0 or page_h <= 0:
        return None
    cell_w, cell_h = page_w / FREE_BOX_GRID, page_h / FREE_BOX_GRID
    taken = [[False] * FREE_BOX_GRID for _ in range(FREE_BOX_GRID)]
    for left, bottom, right, top in boxes:
        col_from = max(0, int((left - FREE_BOX_CLEARANCE_MM) / cell_w))
        col_to = min(FREE_BOX_GRID - 1, int((right + FREE_BOX_CLEARANCE_MM) / cell_w))
        row_from = max(0, int((bottom - FREE_BOX_CLEARANCE_MM) / cell_h))
        row_to = min(FREE_BOX_GRID - 1, int((top + FREE_BOX_CLEARANCE_MM) / cell_h))
        for row in range(row_from, row_to + 1):
            for col in range(col_from, col_to + 1):
                taken[row][col] = True
    best = _largest_free_rectangle(taken)
    if not best or (best[2] - best[0]) * (best[3] - best[1]) < (FREE_BOX_GRID * FREE_BOX_GRID) * 0.03:
        return None
    col_from, row_from, col_to, row_to = best
    return (col_from * cell_w, row_from * cell_h, col_to * cell_w, row_to * cell_h)


def scale_of(template):
    """What one millimetre of the sheet is in the product: 1 for a flat template, N for a sheet drawn
    at 1:N (an inflatable's siatka). Anything unexpected reads as natural size."""
    try:
        return max(1, int(template.get("scale") or 1))
    except (TypeError, ValueError):
        return 1


def back_mirrored(template):
    """Whether the second side is the front seen through the fabric (a flag: yes, the default) or a print of
    its own (a tension-fabric wall: `back_mirrored: false`)."""
    return template.get("back_mirrored") is not False


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
    # A sheet drawn at 1:N (template `scale`): the panel states full-size millimetres, as the stamp does.
    scale = scale_of(template)
    return {"material_name": f"{name} — {side}" if side else name,
            "label": side,
            "safe_box_mm": home,
            "scale": scale,
            "brutto_mm": (float(brutto[0]) * scale, float(brutto[1]) * scale),
            "safe_mm_box": (float(safe[0]) * scale, float(safe[1]) * scale),
            "bleed_mm": float(template.get("bleed_mm") or 0.0) * scale,
            "safe_mm": float(template.get("safe_mm") or 0.0) * scale,
            "spec_position": template.get("spec_position") or "panel",
            "spec_strip_mm": 0.0,
            # What the customer writes in besides the file name (a constant-pressure tent's „kolor nóg").
            "fill_in": list(template.get("fill_in") or [])}


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
    # The stamp's geometry is FULL size and its page is as drawn (the generator's convention, which
    # the measuring and the rules divide by `scale`): a sheet template drawn at 1:N multiplies here.
    scale = scale_of(template)
    return {
        "v": STAMP_VERSION,
        "template": template.get("token"),
        "material": template.get("material") or "",
        "page_mm": [round(float(v), 2) for v in template["page_mm"]],
        "netto_mm": [round(float(v) * scale, 2) for v in (boxes["netto_mm"] or template["page_mm"])],
        "bleed_mm": round(float(template.get("bleed_mm") or 0.0) * scale, 2),
        "safe_mm": round(float(template.get("safe_mm") or 0.0) * scale, 2),
        # Carried even though the checker still reads only `safe_mm`: the sides are what the shop
        # actually set, and a returning file that lost them cannot be judged against the template it
        # was issued from.
        "sewn_sides_mm": {side: round(value * scale, 2)
                          for side, value in sewn_mm(template).items()},
        "safe_total_sides_mm": {side: round(value * scale, 2)
                                for side, value in margins_mm(template).items()},
        "scale": scale,
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
                 "mirrored": index > 0 and back_mirrored(template)},
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
