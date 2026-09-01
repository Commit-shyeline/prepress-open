"""What the PDF DECLARES about itself, read with pikepdf.

The counterpart to `measure.py`: that module renders pixels and reports what the ink does, this one
reads objects and reports what the file says it contains — fonts, colour spaces, spot colorants,
overprint, page count, and who produced it. No rendering, so it is fast and it works on files too big
to rasterise.

Every extractor here was checked against the in-house engine it replaces, which uses PyMuPDF, on real
production files (2026-08-23). Fonts, producer, creator, page count, overprint and spot names came out
IDENTICAL, including `['Cut', 'Regmark']` on a real cut file. Two differences were found, and both
are handled rather than papered over:

* `ICCBased` alone says nothing. PyMuPDF reports `ICCBased(RGB,…)`; pikepdf hands back the array, so
  the profile stream's `/N` is read — 1 Gray, 3 RGB, 4 CMYK. Without that the RGB rule would look for
  "RGB" in a string that never contains it and pass every RGB file ever made.
* `Separation` and `DeviceN` are colorant spaces, not colour modes. They are routed to `spot_names`
  and kept OUT of `colour_spaces`, or a correct cut file would report its die line as a colour mode.

KNOWN LIMIT, stated because a silent one is worse: colour is read from named resources and image
XObjects only. A fully vector file that sets RGB inline with the `rg` operator declares no colour
space anywhere, so nothing is reported and the rule says "not measured" instead of guessing. The
in-house engine has the same blind spot. Scanning content streams for `rg`/`RG` would close it, but
our OWN templates stroke their guides in RGB, so it would flag correct returns — a false positive is
worse than an honest gap.
"""
import math
import os
import re
import unicodedata

# A PDF exported from an office application is almost never artwork, and magic bytes cannot catch it —
# a Word document saved as PDF *is* a valid PDF. The producer metadata is the reliable tell, and in
# the in-house tool it caught a real case: an A4 letter to a city office sitting among print files.
OFFICE_PRODUCERS = ("word", "excel", "powerpoint", "microsoft print to pdf", "libreoffice")

# Spot colorants that are INSTRUCTIONS TO A MACHINE, not inks. Telling a customer to convert these to
# CMYK is actively destructive — it would flatten their cut path into printed artwork. Matched as
# case-insensitive substrings because real files spell them "Cut", "cut", "Cut 2" and "Partial cut".
# The Polish entries are flag-finishing separations — sewing line, sleeve, hem, pole — and they are
# STEMS, not words: "szyci" catches "szycie" and the genitive "szycia", "tunel" catches "tunelu".
TECHNICAL_SPOT_MARKERS = ("cut", "contour", "crease", "fold", "drill", "perf", "regmark",
                          "register", "kiss", "score", "szyci", "tunel", "obszyci", "drzewiec",
                          "cięc", "ciec", "zagięc", "zagiec")

# Names that mark the cutting path specifically. A Crease or a Drill is technical but is not the cut.
CUT_SPOT_MARKERS = ("cut", "contour")

# The four process inks plus the two names PDF reserves. A DeviceN image is often separated into
# /Cyan /Magenta /Yellow /Black, and calling those "extra inks" produced the advice "convert Cyan to
# CMYK", which means nothing.
PROCESS_COLORANTS = frozenset({"cyan", "magenta", "yellow", "black", "all", "none",
                               "process cyan", "process magenta", "process yellow",
                               "process black"})

# Every page is read, not just the first: in the in-house corpus 12 % of files are multipage and 22 %
# of those have a page 1 that misrepresents the file — one job hid an RGB page behind a grey first
# page, which a page-1-only check called clean. That is a false NEGATIVE, the kind nobody finds until
# it prints. The cap bounds the cost and sits above the largest page count seen.
MAX_PAGES_READ = 12

# A placed logo carries its own /Resources, so its fonts and colours live one level down. Recursion is
# capped because a malformed file can reference itself.
MAX_XOBJECT_DEPTH = 4

# What `/N` on an ICC profile means, which is the only way to tell an RGB profile from a CMYK one.
ICC_COMPONENTS = {1: "Gray", 3: "RGB", 4: "CMYK"}

_PDF_NAME_ESCAPE = re.compile(r"#([0-9A-Fa-f]{2})")

