# -*- coding: utf-8 -*-
"""Generate the bundled demo artwork the 3D scene wears when nobody uploaded anything.

A brandless, deliberately flag-shaped composition (1:3 portrait, the proportion most of the
stored templates share), all vector, a few kilobytes — so it can live in the repository and every
install has a decent-looking hero without pointing PREPRESS_DEMO_ARTWORK at somebody's real job.

It also has to OBEY THE RULE THE PAGE IT DECORATES ADVERTISES. This file is the artwork on the
hero of a page that offers to tell customers when their detail strays out of the safe area, and
for a while nothing had ever measured it — the composition was laid out in round page fractions
picked by eye, and the check engine never sees it, because the demo goes straight to the 3D
scene's texture and never through `/api/check`. So the safe box is declared here and the layout is
asserted against it at generation time; `content_extents_mm()` is the same geometry, exposed so a
test can hold the file to it without rendering anything.

Run: python scripts/make_demo_artwork.py   → prepress/static/demo-artwork.pdf
"""
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

WIDTH, HEIGHT = 1000 * mm, 3000 * mm
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "prepress", "static", "demo-artwork.pdf")

# ── The safe area this composition holds itself to ───────────────────────────
# As a FRACTION of each page dimension, not a margin in millimetres, because the 3D scene maps this
# raster across whatever page it is worn on (`wearArtwork` in model3d.html sets the texture's
# repeat and offset from `page_mm`) — so a margin in this file's own millimetres is stretched by the
# ratio between this page and that one, and stops being the margin it claimed to be.
#
# 0.19 is the worst case among the shapes this actually flies on, not a guess. Measured on VENTO S
# page 1, the numbers recorded in lines.role_boxes and specblock: the outline the margin is taken
# from is 633.0 mm wide, the typed margin is 40 mm, so the safe area is 553 mm (specblock.py) inside
# a 890 mm page. That leaves (890 − 553) / 2 = 168.5 mm a side, which is 0.189 of the page — an
# inset five times the 0.04 that "40 mm from the cut line" suggests on a sheet this wide, because a
# feather flag's safe area is bounded by a curved shape sitting inside a wider roll, not by the
# sheet edge. Applied to BOTH axes for the same stretching reason.
SAFE_MARGIN_FRACTION = 0.19
SAFE = (WIDTH * SAFE_MARGIN_FRACTION, HEIGHT * SAFE_MARGIN_FRACTION,
        WIDTH * (1 - SAFE_MARGIN_FRACTION), HEIGHT * (1 - SAFE_MARGIN_FRACTION))

INK = HexColor("#10344C")       # deep navy ground
SKY = HexColor("#2D9CDB")       # main diagonal
MIST = HexColor("#7FC8EE")      # soft counter-band
SUN = HexColor("#F2C94C")       # one warm accent
PAPER = HexColor("#F5F7FA")     # the wordmark, and nothing else now
# The band the wordmark crosses. It was PAPER, and white lettering laid across a near-white band
# simply vanished into it (the shop, 2026-09-01). This is the project's own accent green, the dark
# end of it: white on #2F7D40 measures 4.6:1, which carries display lettering comfortably, where
# the lighter #3EA855 of the page furniture would only manage 2.8:1.
GRASS = HexColor("#2F7D40")

# ── The composition, as numbers rather than as drawing calls ─────────────────
# Split out so the same geometry can be asserted and drawn. The bands are deliberately NOT in here:
# they run off all four edges on purpose, and a full-bleed ground is not an intrusion — which is the
# engine's own rule (rules.check_safe_area measures DETAIL in the keep-out ring, not colour).

WORDMARK = "TWOJA GRAFIKA"
WORDMARK_FONT, WORDMARK_SIZE = "Helvetica-Bold", WIDTH * 0.11
CREDIT = "podgląd przykładowy — prepress-open"
CREDIT_FONT, CREDIT_SIZE = "Helvetica", WIDTH * 0.045
# Where the vertical setting stands and where it starts climbing. It starts at 0.33 of the height
# and not at 0.07: from down there the setting ran up through the SKY diagonal AND the band above
# it, so the lettering crossed three grounds and was unreadable on two of them. From 0.33 it sits
# in the clear navy between the accent band below it and the mist band above — the only stretch of
# this composition that is one colour for the whole length of a thirteen-character word.
WORDMARK_BASELINE_X, WORDMARK_START_Y = WIDTH * 0.30, HEIGHT * 0.33
CREDIT_OFFSET = WIDTH * 0.07          # to the RIGHT of the wordmark's baseline once rotated

# The sun disc. Its CENTRE was at 0.90 of the height, which put its top edge 430 mm above the safe
# area on a disc 320 mm across — the one element of this composition the checker would have called
# an intrusion, had anything ever run the checker over it. 0.755 seats the whole disc inside, in the
# same calm upper field, and keeps it clear of the mist band below.
SUN_CENTRE = (WIDTH * 0.62, HEIGHT * 0.755)
SUN_RADIUS, SUN_HOLE = WIDTH * 0.16, WIDTH * 0.105


