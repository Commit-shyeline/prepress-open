"""The bundled demo artwork obeys the rule the page it decorates advertises.

The hero of a page that offers to find detail outside the safe area wears this file, and the file
never passes through `/api/check` — the 3D scene takes it straight as a texture. So nothing measured
it for as long as it existed, and the sun disc sat 430 mm outside the safe area the whole time.
These tests are that missing measurement.

Run: python3.13 -m pytest tests -q
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("reportlab", reason="the demo artwork is drawn with reportlab")


def _demo_module():
    """The generator script, imported by path: it lives in scripts/, which is not a package."""
    path = os.path.join(ROOT, "scripts", "make_demo_artwork.py")
    spec = importlib.util.spec_from_file_location("make_demo_artwork", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _demo_module()


def test_nothing_in_the_demo_artwork_leaves_the_safe_area():
    assert demo.intrusions_mm() == []


def _shop_templates():
    """The templates the repository ships in materials.json — the hero wears one of them."""
    from prepress import materials
    return materials.load_templates(os.path.join(ROOT, "materials.json"))


@pytest.mark.parametrize("template", _shop_templates(), ids=lambda t: t.get("name") or t.get("token"))
def test_the_per_template_demo_stays_inside_that_templates_safe_area(template):
    """The hero's flag is a FEATHER: its safe area is a curved outline, and a rectangle laid out in
    page fractions cannot promise to stay inside it. The per-template demo is laid out inside the
    largest box that fits the safe outline, and every element must land there."""
    from prepress import demo_artwork, from_template, offset

    try:
        box = demo_artwork.safe_box_mm(template)
    except (demo_artwork.DemoError, from_template.TemplateError):
        pytest.skip("no drawable safe area on this template")
    assert demo_artwork.intrusions_mm(template) == []
    # And the box itself really is inside the safe outline — every corner of every element is.
    drawing, _ = from_template.derive(template)
    rings = [offset.flatten(e) for e in drawing["safe"]]
    for name, (x0, y0, x1, y1) in demo_artwork.elements_mm(template).items():
        for corner in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            assert offset.all_inside(rings, corner), (name, corner, box)
    pdf = demo_artwork.build_pdf(template)
    assert pdf.startswith(b"%PDF")
    import pikepdf, io
    with pikepdf.open(io.BytesIO(pdf)) as doc:
        w, h = [float(v) for v in doc.pages[0].mediabox][2:]
        assert abs(w * 25.4 / 72 - float(template["page_mm"][0])) < 0.05
        assert abs(h * 25.4 / 72 - float(template["page_mm"][1])) < 0.05


@pytest.mark.parametrize("name", ["wordmark", "credit", "sun"])
def test_every_element_is_accounted_for(name):
    """A safe-area check that silently skips an element is worse than none — it reads as a pass."""
    assert name in [entry[0] for entry in demo.content_extents_mm()]


def test_an_element_pushed_out_is_actually_caught():
    """The guard, exercised. Without this the passing test above proves only that it never fires."""
    original = demo.SUN_CENTRE
    try:
        demo.SUN_CENTRE = (original[0], demo.HEIGHT * 0.90)      # where it used to be
        found = demo.intrusions_mm()
        assert [name for name, _ in found] == ["sun"]
        assert found[0][1] == pytest.approx(430.0, abs=1.0)
    finally:
        demo.SUN_CENTRE = original


def test_the_safe_margin_matches_the_measured_worst_case():
    """0.19 is VENTO S — a 553 mm safe area inside an 890 mm page. If that measurement is ever
    revised, this test is the place the demo's own margin gets revised with it."""
    assert demo.SAFE_MARGIN_FRACTION == pytest.approx((890.0 - 553.0) / 2 / 890.0, abs=0.002)
