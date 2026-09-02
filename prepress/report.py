"""The check as a PDF a customer attaches to their order.

One page per report unless the findings run long. Nothing is measured here: the page hands over the
verdict it already shows — the summary, the rows, and the overlay picture it drew — and this file
lays them out. That keeps the report identical to what the customer read, and keeps the customer's
artwork out of the request: the picture is the small preview, never the file.

Polish needs a real TrueType font; reportlab's built-in Helvetica has no ą, ł or ś. The first font
found in `FONT_CANDIDATES` is registered, and a shop can point at its own with PREPRESS_REPORT_FONT.
Nothing found: Helvetica, and the diacritics are stripped rather than printed as boxes.
"""
import io
import os
import unicodedata

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_ENV = "PREPRESS_REPORT_FONT"
BOLD_CANDIDATES = (
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)
FONT_CANDIDATES = (
    os.environ.get(FONT_ENV) or "",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
MARGIN = 42
LEVEL_RGB = {"red": (0.82, 0.12, 0.24), "amber": (0.78, 0.47, 0.0),
             "green": (0.18, 0.62, 0.32), "info": (0.42, 0.44, 0.50)}

_font = {"name": None, "unicode": False, "bold": None}


def bold_font_name():
    """A bold TrueType face to match `_font_name()`, or Helvetica-Bold when none is installed."""
    if _font["bold"]:
        return _font["bold"]
    for path in BOLD_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("ReportSansBold", path))
                _font["bold"] = "ReportSansBold"
                return _font["bold"]
            except Exception:                        # noqa: BLE001 — try the next
                continue
    _font["bold"] = "Helvetica-Bold"
    return _font["bold"]


def _font_name():
    """Register the first usable TrueType font once; fall back to Helvetica."""
    if _font["name"]:
        return _font["name"]
    for path in FONT_CANDIDATES:
        if path and os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("ReportSans", path))
                _font.update(name="ReportSans", unicode=True)
                return _font["name"]
            except Exception:                        # noqa: BLE001 — a broken font file: try the next
                continue
    _font.update(name="Helvetica", unicode=False)
    return _font["name"]


def fold_ascii(value):
    """`łódź` → `lodz`: the diacritics folded away, for a font or a filename that cannot carry them.
    Werkzeug's secure_filename simply DROPS ł, which turned „łódź" into „odz"."""
    folded = unicodedata.normalize("NFKD", str(value or "").replace("ł", "l").replace("Ł", "L"))
    return "".join(ch for ch in folded if ord(ch) < 128)


def _text(value):
    """What the chosen font can show: everything, or ASCII with the diacritics folded away."""
    return str(value or "") if _font["unicode"] else fold_ascii(value)


def _wrap(pdf, text, size, width):
    """Greedy word wrap against the real string width."""
    words, lines, line = _text(text).split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if pdf.stringWidth(candidate, _font_name(), size) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def build_pdf(payload, brand="prepress-open"):
    """`payload` is what the check page holds: filename, subject, size_text, summary,
    checks [{level, title, detail}], and optionally overlay_png (base64) with its legend."""
    import base64

    font = _font_name()
    page_w, page_h = A4
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(_text(f"Raport kontroli — {payload.get('filename') or 'plik'}"))
    pdf.setCreator("prepress-open")
    width = page_w - 2 * MARGIN
    y = page_h - MARGIN

    def line(text, size, bold=False, colour=(0.14, 0.15, 0.18), gap=4):
        nonlocal y
        pdf.setFillColorRGB(*colour)
        pdf.setFont(font, size)
        for row in _wrap(pdf, text, size, width):
            if y < MARGIN + size:
                pdf.showPage()
                y = page_h - MARGIN
                pdf.setFont(font, size)
                pdf.setFillColorRGB(*colour)
            pdf.drawString(MARGIN, y - size, row)
            y -= size * 1.35
        y -= gap

    line(f"{brand} · raport kontroli pliku", 9, colour=(0.42, 0.44, 0.50))
    line(payload.get("filename") or "plik", 15)
    for key in ("subject", "size_text", "checked_at"):
        if payload.get(key):
            line(payload[key], 10, colour=(0.42, 0.44, 0.50), gap=0)
    y -= 8
    line(payload.get("summary") or "", 12, gap=10)

    for check in payload.get("checks") or []:
        level = str(check.get("level") or "info")
        if y < MARGIN + 40:
            pdf.showPage()
            y = page_h - MARGIN
        pdf.setFillColorRGB(*LEVEL_RGB.get(level, LEVEL_RGB["info"]))
        pdf.circle(MARGIN + 4, y - 7, 3.2, stroke=0, fill=1)
        x_text = MARGIN + 14
        pdf.setFillColorRGB(0.14, 0.15, 0.18)
        pdf.setFont(font, 10.5)
        for row in _wrap(pdf, check.get("title") or check.get("code") or "", 10.5, width - 14):
            pdf.drawString(x_text, y - 10.5, row)
            y -= 14
        if check.get("detail"):
            pdf.setFillColorRGB(0.42, 0.44, 0.50)
            pdf.setFont(font, 9)
            for row in _wrap(pdf, check["detail"], 9, width - 14):
                pdf.drawString(x_text, y - 9, row)
                y -= 12
        y -= 6

    overlay = payload.get("overlay_png")
    if overlay:
        try:
            image = ImageReader(io.BytesIO(base64.b64decode(overlay)))
            img_w, img_h = image.getSize()
            box_w, box_h = width, page_h - 2 * MARGIN - 30
            scale = min(box_w / img_w, box_h / img_h)
            draw_w, draw_h = img_w * scale, img_h * scale
            if y - draw_h - 20 < MARGIN:
                pdf.showPage()
                y = page_h - MARGIN
            pdf.drawImage(image, MARGIN + (width - draw_w) / 2, y - draw_h, draw_w, draw_h)
            y -= draw_h + 6
            if payload.get("legend"):
                line(payload["legend"], 8.5, colour=(0.42, 0.44, 0.50))
        except Exception:                            # noqa: BLE001 — the verdict outranks the picture
            pass

    pdf.save()
    return buffer.getvalue()