def content_extents_mm():
    """Every element that counts as DETAIL, as (name, x0, y0, x1, y1) boxes in millimetres.

    In the page's own coordinates, origin bottom-left, which is what reportlab draws in and what
    the safe box above is stated in. The rotation is resolved here rather than left to the reader:
    `rotate(90)` maps a glyph's local +y to the page's −x, so the lettering's ascenders stand to the
    LEFT of its baseline and its advance climbs the page.
    """
    ascent = pdfmetrics.getAscent(WORDMARK_FONT, WORDMARK_SIZE)
    descent = pdfmetrics.getDescent(WORDMARK_FONT, WORDMARK_SIZE)          # negative
    length = pdfmetrics.stringWidth(WORDMARK, WORDMARK_FONT, WORDMARK_SIZE)
    credit_ascent = pdfmetrics.getAscent(CREDIT_FONT, CREDIT_SIZE)
    credit_descent = pdfmetrics.getDescent(CREDIT_FONT, CREDIT_SIZE)
    credit_length = pdfmetrics.stringWidth(CREDIT, CREDIT_FONT, CREDIT_SIZE)
    credit_baseline_x = WORDMARK_BASELINE_X + CREDIT_OFFSET
    return [
        ("wordmark",
         WORDMARK_BASELINE_X - ascent, WORDMARK_START_Y,
         WORDMARK_BASELINE_X - descent, WORDMARK_START_Y + length),
        ("credit",
         credit_baseline_x - credit_ascent, WORDMARK_START_Y,
         credit_baseline_x - credit_descent, WORDMARK_START_Y + credit_length),
        ("sun",
         SUN_CENTRE[0] - SUN_RADIUS, SUN_CENTRE[1] - SUN_RADIUS,
         SUN_CENTRE[0] + SUN_RADIUS, SUN_CENTRE[1] + SUN_RADIUS),
    ]


def intrusions_mm():
    """The elements that leave the safe box, and by how far. Empty is the point of the file."""
    out = []
    for name, x0, y0, x1, y1 in content_extents_mm():
        over = max(SAFE[0] - x0, SAFE[1] - y0, x1 - SAFE[2], y1 - SAFE[3])
        if over > 0:
            out.append((name, over / mm))
    return out


def polygon(pdf, points, colour):
    pdf.setFillColor(colour)
    path = pdf.beginPath()
    path.moveTo(*points[0])
    for x, y in points[1:]:
        path.lineTo(x, y)
    path.close()
    pdf.drawPath(path, fill=1, stroke=0)


def draw(pdf):
    pdf.setFillColor(INK)
    pdf.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)

    # Two broad diagonals climbing the flag, the mist one cutting back across. Full bleed by
    # intent: a ground that runs off the edges is what the check engine calls a ground.
    polygon(pdf, [(0, 0), (WIDTH, 0), (WIDTH, HEIGHT * 0.42), (0, HEIGHT * 0.18)], SKY)
    polygon(pdf, [(0, HEIGHT * 0.16), (WIDTH, HEIGHT * 0.40), (WIDTH, HEIGHT * 0.46),
                  (0, HEIGHT * 0.22)], GRASS)
    polygon(pdf, [(0, HEIGHT * 0.55), (WIDTH, HEIGHT * 0.78), (WIDTH, HEIGHT * 0.86),
                  (0, HEIGHT * 0.63)], MIST)

    # The sun disc in the calm upper field.
    pdf.setFillColor(SUN)
    pdf.circle(SUN_CENTRE[0], SUN_CENTRE[1], SUN_RADIUS, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.circle(SUN_CENTRE[0], SUN_CENTRE[1], SUN_HOLE, fill=1, stroke=0)

    # The vertical wordmark, reading bottom-up like every flag of this proportion.
    pdf.saveState()
    pdf.translate(WORDMARK_BASELINE_X, WORDMARK_START_Y)
    pdf.rotate(90)
    pdf.setFillColor(PAPER)
    pdf.setFont(WORDMARK_FONT, WORDMARK_SIZE)
    pdf.drawString(0, 0, WORDMARK)
    pdf.setFillColor(MIST)
    pdf.setFont(CREDIT_FONT, CREDIT_SIZE)
    pdf.drawString(0, -CREDIT_OFFSET, CREDIT)
    pdf.restoreState()


def main():
    # Refused rather than written: a demo that breaks the rule the page advertises is worse than no
    # demo, and the whole reason this check exists is that nobody would have noticed otherwise.
    over = intrusions_mm()
    if over:
        raise SystemExit("refusing to write: outside the safe area — "
                         + ", ".join(f"{name} by {far:.1f} mm" for name, far in over))
    pdf = canvas.Canvas(OUT, pagesize=(WIDTH, HEIGHT))
    pdf.setTitle("prepress-open demo artwork")
    draw(pdf)
    pdf.showPage()
    pdf.save()
    print("written:", OUT, os.path.getsize(OUT), "bytes")
    for name, x0, y0, x1, y1 in content_extents_mm():
        print(f"  {name:9s} {x0 / mm:7.1f} {y0 / mm:7.1f} {x1 / mm:7.1f} {y1 / mm:7.1f} mm")
    print(f"  safe box  {SAFE[0] / mm:7.1f} {SAFE[1] / mm:7.1f} "
          f"{SAFE[2] / mm:7.1f} {SAFE[3] / mm:7.1f} mm")


if __name__ == "__main__":
    main()
