"""Phase 0 of the visualizer plan: measurements return POSITIONS, not only verdicts.

A 3D model (and the flat overlay before it) can only paint a finding somewhere — so intrusion
comes back as rectangles, placements carry the rectangle they were drawn into, and the scalar
verdict is DERIVED from the regions so there is exactly one detector.

Run: python3.13 -m pytest tests -q
"""
import io
import os
import sys

import pytest
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import measure, rules, structure  # noqa: E402

PT = 72 / 25.4


def _page(width_mm=500, height_mm=800):
    buffer = io.BytesIO()
    return buffer, canvas.Canvas(buffer, pagesize=(width_mm * PT, height_mm * PT))


def _checker_image(px=64):
    """A tiny checkerboard — high local contrast, so the detail mask sees it as lettering would."""
    from PIL import Image
    image = Image.new("L", (px, px), 255)
    for y in range(px):
        for x in range(px):
            if (x // 4 + y // 4) % 2 == 0:
                image.putpixel((x, y), 0)
    return image


def _expected(width_mm=500, height_mm=800, bleed=20, safe=30, sides=None):
    out = {"brutto_mm": (width_mm, height_mm), "bleed_mm": bleed, "safe_mm": safe, "scale": 1}
    if sides:
        out["safe_sides_mm"] = sides
    return out


def test_a_placement_reports_where_it_sits():
    """The rectangle is read off the CTM's translation — at 300 dpi-ish scale on a known spot."""
    buffer, pdf = _page()
    # 100 mm square image at (50, 650) from the page's bottom-left = y 50 mm from the TOP.
    pdf.drawImage(ImageReader(_checker_image()), 50 * PT, 650 * PT,
                  width=100 * PT, height=100 * PT)
    pdf.showPage()
    pdf.save()

    placements = structure.placed_images(buffer.getvalue())
    assert len(placements) == 1
    x, y, w, h = placements[0]["rect_mm"]
    assert (x, w, h) == pytest.approx((50.0, 100.0, 100.0), abs=0.5)
    # Top-left frame: 800 mm page, image top at 650+100=750 from the bottom → 50 from the top.
    assert y == pytest.approx(50.0, abs=0.5)


def test_intrusion_comes_back_as_regions_in_the_ring():
    buffer, pdf = _page()
    # A busy block INSIDE the keep-out ring (ring depth = 20 bleed + 30 safe = 50 mm): at x=5..45.
    pdf.drawImage(ImageReader(_checker_image()), 5 * PT, 300 * PT,
                  width=40 * PT, height=200 * PT)
    pdf.showPage()
    pdf.save()

    facts = measure.measure(buffer.getvalue(), _expected())
    regions = facts["safe_intrusion_regions_mm"]
    assert regions, "the block in the ring must produce a region"
    assert facts["safe_intrusion_mm"] > 0
    x, y, w, h = regions[0]
    # Region frame is artwork mm, top-left. The block sits on the LEFT edge.
    assert x < 50 and w <= 60
    # Chunky by design: a single block must not dissolve into confetti.
    assert len(regions) <= 3


def test_clean_centre_produces_no_regions():
    buffer, pdf = _page()
    pdf.drawImage(ImageReader(_checker_image()), 200 * PT, 300 * PT,
                  width=100 * PT, height=100 * PT)
    pdf.showPage()
    pdf.save()
    facts = measure.measure(buffer.getvalue(), _expected())
    assert facts["safe_intrusion_regions_mm"] == []
    assert facts["safe_intrusion_mm"] == 0.0


def test_the_wide_sewn_side_is_measured_at_its_own_depth():
    """The gap deliberately left on 2026-08-25, now closed: a mast tunnel's 110 mm side used to be
    measured at the narrowest side's depth, so artwork inside the tunnel band passed."""
    buffer, pdf = _page()
    # Detail at x 60..100 mm: OUTSIDE a uniform 50 mm ring, INSIDE a 130 mm tunnel-side ring.
    pdf.drawImage(ImageReader(_checker_image()), 60 * PT, 300 * PT,
                  width=40 * PT, height=200 * PT)
    pdf.showPage()
    pdf.save()
    data = buffer.getvalue()

    uniform = measure.measure(data, _expected())
    assert uniform["safe_intrusion_regions_mm"] == []

    tunnel = measure.measure(data, _expected(
        sides={"left": 110, "top": 30, "right": 30, "bottom": 30}))
    assert tunnel["safe_intrusion_regions_mm"], "the tunnel-side band must now be judged"
    assert tunnel["safe_intrusion_mm"] > 0


def test_the_verdict_scalar_is_derived_from_the_regions():
    """One detector: no regions must always mean zero intrusion, and vice versa."""
    buffer, pdf = _page()
    pdf.drawImage(ImageReader(_checker_image()), 5 * PT, 300 * PT,
                  width=40 * PT, height=200 * PT)
    pdf.showPage()
    pdf.save()
    facts = measure.measure(buffer.getvalue(), _expected())
    assert bool(facts["safe_intrusion_regions_mm"]) == (facts["safe_intrusion_mm"] > 0)


def test_the_safe_area_finding_carries_the_regions():
    finding = rules.check_safe_area(
        {"safe_intrusion_mm": 12.0, "safe_intrusion_regions_mm": [[0.0, 300.0, 50.0, 210.0]]},
        _expected())
    assert finding["code"] == "check.safe_area.intrusion"
    assert finding["regions"] == [[0.0, 300.0, 50.0, 210.0]]


def test_the_resolution_finding_points_at_the_low_image():
    facts = {"min_dpi": 40,
             "image_placements": [{"rect_mm": [10, 10, 200, 100], "dpi": 40},
                                  {"rect_mm": [300, 300, 50, 50], "dpi": 300}]}
    finding = rules.check_resolution(facts, _expected(), material={"min_dpi": 100})
    assert finding["code"] == "check.resolution.low"
    assert finding["regions"] == [[10, 10, 200, 100]]


def test_a_green_finding_carries_no_regions_key():
    finding = rules.check_safe_area({"safe_intrusion_mm": 0.0, "safe_intrusion_regions_mm": []},
                                    _expected())
    assert "regions" not in finding
