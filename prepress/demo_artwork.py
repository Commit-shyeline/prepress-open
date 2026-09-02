"""The demo artwork the hero flag wears, drawn INSIDE the template's own safe area.

The bundled `static/demo-artwork.pdf` holds a flat 19 % margin on a 1000 × 3000 page, and the hero
is a feather flag whose safe area is a curved outline 30 mm inside the cut — a rectangle laid out in
page fractions cannot promise to stay inside a feather, and on the S Play A it did not: the wordmark
sat in the keep-out ring of the very page that offers to catch exactly that (Shyeline, 2026-09-02).

So the composition is built PER TEMPLATE: the safe outline comes from `from_template.derive`, the
largest box that fits inside it from `offset.largest_inscribed_box`, and every element that counts
as detail — the wordmark, the credit, the sun — is laid out inside that box. The full-bleed ground
and the diagonals run off the page on purpose: flat colour is not detail, and a ground that reaches
the edges is what the check engine calls a ground. The page IS the template's page, so the 3D
scene's texture mapping (`wearArtwork`: page fractions) lands every millimetre where it was drawn.

`intrusions_mm` is the same geometry exposed for the test: an empty list is the point of the file.
"""
import io

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

from . import from_template, offset

PT_PER_MM = 72.0 / 25.4

INK = HexColor("#10344C")       # deep navy ground
SKY = HexColor("#2D9CDB")       # main diagonal
MIST = HexColor("#7FC8EE")      # soft counter-band
SUN = HexColor("#F2C94C")       # one warm accent
PAPER = HexColor("#F5F7FA")     # the wordmark, and nothing else
GRASS = HexColor("#2F7D40")

WORDMARK = "TWOJA GRAFIKA"
CREDIT = "podgląd przykładowy — prepress-open"
# Polish needs a TrueType face: reportlab's Helvetica has no ą or ł and drew the credit with boxes.
# The report already finds one (or folds the diacritics away); the demo uses the same face.
from . import report as _report                       # noqa: E402
def _font():
    return _report._font_name()
WORDMARK_FONT, CREDIT_FONT = "Helvetica-Bold", "Helvetica"
# Breathing room inside the inscribed box, as a fraction of its width.
INSET = 0.06


class DemoError(ValueError):
    """The template has no safe area a composition could be laid out in."""


def safe_box_mm(template):
    """The largest rectangle inside the template's safe outline, (x0, y0, x1, y1) in page mm, y UP."""
    drawing, _notes = from_template.derive(template)
    rings = [offset.flatten(entry) for entry in drawing.get("safe") or []]
    box = offset.largest_inscribed_box([ring for ring in rings if len(ring) >= 3]) if rings else None
    if not box or box[2] - box[0] < 50 or box[3] - box[1] < 50:
        raise DemoError("Ten szablon nie ma obszaru bezpiecznego, w którym zmieściłaby się grafika.")
    return box


