"""Read a returned file's stamp — which template was this designed on?

This is the half that makes the loop honest. The in-house tool this comes from GUESSES the template
from page size, filename and inner strokes, and that guess has been wrong in production: it once
picked the wrong one of three nested outlines and produced a cut 12 mm off, and a whole word-overlap
matcher exists because filename matching kept missing. Here the template was ISSUED by us, so the
answer is looked up rather than inferred.

Designers do flatten and re-export, and some of them will lose the stamp. That is expected, not a
defect — `identify` says so plainly and the caller falls back to measuring geometry, rather than
pretending it knows.
"""
import json

from .generate import STAMP_KEY, STAMP_VERSION


class Identification(dict):
    """A stamp reading. Truthy when a template was recognised."""

    def __bool__(self):
        return bool(self.get("recognised"))


def _unrecognised(reason):
    return Identification(recognised=False, reason=reason, page=None, stamp=None)


def read_stamp(pdf_bytes, page_index=0):
    """The stamp on one page of a returned file, or an unrecognised result explaining why."""
    import pikepdf

    try:
        with pikepdf.open(_stream(pdf_bytes)) as pdf:
            if page_index >= len(pdf.pages):
                return _unrecognised(f"The file has {len(pdf.pages)} page(s); page "
                                     f"{page_index + 1} was asked for.")
            raw = pdf.pages[page_index].obj.get(pikepdf.Name(STAMP_KEY))
            if raw is None:
                return _unrecognised("No template stamp — this file was not made from one of our "
                                     "templates, or it was flattened on export.")
            stamp = json.loads(str(raw))
    except json.JSONDecodeError:
        return _unrecognised("The template stamp is damaged and cannot be read.")
    except Exception as error:                      # noqa: BLE001 — a bad upload is an ANSWER here
        return _unrecognised(f"The file could not be opened as a PDF ({type(error).__name__}).")

    if stamp.get("v") != STAMP_VERSION:
        return _unrecognised(f"The stamp is version {stamp.get('v')}, this build reads "
                             f"version {STAMP_VERSION}.")
    return Identification(recognised=True, reason="", page=page_index, stamp=stamp)


def stamped_geometry(stamp):
    """Rebuild the boxes the template was drawn with, in full-size millimetres.

    Deliberately recomputed from the stamped netto/bleed/safe rather than stored as three rectangles:
    one source of truth means a returned file cannot claim geometry that contradicts its own numbers.
    """
    width, height = (float(v) for v in stamp["netto_mm"])
    bleed, safe = float(stamp["bleed_mm"]), float(stamp["safe_mm"])
    # A template built from a SHAPED production file states its page outright, because that page is
    # not netto plus bleed on each side — a flag's sheet is bigger than its cut in ways no arithmetic
    # recovers. A checker that assumed otherwise would fail every correct return of such a template.
    declared_page = stamp.get("page_mm")
    brutto = ((float(declared_page[0]), float(declared_page[1])) if declared_page
              else (width + 2 * bleed, height + 2 * bleed))
    return {
        "netto_mm": (width, height),
        "brutto_mm": brutto,
        "safe_mm_box": (width - 2 * safe, height - 2 * safe),
        "bleed_mm": bleed,
        "safe_mm": safe,
        # What finishing eats per side, and the resulting total inset. Present only when the shop
        # set them. The measurement layer still works from the smallest — see measure.measure.
        "sewn_sides_mm": stamp.get("sewn_sides_mm"),
        "safe_sides_mm": stamp.get("safe_total_sides_mm"),
        "scale": int(stamp.get("scale", 1)),
        # Page furniture, not artwork. The template page is brutto/scale PLUS this, so the checker
        # has to know about it — otherwise a perfectly correct return fails its own size rule.
        "strip_mm": float(stamp.get("strip_mm", 0.0) or 0.0),
        "material_id": stamp.get("material"),
        "label": stamp.get("label", ""),
        # Present only on templates generated from an imported production file.
        "template": stamp.get("template"),
    }


def printed_token(pdf_bytes, page_index=0):
    """The drawn identity (`prepress-open:<token>` in the bleed corner), for files REBUILT by a
    design app: an Illustrator/Affinity/Corel export discards the page-dictionary stamp, but the
    template's drawn text survives as long as its layer was kept. Returns the token or None —
    never raises, because an unreadable file simply has no printed identity."""
    import io
    import re

    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))
        try:
            if page_index >= len(document):
                return None
            text = document[page_index].get_textpage().get_text_bounded()
        finally:
            document.close()
    except Exception:                               # noqa: BLE001 — no text layer = no token
        return None
    match = re.search(r"prepress-open:([A-Za-z0-9_-]{4,32})", text or "")
    return match.group(1) if match else None


def page_size_mm(pdf_bytes, page_index=0):
    """The actual page size of a returned file, for comparing against what the stamp expects."""
    import pikepdf

    with pikepdf.open(_stream(pdf_bytes)) as pdf:
        box = [float(v) for v in pdf.pages[page_index].mediabox]
        return ((box[2] - box[0]) * 25.4 / 72, (box[3] - box[1]) * 25.4 / 72)


def declared_boxes_mm(pdf_bytes, page_index=0):
    """Every page box the file DECLARES, in millimetres: media, crop, bleed, trim, art.

    Worth reading rather than inferring. A designer who set their document up properly declares a
    TrimBox, and that is a stronger statement of intent than the page size alone — page size says
    "this is how big the sheet is", TrimBox says "this is where I expect you to cut". Files that
    declare one and disagree with the template are a different problem from files that were simply
    resized, and the two deserve different messages.

    Only present boxes are returned; roughly one real file in six declares any (measured on the
    in-house corpus), so absence is normal and must not read as an error.
    """
    import pikepdf

    boxes = {}
    with pikepdf.open(_stream(pdf_bytes)) as pdf:
        page = pdf.pages[page_index]
        for attribute in ("mediabox", "cropbox", "bleedbox", "trimbox", "artbox"):
            raw = page.obj.get(pikepdf.Name("/" + attribute[:-3].title() + "Box"))
            if raw is None:
                continue
            try:
                values = [float(v) for v in raw]
            except (TypeError, ValueError):
                continue
            boxes[attribute] = ((values[2] - values[0]) * 25.4 / 72,
                                (values[3] - values[1]) * 25.4 / 72)
    return boxes


def _stream(pdf_bytes):
    import io

    return io.BytesIO(pdf_bytes) if isinstance(pdf_bytes, (bytes, bytearray)) else pdf_bytes
