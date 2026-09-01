"""Look at what we have, create what we do not, generate to the rules — and the offset maths under it.

The shop's rule for the generator, verbatim: „Patrzymy co mamy, tworzymy czego nie mamy, generujemy
szablon dla klienta zgodnie z ustalonymi wcześniej zasadami."

The offset tests come first because everything else rests on them, and both of their assertions exist
because the first implementation got them wrong: a negative distance GREW a square, and a square
shrunk past its own width came back as a small square instead of a refusal.

Run: python3.13 -m pytest tests -q
"""
import io
import math
import os
import sys

import pytest
from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import (from_template, identify, lines, offset, outline, shape,  # noqa: E402
                      specblock)

PT = 72 / 25.4


def _square(size=100.0, at=(0.0, 0.0)):
    x, y = at
    return {"start": (x, y), "closed": True,
            "segments": [("l", (x + size, y)), ("l", (x + size, y + size)),
                         ("l", (x, y + size)), ("l", (x, y))]}


# ── The offset maths ────────────────────────────────────────────────────────

def test_a_negative_distance_shrinks_and_a_positive_one_grows():
    """The direction was inverted in the first version: -10 grew a 100 mm square to 120 mm."""
    smaller, problem = offset.offset_outline(_square(), -10)
    assert problem is None
    assert (smaller["width_mm"], smaller["height_mm"]) == (80.0, 80.0)

    bigger, problem = offset.offset_outline(_square(), +5)
    assert problem is None
    assert (bigger["width_mm"], bigger["height_mm"]) == (110.0, 110.0)


def test_a_margin_that_does_not_fit_is_refused_not_approximated():
    """60 mm off both sides of a 100 mm shape leaves nothing. The first version returned a 20 mm
    square, because the collapsed ring stays counter-clockwise and an area-sign check cannot see it."""
    result, problem = offset.offset_outline(_square(), -60)
    assert result is None
    assert "nie zmieści się" in problem


def test_the_offset_really_is_the_distance_in():
    """Not just a smaller box: every point of the result must be that far from the original edge."""
    inset, problem = offset.offset_outline(_square(200.0), -25)
    assert problem is None
    points = [inset["start"]] + [s[1] for s in inset["segments"]]
    for point in points:
        assert 25.0 - 0.5 <= min(point[0], point[1], 200 - point[0], 200 - point[1]) <= 25.0 + 0.5


def test_a_curve_is_offset_as_a_curve_not_as_its_bounding_box():
    """The point of doing this properly: on a curved shape the true inset is SMALLER than a
    rectangle inset from the bounding box, because the extremes sit on curves."""
    circleish = {"start": (100.0, 0.0), "closed": True, "segments": [
        ("c", (155.0, 0.0), (200.0, 45.0), (200.0, 100.0)),
        ("c", (200.0, 155.0), (155.0, 200.0), (100.0, 200.0)),
        ("c", (45.0, 200.0), (0.0, 155.0), (0.0, 100.0)),
        ("c", (0.0, 45.0), (45.0, 0.0), (100.0, 0.0))]}
    inset, problem = offset.offset_outline(circleish, -30)
    assert problem is None
    # A circle of radius 100 inset by 30 has radius 70, so 140 across — and the bounding-box answer
    # would also be 140 here, which is why the check is on the RADIUS at 45 degrees.
    points = [inset["start"]] + [s[1] for s in inset["segments"]]
    radii = [math.dist(p, (100.0, 100.0)) for p in points]
    assert 69.0 < min(radii) and max(radii) < 71.5, (min(radii), max(radii))


# ── Look, create, generate ──────────────────────────────────────────────────

def _template(outlines, bleed_mm=0.0, safe_mm=40.0, page=(500.0, 800.0)):
    return {"token": "t1", "name": "Szablon testowy", "page_mm": list(page),
            "outlines": outlines, "bleed_mm": bleed_mm, "safe_mm": safe_mm}


