# -*- coding: utf-8 -*-
"""Generate the bundled demo artwork the 3D scene wears when nobody uploaded anything.

A brandless, deliberately flag-shaped composition (1:3 portrait, the proportion most of the
stored templates share), all vector, a few kilobytes — so it can live in the repository and every
install has a decent-looking hero without pointing PREPRESS_DEMO_ARTWORK at somebody's real job.

Run: python scripts/make_demo_artwork.py   → prepress/static/demo-artwork.pdf
"""
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

WIDTH, HEIGHT = 1000 * mm, 3000 * mm
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "prepress", "static", "demo-artwork.pdf")

INK = HexColor("#10344C")       # deep navy ground
SKY = HexColor("#2D9CDB")       # main diagonal
MIST = HexColor("#7FC8EE")      # soft counter-band
SUN = HexColor("#F2C94C")       # one warm accent
PAPER = HexColor("#F5F7FA")


def polygon(pdf, points, colour):
    pdf.setFillColor(colour)
    path = pdf.beginPath()
    path.moveTo(*points[0])
    for x, y in points[1:]:
        path.lineTo(x, y)
    path.close()
    pdf.drawPath(path, fill=1, stroke=0)


pdf = canvas.Canvas(OUT, pagesize=(WIDTH, HEIGHT))
pdf.setTitle("prepress-open demo artwork")

pdf.setFillColor(INK)
pdf.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)

# Two broad diagonals climbing the flag, the mist one cutting back across.
polygon(pdf, [(0, 0), (WIDTH, 0), (WIDTH, HEIGHT * 0.42), (0, HEIGHT * 0.18)], SKY)
polygon(pdf, [(0, HEIGHT * 0.16), (WIDTH, HEIGHT * 0.40), (WIDTH, HEIGHT * 0.46),
              (0, HEIGHT * 0.22)], PAPER)
polygon(pdf, [(0, HEIGHT * 0.55), (WIDTH, HEIGHT * 0.78), (WIDTH, HEIGHT * 0.86),
              (0, HEIGHT * 0.63)], MIST)

# The sun disc in the calm upper field.
pdf.setFillColor(SUN)
pdf.circle(WIDTH * 0.62, HEIGHT * 0.90, WIDTH * 0.16, fill=1, stroke=0)
pdf.setFillColor(INK)
pdf.circle(WIDTH * 0.62, HEIGHT * 0.90, WIDTH * 0.105, fill=1, stroke=0)

# The vertical wordmark, reading bottom-up like every flag of this proportion.
pdf.saveState()
pdf.translate(WIDTH * 0.30, HEIGHT * 0.07)
pdf.rotate(90)
pdf.setFillColor(PAPER)
pdf.setFont("Helvetica-Bold", WIDTH * 0.13)
pdf.drawString(0, 0, "TWOJA GRAFIKA")
pdf.setFillColor(MIST)
pdf.setFont("Helvetica", WIDTH * 0.045)
pdf.drawString(0, -WIDTH * 0.07, "podgląd przykładowy — prepress-open")
pdf.restoreState()

pdf.showPage()
pdf.save()
print("written:", OUT, os.path.getsize(OUT), "bytes")