# The operators that actually SHOW text. A `/Font` resource only proves a font is available, and
# reportlab — which draws this project's own templates — registers Helvetica whether or not anything
# is typed. So a customer who pasted artwork over our spec panel would be told to convert text that
# is not there. Text-showing operators are the real "converted to curves" test.
TEXT_OPERATORS = frozenset({"Tj", "TJ", "'", '"'})


def facts(pdf_bytes, filename="", max_pages=MAX_PAGES_READ):
    """Everything the file declares, or `readable: False` with a reason.

    An unreadable file is an ANSWER — password-protected, damaged, not a PDF — so it never raises.
    """
    import pikepdf

    result = {"readable": False, "reason": "", "pages": 0, "producer": "", "creator": "",
              "fonts": [], "colour_spaces": [], "spot_names": [], "overprint": False,
              "shows_text": False}
    result.update(filename_facts(filename))
    try:
        with pikepdf.open(_stream(pdf_bytes)) as pdf:
            result["pages"] = len(pdf.pages)
            info = pdf.docinfo or {}
            result["producer"] = _text(info.get("/Producer"))
            result["creator"] = _text(info.get("/Creator"))
            fonts, spaces, spots = set(), set(), set()
            overprint = shows_text = False
            for page in list(pdf.pages)[:max_pages]:
                resources = page.obj.get("/Resources")
                found = _read_resources(resources, set(), 0)
                fonts |= found["fonts"]
                spaces |= found["colour_spaces"]
                spots |= found["spot_names"]
                overprint = overprint or found["overprint"]
                shows_text = shows_text or _shows_text(page.obj, resources, 0, set())
            result.update(readable=True, fonts=sorted(fonts), colour_spaces=sorted(spaces),
                          spot_names=sorted(spots), overprint=overprint, shows_text=shows_text)
    except pikepdf.PasswordError:
        result["reason"] = "password"
    except Exception as error:                      # noqa: BLE001 — a bad upload is a verdict
        result["reason"] = f"{type(error).__name__}"
    return result


def filename_facts(filename):
    """What the NAME alone says — the two naming rules customers break.

    Both travel badly: a file server and a RIP handle ASCII names reliably, and `ł` in particular has
    a history of arriving mangled. An extra dot makes the extension ambiguous to the tools downstream.
    """
    stem = os.path.splitext(filename or "")[0]
    return {"extension": os.path.splitext(filename or "")[1].lstrip(".").lower(),
            "has_diacritics": _has_diacritics(stem),
            "extra_dots": "." in stem}


def _has_diacritics(text):
    """True if any letter carries a diacritic (ą, ó, ł …)."""
    return any(unicodedata.combining(character)
               for character in unicodedata.normalize("NFD", text))


def _read_resources(resources, visited, depth):
    """Fonts, colour spaces, colorants and overprint from one /Resources dictionary and, one level
    down, from every Form XObject it places."""
    found = {"fonts": set(), "colour_spaces": set(), "spot_names": set(), "overprint": False}
    if resources is None or depth > MAX_XOBJECT_DEPTH:
        return found

    for name, font in _entries(resources, "/Font"):
        found["fonts"].add(_font_name(font, name))
    for _name, space in _entries(resources, "/ColorSpace"):
        _classify_colour_space(space, found)
    for _name, state in _entries(resources, "/ExtGState"):
        try:
            if bool(state.get("/OP")) or bool(state.get("/op")):
                found["overprint"] = True
        except Exception:                           # noqa: BLE001 — one bad state is not a verdict
            continue
    for _name, xobject in _entries(resources, "/XObject"):
        try:
            subtype = str(xobject.get("/Subtype"))
            if subtype == "/Image":
                _classify_colour_space(xobject.get("/ColorSpace"), found)
                continue
            key = _identity(xobject)
            if subtype == "/Form" and key not in visited:
                visited.add(key)
                nested = _read_resources(xobject.get("/Resources"), visited, depth + 1)
                found["fonts"] |= nested["fonts"]
                found["colour_spaces"] |= nested["colour_spaces"]
                found["spot_names"] |= nested["spot_names"]
                found["overprint"] = found["overprint"] or nested["overprint"]
        except Exception:                           # noqa: BLE001 — skip an unreadable xobject
            continue
    return found


