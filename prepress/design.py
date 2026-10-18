# -*- coding: utf-8 -*-
"""The design editor's server side (ROADMAP „Step 3: the design editor"): the sheet as a picture, the design back as a page.

The editor (static/designer.js) draws on the customer sheet rasterised at a fixed long edge, so its
pixel grid is the sheet's; what comes back is that grid painted with the customer's pictures and texts,
and this module makes it the file the customer would otherwise have exported from Illustrator: one
page of the template's size, the artwork across the whole page, the same stamp the sheet carries.
Everything after that is the existing loop: the check reads the stamp, measures, reports.
"""
import io

from . import from_template, shape

DEFAULT_NOTE = "projekt z edytora online"


class DesignError(ValueError):
    """The design cannot be made into a page: the reason is for the customer."""


def sheet_png(template, long_edge_px):
    """The customer sheet of `template` as a PNG, its longer side `long_edge_px`: the editor's background
    and the pixel grid every design on it is drawn in. Rendered as the demo artwork is (shape.render_preview,
    pypdfium2); raster.py is for raster uploads, not PDFs."""
    return shape.render_preview(from_template.build_pdf(template), 0, max_pixels=long_edge_px)


def compose_pdf(template, artwork_png, note=DEFAULT_NOTE):
    """The design as the filled template page: a one-page PDF of `template["page_mm"]` with the artwork
    raster drawn edge to edge, stamped like the sheet so `identify.read_stamp` names the template."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    try:
        page_w, page_h = (float(v) for v in template["page_mm"])
    except (KeyError, TypeError, ValueError) as error:
        raise DesignError("The template has no page size to draw the design on.") from error
    if page_w <= 0 or page_h <= 0:
        raise DesignError("The template's page size is empty.")
    try:
        image = ImageReader(io.BytesIO(artwork_png))
        image.getSize()
    except Exception as error:                      # noqa: BLE001 — a bad upload is an ANSWER here
        raise DesignError("The design's picture could not be read; save it again.") from error

    width_pt, height_pt = page_w * from_template.PT_PER_MM, page_h * from_template.PT_PER_MM
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width_pt, height_pt))
    pdf.setTitle(template.get("name") or "Projekt")
    pdf.setCreator("prepress-open")
    pdf.drawImage(image, 0, 0, width=width_pt, height=height_pt)
    pdf.showPage()
    pdf.save()
    return from_template._stamp(buffer.getvalue(), template, [note])