def _typed(entry, kind, safe_base=False):
    """An outline with its type and, independently, whether the margin is measured from it.

    The two are separate on purpose: the shop's 633 mm outline is a CUT LINE that is also the edge the
    safe margin comes off, and an earlier model made those mutually exclusive.
    """
    return {**shape.serialise(entry), "type": kind, "safe_base": safe_base}


def test_a_drawn_box_is_used_and_a_missing_one_is_created():
    """The rule, in one test, on the shape of a real flag: brutto is drawn so it is taken as it
    stands; the safe area is not drawn, so it is computed from the line the shop flagged."""
    template = _template([_typed(_square(400.0, (50.0, 50.0)), "bleed"),
                          _typed(_square(360.0, (70.0, 70.0)), "cut"),
                          _typed(_square(300.0, (100.0, 100.0)), "cut", safe_base=True)],
                         safe_mm=40)
    drawing, notes = from_template.derive(template)
    assert "brutto: wzięte z szablonu" in notes
    assert "obszar bezpieczny: dorobiony — od zaznaczonej linii minus 40 mm" in notes
    assert drawing["brutto"][0]["width_mm"] == 400.0
    # BOTH cut lines are drawn, and the finished size is their union — the wider one.
    assert sorted(o["width_mm"] for o in drawing["netto"]) == [300.0, 360.0]
    assert drawing["safe"][0]["width_mm"] == pytest.approx(220.0, abs=0.5)   # 300 - 2*40


def test_the_margin_comes_off_the_flagged_line_not_the_widest_cut():
    """The correction that made the flag work: the tunnel hem is the cut, the body edge is what the
    margin is measured from, and they are different lines."""
    hem = _typed(_square(400.0, (50.0, 50.0)), "cut")
    body = _typed(_square(300.0, (100.0, 100.0)), "cut", safe_base=True)
    drawing, _notes = from_template.derive(_template([hem, body], safe_mm=40))
    # 300 - 2*40 = 220, NOT 400 - 2*40 = 320.
    assert drawing["safe"][0]["width_mm"] == pytest.approx(220.0, abs=0.5)


def test_with_no_line_flagged_the_margin_comes_off_the_cut():
    drawing, notes = from_template.derive(
        _template([_typed(_square(400.0, (50.0, 50.0)), "cut")], safe_mm=40))
    assert "obszar bezpieczny: dorobiony — od cięcia minus 40 mm" in notes
    assert drawing["safe"][0]["width_mm"] == pytest.approx(320.0, abs=0.5)


def test_a_drawn_safe_area_wins_over_the_typed_margin():
    """The shop drew it because that is where the edge really is; no computation improves on that."""
    template = _template([_typed(_square(360.0, (70.0, 70.0)), "cut"),
                          _typed(_square(280.0, (110.0, 110.0)), "safe")], safe_mm=40)
    drawing, notes = from_template.derive(template)
    assert "obszar bezpieczny: wzięty z szablonu" in notes
    assert drawing["safe"][0]["width_mm"] == 280.0


def test_with_nothing_drawn_both_boxes_are_created_from_the_cut():
    template = _template([_typed(_square(360.0, (70.0, 70.0)), "cut")], bleed_mm=20, safe_mm=30)
    drawing, notes = from_template.derive(template)
    assert "brutto: dorobione — cięcie + 20 mm" in notes
    assert "obszar bezpieczny: dorobiony — od cięcia minus 30 mm" in notes
    assert drawing["brutto"][0]["width_mm"] == pytest.approx(400.0, abs=0.5)
    assert drawing["safe"][0]["width_mm"] == pytest.approx(300.0, abs=0.5)


def test_a_template_with_no_cut_line_cannot_be_generated():
    template = _template([_typed(_square(360.0), "crease")])
    with pytest.raises(from_template.TemplateError, match="linii cięcia"):
        from_template.derive(template)


def test_an_impossible_margin_is_reported_rather_than_drawn_wrong():
    """A safe area that folded through itself would look plausible on a customer's sheet."""
    template = _template([_typed(_square(100.0), "cut")], safe_mm=80)
    with pytest.raises(from_template.TemplateError, match="obszaru bezpiecznego"):
        from_template.derive(template)


