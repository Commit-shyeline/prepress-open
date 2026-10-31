"""The bleed check walks the bleed LINE, not the page edge.

A row or column scan of the page read a shaped flag's white corners and a roll-up's white upper half
as artwork hundreds of millimetres short of the bleed (2026-10-31). These pages are
drawn here as rasters, at 2 px/mm, so every distance below is in full-size millimetres.

Run: python3.13 -m pytest tests -q
"""
import io
import os
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import die, from_template, measure, rules  # noqa: E402

PX_PER_MM = 2
PAGE_MM = (500, 800)
NAVY = (0, 30, 80)


def _raster(paint):
    image = Image.new("RGB", (PAGE_MM[0] * PX_PER_MM, PAGE_MM[1] * PX_PER_MM), "white")
    paint(ImageDraw.Draw(image), lambda points: [(x * PX_PER_MM, y * PX_PER_MM) for x, y in points])
    buffer = io.BytesIO()
    image.save(buffer, "PNG", dpi=(PX_PER_MM * 25.4, PX_PER_MM * 25.4))
    return buffer.getvalue()


def _expected(bleed=20, safe=30):
    return {"brutto_mm": PAGE_MM, "bleed_mm": bleed, "safe_mm": safe, "scale": 1}


def _verdict(facts, bleed=20):
    return rules.check_artwork_reaches_bleed(facts, _expected(bleed))


# A sail: straight pole side on the right, a curve down the left — the page's corners stay paper.
SAIL = [(480, 20), (480, 780), (60, 780), (40, 500), (80, 250), (200, 80), (350, 25)]


def test_a_white_background_is_the_designers_call_not_a_shortfall():
    """A logo floating on white: paper from the edge all the way into the safe area."""
    facts = measure.measure(_raster(lambda draw, at: draw.rectangle(at([(150, 300), (350, 500)]), fill=NAVY)),
                            _expected())
    assert max(facts["blank_edges_mm"]) > 100, "the page-edge scan still reads it as short"
    assert facts["bleed_shortfall"]["share"] == 0
    assert _verdict(facts)["level"] == "green"


def test_a_panel_stopping_short_of_the_edge_is_still_red():
    """Ink 15 mm in from the left edge: a white strip after trimming, over half the 20 mm bleed."""
    facts = measure.measure(_raster(lambda draw, at: draw.rectangle(at([(15, 0), (500, 800)]), fill=NAVY)),
                            _expected())
    assert facts["bleed_shortfall"]["missing_mm"] == pytest.approx(15, abs=1)
    finding = _verdict(facts)
    assert finding["level"] == "red"
    assert float(finding["values"]["missing"]) == pytest.approx(15, abs=1)


def test_a_shaped_product_is_walked_along_its_own_outline():
    """The sail filled to its outline passes; walked as a rectangle it was a red shortfall."""
    data = _raster(lambda draw, at: draw.polygon(at(SAIL), fill=NAVY))
    assert _verdict(measure.measure(data, _expected()))["level"] == "red"
    facts = measure.measure(data, _expected(), template_lines={"bleed": [SAIL]})
    assert facts["bleed_shortfall"]["share"] <= rules.BARE_PERIMETER_TOLERANCE
    assert _verdict(facts)["level"] == "green"


def test_artwork_on_another_page_is_located_before_it_is_walked():
    """The same sail drawn 20 mm left and 10 mm up — the graphic team's own die page. Pinned where the
    template draws it, the line reads 20 mm short; located first, it passes and says how far it moved."""
    moved = [(x - 20, y - 10) for x, y in SAIL]
    data = _raster(lambda draw, at: draw.polygon(at(moved), fill=NAVY))
    pinned = measure.measure(data, _expected(), template_lines={"bleed": [SAIL]})
    assert _verdict(pinned)["level"] == "red"
    located = measure.measure(data, _expected(), template_lines={"bleed": [SAIL]}, locate_template_lines=True)
    assert located["bleed_line_offset_mm"] == pytest.approx((-20, -10), abs=1)
    assert _verdict(located)["level"] == "green"