def _shows_text(container, resources, depth, walking):
    """True as soon as one content stream actually draws text, forms included."""
    import pikepdf

    try:
        instructions = pikepdf.parse_content_stream(container)
    except Exception:                               # noqa: BLE001 — an unparseable stream shows none
        return False
    for operands, operator in instructions:
        name = str(operator)
        if name in TEXT_OPERATORS:
            return True
        if name != "Do" or not operands or depth >= MAX_XOBJECT_DEPTH:
            continue
        form = _lookup(resources, "/XObject", operands[0])
        if form is None:
            continue
        try:
            if str(form.get("/Subtype")) != "/Form":
                continue
        except Exception:                           # noqa: BLE001
            continue
        key = _identity(form)
        if key in walking:
            continue
        walking.add(key)
        try:
            if _shows_text(form, form.get("/Resources") or resources, depth + 1, walking):
                return True
        finally:
            walking.discard(key)
    return False


def _entries(resources, key):
    """(name, object) pairs from one resource sub-dictionary, empty when it is absent or damaged."""
    try:
        entry = resources.get(key)
        return list(entry.items()) if entry is not None else []
    except Exception:                               # noqa: BLE001
        return []


def _font_name(font, fallback):
    """The font's BaseFont, subset prefix included — `AAAAAA+Consolas` is how the file names it."""
    try:
        return str(font.get("/BaseFont", fallback)).lstrip("/")
    except Exception:                               # noqa: BLE001
        return str(fallback).lstrip("/")


def _classify_colour_space(space, found):
    """Sort one colour space into a colour MODE or a spot COLORANT.

    They are different questions with different answers: RGB is something to convert, a `Cut`
    separation is something to leave alone.
    """
    if space is None:
        return
    try:
        if not _is_array(space):
            name = str(space).lstrip("/")
            if name:
                found["colour_spaces"].add(name)
            return
        family = str(space[0]).lstrip("/")
        if family == "Separation":
            found["spot_names"].add(_decode_pdf_name(str(space[1]).lstrip("/")))
        elif family == "DeviceN":
            for colorant in space[1]:
                found["spot_names"].add(_decode_pdf_name(str(colorant).lstrip("/")))
        elif family == "Indexed":
            _classify_colour_space(space[1], found)
        elif family == "ICCBased":
            components = int(space[1].get("/N", 0) or 0)
            found["colour_spaces"].add(
                f"ICCBased({ICC_COMPONENTS[components]})" if components in ICC_COMPONENTS
                else "ICCBased")
        else:
            found["colour_spaces"].add(family)
    except Exception:                               # noqa: BLE001 — a damaged space is not a mode
        return
    found["spot_names"].discard("All")
    found["spot_names"].discard("None")


def _is_array(space):
    try:
        return not isinstance(space, str) and len(space) > 1 and str(space[0]).startswith("/")
    except Exception:                               # noqa: BLE001
        return False


def _decode_pdf_name(raw):
    """`PANTONE#20711#20C` → `PANTONE 711 C`. `#20` is a space in a PDF name, and left encoded it is
    unreadable in a report and unmatchable against the marker lists."""
    return _PDF_NAME_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), raw)


# ── Where the rasters land, and at what resolution ───────────────────────────
#
# A placement covering less than this share of the page is decorative — an icon, a logo, a bullet —
# and its resolution must not decide the verdict for the whole print. In the in-house corpus 1 %
# cleanly separates real artwork (5 %–99 % of the page) from inline icons (0.2 %–0.8 %).
SIGNIFICANT_AREA_FRACTION = 0.01