# ── The sheet that comes out ────────────────────────────────────────────────

def test_the_generated_sheet_measures_the_way_it_was_meant_to():
    """Read the output back with the same reader that read the input: what was drawn has to measure
    right, which is the only check that covers the whole chain.

    The shapes are deliberately large: `outline.candidates` ignores anything under 0.05 m², so a
    360 mm safe area would be drawn correctly and then filtered out of the verification.
    """
    template = _template([_typed(_square(700.0, (50.0, 50.0)), "bleed"),
                          _typed(_square(640.0, (80.0, 80.0)), "cut"),
                          _typed(_square(560.0, (120.0, 120.0)), "cut", safe_base=True)],
                         safe_mm=40, page=(800.0, 1200.0))
    pdf_bytes = from_template.build_pdf(template)
    widths = sorted(c["width_mm"] for c in outline.candidates(pdf_bytes))
    assert any(abs(w - 480.0) < 1.0 for w in widths), widths      # safe: 560 - 2*40
    assert any(abs(w - 640.0) < 0.5 for w in widths), widths      # cut, as drawn
    assert any(abs(w - 700.0) < 0.5 for w in widths), widths      # brutto, as drawn


def test_the_sheet_keeps_the_production_page_and_says_so_in_its_stamp():
    """A shaped template's sheet is NOT netto plus bleed per side, so the stamp carries the real page
    — otherwise a checker fails every correct return of this template."""
    template = _template([_typed(_square(360.0, (70.0, 70.0)), "cut")],
                         bleed_mm=20, safe_mm=30, page=(500.0, 800.0))
    pdf_bytes = from_template.build_pdf(template)
    assert [round(v) for v in identify.page_size_mm(pdf_bytes)] == [500, 800]

    found = identify.read_stamp(pdf_bytes)
    assert found, found.get("reason")
    geometry = identify.stamped_geometry(found["stamp"])
    assert geometry["template"] == "t1"
    assert [round(v) for v in geometry["brutto_mm"]] == [500, 800], "the real sheet, not 400+2*20"
    assert [round(v) for v in geometry["netto_mm"]] == [360, 360]


def test_a_double_sided_template_gives_a_page_per_side():
    """Two-sided products are ordinary. A designer handed ONE sheet for a double-sided flag has to
    guess which half is which, and guessing is the thing this project removes."""
    import pikepdf

    template = _template([_typed(_square(640.0, (80.0, 80.0)), "cut")],
                         safe_mm=30, page=(800.0, 1200.0))
    template["sides"] = 2
    pdf_bytes = from_template.build_pdf(template)
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        assert len(pdf.pages) == 2

    # Each page says WHICH side it is, so a returned back cannot be mistaken for a returned front.
    front = identify.read_stamp(pdf_bytes, 0)
    back = identify.read_stamp(pdf_bytes, 1)
    assert front["stamp"]["side"] == "PRZÓD"
    assert back["stamp"]["side"] == "TYŁ"
    assert front["stamp"]["sides"] == 2
    assert front["stamp"]["mirrored"] is False and back["stamp"]["mirrored"] is True


def test_the_back_of_a_double_sided_template_is_mirrored():
    """Shop rule, 2026-08-24: „druga strona musi być odbiciem lustrzanym". The pole is on the left of
    the front, so from behind it is on the right — an unmirrored back puts the artwork on the wrong
    edge."""
    # Deliberately OFF-CENTRE, or a mirror is indistinguishable from doing nothing.
    template = _template([_typed(_square(400.0, (60.0, 100.0)), "cut")],
                         safe_mm=0, page=(800.0, 1200.0))
    template["sides"] = 2
    pdf_bytes = from_template.build_pdf(template)

    front = outline.candidates(pdf_bytes, 0)
    back = outline.candidates(pdf_bytes, 1)
    cut_front = next(c for c in front if abs(c["width_mm"] - 400.0) < 0.5)
    cut_back = next(c for c in back if abs(c["width_mm"] - 400.0) < 0.5)
    # Front sits 60 mm from the left; mirrored about an 800 mm page it sits 800-60-400 = 340 mm in.
    assert cut_front["origin_mm"][0] == pytest.approx(60.0, abs=0.5)
    assert cut_back["origin_mm"][0] == pytest.approx(340.0, abs=0.5)
    # Vertically nothing moves: a flag is mirrored left-to-right, not upside down.
    assert cut_back["origin_mm"][1] == pytest.approx(cut_front["origin_mm"][1], abs=0.5)


