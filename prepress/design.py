# -*- coding: utf-8 -*-
"""The design editor's server side (ROADMAP „Step 3: the design editor"): the sheet as a picture, the design back as a page.

The editor (static/designer.js) draws on the customer sheet rasterised at a fixed long edge, so its
pixel grid is the sheet's; what comes back is that grid painted with the customer's pictures and texts,
and this module makes it the file the customer would otherwise have exported from Illustrator: one
page of the template's size, the artwork across the whole page, the same stamp the sheet carries.
Everything after that is the existing loop: the check reads the stamp, measures, reports.
"""
import io
import math

from . import from_template, shape

DEFAULT_NOTE = "projekt z edytora online"


class DesignError(ValueError):
    """The design cannot be made into a page: the reason is for the customer."""


def sheet_png(template, long_edge_px):
    """The customer sheet of `template` as a PNG, its longer side `long_edge_px`: the editor's background
    and the pixel grid every design on it is drawn in. Rendered as the demo artwork is (shape.render_preview,
    pypdfium2); raster.py is for raster uploads, not PDFs."""
    return shape.render_preview(from_template.build_pdf(template), 0, max_pixels=long_edge_px)


def compose_pdf(template, artwork_png, note=DEFAULT_NOTE, elements=None, rasters=None):
    """The design as the filled template page: a one-page PDF of `template["page_mm"]`, stamped like the
    sheet so `identify.read_stamp` names the template.

    `artwork_png` is the editor's canvas: the pixel grid the elements are placed in, and the picture
    drawn edge to edge when the page cannot be composed from the elements. With `elements` (the
    editor's list, in drawing order) and a raster for EVERY one of them (`rasters`: id -> PNG bytes,
    designer.js `elementRasters`), each is placed at its own pixels instead — a 2048 px canvas is
    22 dpi on a 2.4 m flag, the customer's logo file and a text rasterised for its printed size are
    not. One element without a raster means the canvas is drawn whole: a half-composed page would
    silently drop what the customer placed."""
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
        grid_w, grid_h = image.getSize()
    except Exception as error:                      # noqa: BLE001 — a bad upload is an ANSWER here
        raise DesignError("The design's picture could not be read; save it again.") from error

    width_pt, height_pt = page_w * from_template.PT_PER_MM, page_h * from_template.PT_PER_MM
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width_pt, height_pt))
    pdf.setTitle(template.get("name") or "Projekt")
    pdf.setCreator("prepress-open")
    placed = _placements(elements, rasters)
    if placed:
        pt_per_px = width_pt / grid_w
        # No white ground drawn: a page-sized filled path reads to the check as the template's own cut
        # outline (a sheet template's cut IS the page), and the design would be judged a bare template.
        for element, raster in placed:
            _draw_element(pdf, element, raster, pt_per_px, height_pt)
    else:
        pdf.drawImage(image, 0, 0, width=width_pt, height=height_pt)
    pdf.showPage()
    pdf.save()
    return from_template._stamp(buffer.getvalue(), template, [note])


def _placements(elements, rasters):
    """[(element, ImageReader), …] in drawing order when every element has a usable raster; else []."""
    from reportlab.lib.utils import ImageReader

    if not elements or not rasters:
        return []
    placed = []
    for element in elements:
        if not isinstance(element, dict):
            return []
        try:
            geometry = [float(element[key]) for key in ("x", "y", "scale", "rotation", "width", "height")]
            raster = ImageReader(io.BytesIO(rasters[element["id"]]))
            raster.getSize()
        except (KeyError, TypeError, ValueError):
            return []
        except Exception:                           # noqa: BLE001 — an unreadable raster: the canvas instead
            return []
        if not all(math.isfinite(value) for value in geometry) or geometry[4] <= 0 or geometry[5] <= 0:
            return []
        placed.append((element, raster))
    return placed


def _draw_element(pdf, element, raster, pt_per_px, height_pt):
    """One element's raster on the page: its box (`width` × `height` at `scale`, centred on `x`, `y` in the
    canvas's pixels, turned by `rotation` radians — designer-model.js) in PDF points, y up."""
    box_w = float(element["width"]) * float(element["scale"]) * pt_per_px
    box_h = float(element["height"]) * float(element["scale"]) * pt_per_px
    pdf.saveState()
    pdf.translate(float(element["x"]) * pt_per_px, height_pt - float(element["y"]) * pt_per_px)
    pdf.rotate(-math.degrees(float(element["rotation"])))  # the canvas turns clockwise with y down; PDF counter-clockwise
    pdf.drawImage(raster, -box_w / 2, -box_h / 2, width=box_w, height=box_h, mask="auto")  # a text's transparent ground stays clear
    pdf.restoreState()