def placed_images(pdf_bytes, page_index=0):
    """Every raster placement on the page, with the resolution it will actually print at.

    This is the CTM walk: an image XObject is always drawn into the unit square, so its size on the
    page is whatever the current transformation matrix makes of that square. Nothing else in a PDF
    records the placed size, which is why the number cannot be read off the image itself — a 4000 px
    image is 400 DPI on a 250 mm page and 40 DPI on a 2.5 m one.

    Two traps, both learned the expensive way in the engine this replaces:

    * the placed size comes from the MAGNITUDES OF THE CTM'S COLUMN VECTORS, not from a bounding box.
      A bounding box is axis-aligned, so under a 90° rotation its width belongs to the image's pixel
      HEIGHT — pairing them reported 112 DPI for a rotated banner that was really 75.
    * a form XObject carries its own /Matrix and its own /Resources, so a logo placed inside one is
      scaled twice. Ignoring that reports the logo's resolution as if the form were drawn 1:1.
    """
    import pikepdf

    try:
        with pikepdf.open(_stream(pdf_bytes)) as pdf:
            page = pdf.pages[page_index]
            box = [float(v) for v in (page.obj.get("/CropBox") or page.mediabox)]
            page_area_pt = abs((box[2] - box[0]) * (box[3] - box[1]))
            found = []
            _walk_placements(page.obj, page.obj.get("/Resources"), pikepdf.Matrix(),
                             0, found, set(), page_area_pt)
            # PDF puts y=0 at the BOTTOM; every consumer of these rects (the raster, the overlay,
            # the 3D model) puts it at the TOP. Converted here, once, so nobody downstream flips.
            page_height_pt = box[3] - box[1]
            for placement in found:
                x0, y0, x1, y1 = placement.pop("_bbox_pt")
                placement["rect_mm"] = [round((x0 - box[0]) * 25.4 / 72, 1),
                                        round((page_height_pt - (y1 - box[1])) * 25.4 / 72, 1),
                                        round((x1 - x0) * 25.4 / 72, 1),
                                        round((y1 - y0) * 25.4 / 72, 1)]
            return found
    except Exception:                               # noqa: BLE001 — a bad file has no placements
        return []


def _walk_placements(container, resources, ctm, depth, found, walking, page_area_pt):
    """Run one content stream, tracking the CTM, and record every image it draws."""
    import pikepdf

    try:
        instructions = pikepdf.parse_content_stream(container)
    except Exception:                               # noqa: BLE001 — an unparseable stream draws nothing
        return
    stack = []
    current = ctm
    for operands, operator in instructions:
        name = str(operator)
        if name == "q":
            stack.append(current)
        elif name == "Q":
            current = stack.pop() if stack else ctm
        elif name == "cm" and len(operands) == 6:
            try:
                # `cm` PRE-multiplies: the new matrix applies first, then whatever was already in
                # force. Getting this backwards scales a nested placement by the wrong factor.
                current = pikepdf.Matrix(*[float(value) for value in operands]) @ current
            except (TypeError, ValueError):
                continue
        elif name == "INLINE IMAGE" and operands:
            placement = _inline_placement(operands[0], current, page_area_pt)
            if placement:
                found.append(placement)
        elif name == "Do" and operands:
            xobject = _lookup(resources, "/XObject", operands[0])
            if xobject is None:
                continue
            try:
                subtype = str(xobject.get("/Subtype"))
            except Exception:                       # noqa: BLE001
                continue
            if subtype == "/Image":
                placement = _placement(xobject, current, page_area_pt)
                if placement:
                    found.append(placement)
            elif subtype == "/Form" and depth < MAX_XOBJECT_DEPTH:
                _enter_form(xobject, resources, current, depth, found, walking, page_area_pt)


def _enter_form(form, outer_resources, ctm, depth, found, walking, page_area_pt):
    """Walk into a form XObject with its own matrix and resources composed in.

    `walking` is the recursion STACK, not a seen-set: the same form legitimately appears many times
    at different transforms, and treating a repeat as a cycle would lose every placement after the
    first.
    """
    import pikepdf

    key = _identity(form)
    if key in walking:
        return
    inner = ctm
    matrix = form.get("/Matrix")
    if matrix is not None:
        try:
            inner = pikepdf.Matrix(*[float(value) for value in matrix]) @ ctm
        except (TypeError, ValueError):
            inner = ctm
    walking.add(key)
    try:
        _walk_placements(form, form.get("/Resources") or outer_resources, inner,
                         depth + 1, found, walking, page_area_pt)
    finally:
        walking.discard(key)


def _lookup(resources, group, name):
    """One named resource, or None when the file refers to something it never defined."""
    try:
        entry = resources.get(group)
        return entry.get(str(name)) if entry is not None else None
    except Exception:                               # noqa: BLE001
        return None


def _placement(image, ctm, page_area_pt):
    try:
        return _describe_placement(int(image.get("/Width")), int(image.get("/Height")),
                                   ctm, page_area_pt)
    except Exception:                               # noqa: BLE001 — an image without a size is not one
        return None