def layout_mm(template):
    """Every element that counts as detail, placed inside the safe box: {name: (x0, y0, x1, y1)}.

    A tall flag reads bottom-up, so the wordmark climbs the box along its left third and the sun
    sits in the calm upper field; on a wide box the same rules just spread out. Sizes are fractions
    of the box, never of the page, so the box's shape decides the composition.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    x0, y0, x1, y1 = safe_box_mm(template)
    inset = (x1 - x0) * INSET
    bx0, by0, bx1, by1 = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    width, height = bx1 - bx0, by1 - by0
    tall = height > width * 1.5

    # The wordmark: rotated on a tall box, horizontal on a wide one. Its size is whatever makes it
    # fit the run available, capped so a huge template does not get a huge word.
    run = (height if tall else width) * 0.8
    wordmark_size = min(width * 0.28 if tall else height * 0.35,
                        run / (stringWidth(WORDMARK, _font(), 1.0) or 1.0))
    credit_size = wordmark_size * 0.4
    word_len = stringWidth(WORDMARK, _font(), wordmark_size)
    credit_len = stringWidth(CREDIT, _font(), credit_size)
    ascent, descent = 0.72, 0.21                      # Helvetica, as fractions of the size
    if tall:
        # rotate(90): local +y (ascenders) → page −x; the advance climbs the page.
        baseline_x = bx0 + wordmark_size * ascent + width * 0.08
        start_y = by0 + height * 0.05
        wordmark = (baseline_x - wordmark_size * ascent, start_y,
                    baseline_x + wordmark_size * descent, start_y + word_len)
        credit_x = baseline_x + wordmark_size * 0.6
        credit = (credit_x - credit_size * ascent, start_y,
                  credit_x + credit_size * descent, start_y + credit_len)
        sun_r = min(width * 0.30, height * 0.12)
        sun_c = (bx1 - sun_r - width * 0.05, by1 - sun_r - height * 0.04)
    else:
        baseline_y = by0 + height * 0.18
        start_x = bx0 + width * 0.05
        wordmark = (start_x, baseline_y - wordmark_size * descent,
                    start_x + word_len, baseline_y + wordmark_size * ascent)
        credit_y = baseline_y - wordmark_size * 0.6
        credit = (start_x, credit_y - credit_size * descent,
                  start_x + credit_len, credit_y + credit_size * ascent)
        sun_r = min(height * 0.30, width * 0.12)
        sun_c = (bx1 - sun_r - width * 0.04, by1 - sun_r - height * 0.06)
    sun = (sun_c[0] - sun_r, sun_c[1] - sun_r, sun_c[0] + sun_r, sun_c[1] + sun_r)
    return {"wordmark": wordmark, "credit": credit, "sun": sun,
            "_params": {"tall": tall, "wordmark_size": wordmark_size, "credit_size": credit_size,
                        "sun_c": sun_c, "sun_r": sun_r, "box": (bx0, by0, bx1, by1)}}


def elements_mm(template):
    """Only the detail elements of the layout, without the drawing parameters."""
    return {name: box for name, box in layout_mm(template).items() if not name.startswith("_")}


def intrusions_mm(template):
    """[(element, mm outside the safe BOX)], empty when every element sits inside."""
    x0, y0, x1, y1 = safe_box_mm(template)
    out = []
    for name, (ex0, ey0, ex1, ey1) in elements_mm(template).items():
        over = max(x0 - ex0, y0 - ey0, ex1 - x1, ey1 - y1)
        if over > 0.01:
            out.append((name, round(over, 2)))
    return out


def build_pdf(template):
    """The demo composition on the template's own page, as PDF bytes."""
    page_w, page_h = (float(v) for v in template["page_mm"])
    layout = layout_mm(template)
    p = layout["_params"]
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_w * PT_PER_MM, page_h * PT_PER_MM))
    pdf.setTitle("Twoja grafika — podgląd przykładowy")
    pdf.setCreator("prepress-open")
    pdf.scale(PT_PER_MM, PT_PER_MM)                 # draw in millimetres from here on

    def polygon(points, colour):
        pdf.setFillColor(colour)
        path = pdf.beginPath()
        path.moveTo(*points[0])
        for x, y in points[1:]:
            path.lineTo(x, y)
        path.close()
        pdf.drawPath(path, fill=1, stroke=0)

    pdf.setFillColor(INK)
    pdf.rect(0, 0, page_w, page_h, fill=1, stroke=0)
    # Full bleed by intent: flat colour running off the page is a ground, not detail.
    polygon([(0, 0), (page_w, 0), (page_w, page_h * 0.42), (0, page_h * 0.18)], SKY)
    polygon([(0, page_h * 0.16), (page_w, page_h * 0.40), (page_w, page_h * 0.46),
             (0, page_h * 0.22)], GRASS)
    polygon([(0, page_h * 0.55), (page_w, page_h * 0.78), (page_w, page_h * 0.86),
             (0, page_h * 0.63)], MIST)

    pdf.setFillColor(SUN)
    pdf.circle(p["sun_c"][0], p["sun_c"][1], p["sun_r"], fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.circle(p["sun_c"][0], p["sun_c"][1], p["sun_r"] * 0.65, fill=1, stroke=0)

    pdf.saveState()
    if p["tall"]:
        wx0, wy0, wx1, _wy1 = layout["wordmark"]
        baseline_x = wx0 + p["wordmark_size"] * 0.72
        pdf.translate(baseline_x, wy0)
        pdf.rotate(90)
        pdf.setFillColor(PAPER)
        pdf.setFont(_font(), p["wordmark_size"])
        pdf.drawString(0, 0, _report._text(WORDMARK))
        pdf.setFillColor(MIST)
        pdf.setFont(_font(), p["credit_size"])
        pdf.drawString(0, -(p["wordmark_size"] * 0.6), _report._text(CREDIT))
    else:
        wx0, wy0, _wx1, _wy1 = layout["wordmark"]
        baseline_y = wy0 + p["wordmark_size"] * 0.21
        pdf.setFillColor(PAPER)
        pdf.setFont(_font(), p["wordmark_size"])
        pdf.drawString(wx0, baseline_y, _report._text(WORDMARK))
        pdf.setFillColor(MIST)
        pdf.setFont(_font(), p["credit_size"])
        pdf.drawString(wx0, baseline_y - p["wordmark_size"] * 0.6, _report._text(CREDIT))
    pdf.restoreState()
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