def test_a_short_shape_is_not_located_into_a_pass():
    """Ink stopping 15 mm inside the sail's pole side: moving the line right would bare the curve, so
    the locator leaves it, and the shortfall is still said."""
    trimmed = [(min(x, 465), y) for x, y in SAIL]
    data = _raster(lambda draw, at: draw.polygon(at(trimmed), fill=NAVY))
    facts = measure.measure(data, _expected(), template_lines={"bleed": [SAIL]}, locate_template_lines=True)
    assert _verdict(facts)["level"] in ("amber", "red")


def test_located_offset_keeps_a_line_already_on_the_artwork():
    image = Image.new("RGB", (PAGE_MM[0] * PX_PER_MM, PAGE_MM[1] * PX_PER_MM), "white")
    ImageDraw.Draw(image).polygon([(x * PX_PER_MM, y * PX_PER_MM) for x, y in SAIL], fill=NAVY)
    import numpy
    offset = die.located_offset(numpy.asarray(image), PX_PER_MM, [SAIL], 50, measure.PAPER_MIN_CHANNEL)
    assert offset == pytest.approx((0, 0), abs=0.5)


def _box_outline(kind, left, bottom, right, top):
    """A stored outline (y UP, as imported), drawn as an off-centre box so a reflection shows."""
    return {"type": kind, "closed": True, "start": [left, top], "width_mm": right - left,
            "height_mm": top - bottom, "origin_mm": [left, bottom],
            "segments": [["l", right, top], ["l", right, bottom], ["l", left, bottom], ["l", left, top]]}


def test_the_back_of_a_mirrored_pair_gets_the_reflected_lines():
    """y comes back DOWN from the top; the back is reflected about the page's width; the safe line is
    the one the customer's sheet draws — the cut taken in by the margin."""
    template = {"page_mm": [500, 800], "bleed_mm": 3, "safe_mm": 30,
                "outlines": [_box_outline("bleed", 100, 100, 400, 700), _box_outline("cut", 110, 110, 390, 690)]}

    def x_range(polylines):
        return min(x for x, _ in polylines[0]), max(x for x, _ in polylines[0])

    front = from_template.template_lines_mm(template)
    back = from_template.template_lines_mm(template, mirrored=True)
    assert min(y for _, y in front["bleed"][0]) == pytest.approx(100)
    assert x_range(front["safe"]) == pytest.approx((140, 360), abs=0.1)
    assert x_range(back["bleed"]) == pytest.approx((100, 400))
    assert x_range(back["safe"]) == pytest.approx((140, 360), abs=0.1)
    template["outlines"] = [_box_outline("bleed", 60, 100, 400, 700), _box_outline("cut", 70, 110, 390, 690)]
    assert x_range(from_template.template_lines_mm(template, mirrored=True)["bleed"]) == pytest.approx((100, 440))
    assert from_template.template_lines_mm(dict(template, back_mirrored=False), mirrored=True) == \
        from_template.template_lines_mm(template)
    assert from_template.template_lines_mm(dict(template, kind="sheet")) is None


def test_a_flag_edge_in_the_keep_out_ring_is_not_content():
    """The sail filled to its outline, a logo in the middle: nothing in the ring. A logo crossing the
    safe line on the curved side still is."""
    safe = [(x * 0.8 + 70, y * 0.8 + 80) for x, y in SAIL]
    lines = {"bleed": [SAIL], "safe": [safe]}

    def sail_with_logo(left_mm):
        def paint(draw, at):
            draw.polygon(at(SAIL), fill=NAVY)
            draw.rectangle(at([(left_mm, 400), (left_mm + 60, 460)]), fill="white")
        return _raster(paint)

    clean = measure.measure(sail_with_logo(220), _expected(bleed=3, safe=30), template_lines=lines)
    assert clean["safe_intrusion_mm"] == 0.0
    assert rules.check_artwork_reaches_bleed(clean, _expected(bleed=3, safe=30))["level"] == "green"
    crossing = measure.measure(sail_with_logo(75), _expected(bleed=3, safe=30), template_lines=lines)
    assert crossing["safe_intrusion_mm"] > 0
    assert crossing["safe_intrusion_regions_mm"]
