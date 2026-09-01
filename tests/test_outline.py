"""The path reader: does it return the geometry that was actually drawn?

Every PDF here is authored with reportlab at sizes chosen in advance, so the right answer is known
before the reader runs. That matters more than usual for this module: it is the thing a cut line will
be taken from, and the two existing sources of the same geometry were measured to be wrong by 12.6 mm
(flattened chords) and 16.8 mm (control-point hull) — see `prepress/outline.py`.

Run: python3.13 -m pytest tests -q
"""
import io
import os
import sys

from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import outline  # noqa: E402

PT = 72 / 25.4


def _page(draw, width_mm=500, height_mm=800):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width_mm * PT, height_mm * PT))
    draw(pdf)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _sizes(entries):
    return [(round(e["width_mm"], 1), round(e["height_mm"], 1)) for e in entries]


def test_a_rectangle_is_read_back_at_its_real_size():
    data = _page(lambda pdf: pdf.rect(50 * PT, 100 * PT, 300 * PT, 400 * PT, stroke=1, fill=0))
    found = outline.candidates(data)
    assert _sizes(found) == [(300.0, 400.0)]
    assert found[0]["origin_mm"] == (50.0, 100.0)
    assert found[0]["painted"] == "stroke"


def test_the_curve_is_measured_not_its_control_points():
    """The finding this module exists for. A cubic from (0,0) to (400,0) with both control points
    pushed 300 mm sideways stays FAR inside them: the curve reaches 0.75 of the control offset at
    most, so a hull-based reader overstates the width by a third."""
    def draw(pdf):
        path = pdf.beginPath()
        path.moveTo(50 * PT, 100 * PT)
        # Control points at x = 350 mm; the curve itself never gets past ~275 mm.
        path.curveTo(350 * PT, 300 * PT, 350 * PT, 500 * PT, 50 * PT, 700 * PT)
        pdf.drawPath(path, stroke=1, fill=0)

    found = outline.candidates(_page(draw))
    assert len(found) == 1
    width = found[0]["width_mm"]
    # The control points sit 300 mm out; the true curve reaches 3/4 of that at the midpoint.
    assert 220.0 < width < 240.0, width
    assert abs(found[0]["height_mm"] - 600.0) < 0.5


def test_the_measurement_is_stable_once_the_sampling_is_fine_enough():
    """Checked on the real templates at 8/64/256/2048 samples; the number stops moving at 64."""
    def draw(pdf):
        path = pdf.beginPath()
        path.moveTo(50 * PT, 100 * PT)
        path.curveTo(400 * PT, 250 * PT, 400 * PT, 550 * PT, 50 * PT, 700 * PT)
        pdf.drawPath(path, stroke=1, fill=0)

    data = _page(draw)
    original = outline.BBOX_SAMPLES_PER_CURVE
    try:
        outline.BBOX_SAMPLES_PER_CURVE = 64
        coarse = outline.candidates(data)[0]["width_mm"]
        outline.BBOX_SAMPLES_PER_CURVE = 2048
        fine = outline.candidates(data)[0]["width_mm"]
    finally:
        outline.BBOX_SAMPLES_PER_CURVE = original
    assert abs(coarse - fine) < 0.1, (coarse, fine)


def test_a_transform_is_followed_into_a_scaled_group():
    """A template placed at half scale must report half the size, or every derived box is wrong."""
    def draw(pdf):
        pdf.saveState()
        pdf.translate(25 * PT, 50 * PT)
        pdf.scale(0.5, 0.5)
        pdf.rect(0, 0, 400 * PT, 600 * PT, stroke=1, fill=0)
        pdf.restoreState()

    found = outline.candidates(_page(draw))
    assert _sizes(found) == [(200.0, 300.0)]
    assert found[0]["origin_mm"] == (25.0, 50.0)


def test_furniture_is_left_out_but_nothing_large_is():
    """The VENTO templates carry a 30 x 400 mm sleeve marker and 6 mm registration dots. Neither may
    be offered as a candidate cut line; the real outline must survive."""
    def draw(pdf):
        pdf.rect(20 * PT, 20 * PT, 400 * PT, 700 * PT, stroke=1, fill=0)   # the outline
        pdf.rect(50 * PT, 50 * PT, 30 * PT, 400 * PT, stroke=1, fill=0)    # sleeve marker
        pdf.rect(10 * PT, 10 * PT, 6 * PT, 6 * PT, stroke=1, fill=0)       # registration dot

    found = outline.candidates(_page(draw))
    assert _sizes(found) == [(400.0, 700.0)]


def test_the_sheet_is_labelled_not_hidden():
    """An earlier version dropped anything within 5 mm of the page and silently ate a real outline
    drawn 0.4 mm inside it. The list must show everything and say which entry is the sheet."""
    def draw(pdf):
        pdf.rect(0, 0, 500 * PT, 800 * PT, stroke=1, fill=0)               # the sheet
        pdf.rect(0.2 * PT, 0.2 * PT, 499.6 * PT, 799.6 * PT, stroke=1, fill=0)   # a real guide
        pdf.rect(30 * PT, 30 * PT, 440 * PT, 740 * PT, stroke=1, fill=0)   # the cut

    found = outline.mark_page_sized(outline.candidates(_page(draw)), (500.0, 800.0))
    assert len(found) == 3, _sizes(found)
    # Both page-sized entries are flagged, and the 0.4 mm-inside guide is STILL in the list.
    assert [e["page_sized"] for e in found] == [True, True, False]
    assert found[2]["width_mm"] == 440.0


def test_subpaths_of_one_painting_operation_share_a_group():
    """An outline with a hole is several subpaths of one operation and has to be offered as one
    candidate, not two."""
    def draw(pdf):
        path = pdf.beginPath()
        path.rect(20 * PT, 20 * PT, 400 * PT, 700 * PT)
        path.rect(100 * PT, 100 * PT, 200 * PT, 300 * PT)
        pdf.drawPath(path, stroke=1, fill=0)

    found = outline.candidates(_page(draw))
    assert len(found) == 2
    assert found[0]["group"] == found[1]["group"]


def test_a_clip_path_is_reported_as_a_clip():
    """Some templates record the shape only as a clipping path, so it must not be discarded — but it
    must be distinguishable from a drawn line."""
    def draw(pdf):
        pdf.saveState()
        path = pdf.beginPath()
        path.rect(40 * PT, 40 * PT, 300 * PT, 500 * PT)
        pdf.clipPath(path, stroke=0, fill=0)
        pdf.restoreState()

    found = outline.candidates(_page(draw))
    assert [e["painted"] for e in found] == ["clip"], found
    assert _sizes(found) == [(300.0, 500.0)]


def test_an_unreadable_file_yields_no_candidates_rather_than_raising():
    assert outline.candidates(b"not a pdf at all") == []
    assert outline.candidates(_page(lambda pdf: None), page_index=7) == []