def test_a_one_sided_sheet_is_never_mirrored():
    template = _template([_typed(_square(400.0, (60.0, 100.0)), "cut")],
                         safe_mm=0, page=(800.0, 1200.0))
    pdf_bytes = from_template.build_pdf(template)
    cut = next(c for c in outline.candidates(pdf_bytes, 0) if abs(c["width_mm"] - 400.0) < 0.5)
    assert cut["origin_mm"][0] == pytest.approx(60.0, abs=0.5)
    assert identify.read_stamp(pdf_bytes, 0)["stamp"].get("mirrored") is False


def test_a_one_sided_template_stays_one_page():
    template = _template([_typed(_square(640.0, (80.0, 80.0)), "cut")],
                         safe_mm=30, page=(800.0, 1200.0))
    import pikepdf
    with pikepdf.open(io.BytesIO(from_template.build_pdf(template))) as pdf:
        assert len(pdf.pages) == 1


def test_informational_lines_are_drawn_too():
    """A customer who cannot see where the flag folds cannot keep their logo off the fold."""
    template = _template([_typed(_square(640.0, (80.0, 80.0)), "cut"),
                          _typed(_square(400.0, (200.0, 200.0)), "crease")],
                         safe_mm=20, page=(800.0, 1200.0))
    drawing, _notes = from_template.derive(template)
    assert [kind for kind, _entry in drawing["informational"]] == ["crease"]
    pdf_bytes = from_template.build_pdf(template)
    widths = [c["width_mm"] for c in outline.candidates(pdf_bytes)]
    assert any(abs(w - 400.0) < 0.5 for w in widths), widths


def test_several_cut_lines_are_all_drawn_and_all_count_towards_netto():
    template = _template([_typed(_square(360.0, (70.0, 70.0)), "cut"),
                          _typed(_square(120.0, (400.0, 70.0)), "cut")], safe_mm=10)
    drawing, notes = from_template.derive(template)
    assert len(drawing["netto"]) == 2
    assert "netto: z 2 linii cięcia" in notes
    box = lines.trim_box_mm(template["outlines"])
    assert round(box[2] - box[0]) == 450        # 70 .. 520


# ── The spec panel's size ───────────────────────────────────────────────────

def test_the_panel_is_sized_from_the_longer_side_not_the_width():
    """The shop, looking at a generated flag: „why are those boxes with dimensions and ratio so
    small?" Because the panel was 23 % of the WIDTH, which on a 801 x 2401 mm sheet is 2.1 % of the
    height — about three pixels once the whole page is on screen."""
    from prepress import specblock

    flag_w, flag_h = specblock.panel_size_mm(800.8, 2400.8)
    assert 540 < flag_w < 570, flag_w
    assert 150 < flag_h < 165, flag_h

    # A page near square is untouched: the trade template this layout came from still gets its 180 mm.
    square_w, _square_h = specblock.panel_size_mm(784.0, 784.0)
    assert 178 < square_w < 182, square_w


def test_the_panel_never_dominates_a_narrow_sheet():
    """A very tall, very narrow sheet would otherwise get a panel wider than the page."""
    width, _height = specblock.panel_size_mm(300.0, 4000.0)
    assert width <= 300.0 * 0.70 + 0.01, width


def test_the_panel_has_a_floor_so_a_small_sheet_still_says_something():
    width, _height = specblock.panel_size_mm(200.0, 200.0)
    assert width >= 120.0
