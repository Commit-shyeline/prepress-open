"""The template generator: a queue of items → one multipage PDF the customer designs on.

One page per item, sized to that item's brutto box (divided by its scale), carrying three boxes:

    BRUTTO  the page edge itself — artwork must reach it
    NETTO   the trim line
    SAFE    keep type and logos inside this

Each page is STAMPED with the item it was generated from. That stamp is the point: when the file comes
back, `identify` reads it instead of guessing which template a file belongs to from its size and
filename — the guess that, in the in-house tool this comes from, once produced a cut outline 12 mm out.

Drawing is reportlab and stamping is pikepdf, both permissively licensed, so nothing here pulls in an
AGPL renderer.
"""
import io
import json

from . import item as item_module
from . import messages, specblock

PT_PER_MM = 72.0 / 25.4

# Guide colours, chosen to survive being printed by mistake without being confused for artwork, and to
# stay distinguishable in greyscale: brutto mid grey, netto magenta (the trim convention), safe green.
BRUTTO_RGB = (0.55, 0.55, 0.58)
NETTO_RGB = (0.85, 0.10, 0.45)
SAFE_RGB = (0.18, 0.62, 0.32)

GUIDE_WIDTH_PT = 0.75

STAMP_KEY = "/PrepressTemplate"
STAMP_VERSION = 1


def build_pdf(items, title="Szablon do projektowania"):
    """Queue → PDF bytes. `items` are dicts from `item.resolve`."""
    if not items:
        raise item_module.ItemError(messages.notice("empty_queue"))
    drawn = _draw_pages(items, title)
    return _stamp_pages(drawn, items)


def _draw_pages(items, title):
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(100, 100))   # real size is set per page below
    pdf.setTitle(title)
    pdf.setCreator("prepress-open")
    for entry in items:
        page_width_mm, page_height_mm = item_module.page_size_mm(entry)
        pdf.setPageSize((page_width_mm * PT_PER_MM, page_height_mm * PT_PER_MM))
        _draw_one(pdf, entry, page_width_mm, page_height_mm)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _draw_one(pdf, entry, page_width_mm, page_height_mm):
    """Three concentric boxes plus the spec block, in the page's own (scaled) millimetres.

    When the spec sits in a strip below, the artwork area is the page MINUS that strip, and every box
    is drawn offset upward by it — so the brutto box is still exactly the brutto box.
    """
    scale = entry["scale"]
    bleed = entry["bleed_mm"] / scale
    safe = entry["safe_mm"] / scale
    strip_mm = entry.get("spec_strip_mm", 0.0)
    art_height_mm = page_height_mm - strip_mm

    def rect(inset_mm, colour, dashed=False):
        pdf.setStrokeColorRGB(*colour)
        pdf.setLineWidth(GUIDE_WIDTH_PT)
        pdf.setDash((3, 3) if dashed else (), 0)
        pdf.rect(inset_mm * PT_PER_MM, (strip_mm + inset_mm) * PT_PER_MM,
                 (page_width_mm - 2 * inset_mm) * PT_PER_MM,
                 (art_height_mm - 2 * inset_mm) * PT_PER_MM, stroke=1, fill=0)

    # Brutto is the artwork edge; draw it a hair inside so the stroke is not clipped in half.
    rect(GUIDE_WIDTH_PT / PT_PER_MM / 2, BRUTTO_RGB)
    rect(bleed, NETTO_RGB, dashed=True)
    rect(bleed + safe, SAFE_RGB)
    pdf.setDash((), 0)

    specblock.draw(pdf, entry, page_width_mm, page_height_mm, item_module.describe)


def stamp_payload(entry):
    """What gets written into the page — enough for `identify` to rebuild the geometry."""
    return {
        "v": STAMP_VERSION,
        "material": entry["material_id"],
        "netto_mm": [round(v, 2) for v in entry["netto_mm"]],
        "bleed_mm": round(entry["bleed_mm"], 2),
        "safe_mm": round(entry["safe_mm"], 2),
        "scale": entry["scale"],
        "label": entry["label"],
        "strip_mm": round(entry.get("spec_strip_mm", 0.0), 2),
    }


def _stamp_pages(pdf_bytes, items):
    """Write the per-page stamp with pikepdf, losslessly.

    Page level rather than document level on purpose: a queue produces one PDF holding several
    different templates, so a single document-wide stamp could not say which page is which.
    """
    import pikepdf

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        for page, entry in zip(pdf.pages, items):
            page.obj[pikepdf.Name(STAMP_KEY)] = pikepdf.String(
                json.dumps(stamp_payload(entry), ensure_ascii=False))
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = "prepress-open template"
            meta["pdf:Producer"] = "prepress-open"
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()