def _inline_placement(inline_image, ctm, page_area_pt):
    """An inline image (`BI … ID … EI`) placed the same way an XObject is.

    Worth reading: they are usually small, but they are also invisible to some other tools, and a
    verdict that silently skips them disagrees with the reference implementation on real files.
    """
    try:
        return _describe_placement(int(inline_image.width), int(inline_image.height),
                                   ctm, page_area_pt)
    except Exception:                               # noqa: BLE001
        return None


def _describe_placement(pixels_wide, pixels_high, ctm, page_area_pt):
    width_pt = math.hypot(ctm.a, ctm.b)
    height_pt = math.hypot(ctm.c, ctm.d)
    if min(pixels_wide, pixels_high, width_pt, height_pt) <= 0:
        return None
    # Where the unit square LANDS: its four transformed corners. The bbox is axis-aligned — which
    # is wrong for measuring resolution (see the rotated-banner trap above) but exactly right for
    # drawing a marker, since markers are axis-aligned anyway. Kept in PDF points, bottom-left
    # frame; placed_images converts to top-left millimetres with the page box in hand.
    corners = [(ctm.e, ctm.f),
               (ctm.a + ctm.e, ctm.b + ctm.f),
               (ctm.c + ctm.e, ctm.d + ctm.f),
               (ctm.a + ctm.c + ctm.e, ctm.b + ctm.d + ctm.f)]
    bbox_pt = (min(x for x, _y in corners), min(y for _x, y in corners),
               max(x for x, _y in corners), max(y for _x, y in corners))
    return {"px": (pixels_wide, pixels_high),
            "_bbox_pt": bbox_pt,
            "placed_mm": (round(width_pt * 25.4 / 72, 1), round(height_pt * 25.4 / 72, 1)),
            # The WORSE of the two axes: a placement stretched on one side prints as badly as its
            # weakest direction.
            "dpi": round(min(pixels_wide / (width_pt / 72), pixels_high / (height_pt / 72))),
            "area_fraction": (width_pt * height_pt / page_area_pt) if page_area_pt else 0.0}


def significant_placements(placements):
    """The placements big enough to judge the artwork by — the same cut min_significant_dpi uses."""
    significant = [p for p in placements if p["area_fraction"] >= SIGNIFICANT_AREA_FRACTION]
    return significant or placements


def min_significant_dpi(placements):
    """Lowest resolution among the placements big enough to matter, or None when there are none.

    A 36×27 px icon at 72 DPI must not condemn a banner whose artwork is 200 DPI. A strict minimum
    over every placement did exactly that in the in-house engine, and disagreed with an independent
    tool on about 30 % of real files.
    """
    return min((p["dpi"] for p in significant_placements(placements)), default=None)


def technical_spots(spot_names):
    """The colorants that are machine instructions rather than inks."""
    return [name for name in spot_names
            if any(marker in name.lower() for marker in TECHNICAL_SPOT_MARKERS)]


def process_spots(spot_names):
    """The colorants that are just the process inks under their own names."""
    return [name for name in spot_names if name.strip().lower() in PROCESS_COLORANTS]


def ink_spots(spot_names):
    """The colorants that really are EXTRA inks — the ones worth converting to CMYK."""
    ignored = set(technical_spots(spot_names)) | set(process_spots(spot_names))
    return [name for name in spot_names if name not in ignored]


def cut_spots(spot_names):
    """The colorants that mark the cutting path. Crease and Drill are technical but are not cuts."""
    return [name for name in spot_names
            if any(marker in name.lower() for marker in CUT_SPOT_MARKERS)]


def office_producer(producer, creator):
    """The office application that made this file, or None."""
    origin = f"{producer or ''} {creator or ''}".lower()
    return next((name for name in OFFICE_PRODUCERS if name in origin), None)


def rgb_colour_spaces(colour_spaces):
    """Every declared space that is RGB, however it was declared."""
    return [space for space in colour_spaces if "RGB" in space]


def _text(value):
    return str(value) if value is not None else ""


def _identity(obj):
    """A stable key for an indirect object, so a self-referential form is walked once."""
    try:
        return (obj.objgen[0], obj.objgen[1])
    except Exception:                               # noqa: BLE001 — a direct object is its own id
        return id(obj)


def _stream(data):
    import io

    return io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
