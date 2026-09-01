"""The rules ported from the in-house engine, and the two things a shop owns about them.

Every PDF here is authored on the spot with reportlab rather than mocked, because the questions being
asked — is this a Separation, is that colour space RGB, does a font survive an export — are questions
about real PDF objects, and a mocked fact dict would pass whatever the extractor happened to produce.
The extractors themselves were checked against the PyMuPDF engine they replace on real production
files; that comparison is recorded in `prepress/structure.py`.

Run: python3.13 -m pytest tests -q
"""
import io
import os
import sys

import pytest
from reportlab.lib.colors import CMYKColorSep
from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import generate, identify, item, materials, messages, rules, structure  # noqa: E402

BANNER = {"id": "banner-frontlit-510", "name": "Banner frontlit 510 g", "bleed_mm": 20,
          "safe_mm": 30, "min_dpi": 100, "max_width_mm": 1600, "colour": "cmyk", "notes": "",
          "spec_position": "panel", "cut_path": False}
STICKER = dict(BANNER, id="naklejka-cieta", name="Naklejka cięta", cut_path=True)
ANY_COLOUR = dict(BANNER, id="dowolne-kolory", colour="any")


def _pdf(draw, pages=1):
    """A minimal real PDF. `draw(canvas)` is called once per page."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(200, 200))
    for _ in range(pages):
        draw(pdf)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _facts(pdf_bytes, filename="plik.pdf"):
    """Structural facts only — the geometry rules are covered in test_round_trip."""
    declared = structure.facts(pdf_bytes, filename)
    return {key: value for key, value in declared.items() if key not in ("readable", "reason")}


def _expected():
    return identify.stamped_geometry(generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))


def _one(findings, rule_id):
    hits = [f for f in findings if f["id"] == rule_id]
    return hits[0] if hits else None


# ── The extractor, on files that really contain what is being looked for ─────

def test_a_separation_is_read_back_by_name():
    """The colorant NAME is the only reliable signal for a die line, so it has to survive the read."""
    def draw(pdf):
        pdf.setStrokeColor(CMYKColorSep(0, 1, 1, 0, spotName="Cut"))
        pdf.rect(10, 10, 100, 100, stroke=1, fill=0)
        pdf.setFillColor(CMYKColorSep(0, 0, 0, 1, spotName="PANTONE 711 C"))
        pdf.rect(20, 20, 50, 50, stroke=0, fill=1)

    facts = _facts(_pdf(draw))
    assert facts["spot_names"] == ["Cut", "PANTONE 711 C"]
    # The whole point of the split: one is an instruction to a machine, the other is an ink.
    assert structure.cut_spots(facts["spot_names"]) == ["Cut"]
    assert structure.ink_spots(facts["spot_names"]) == ["PANTONE 711 C"]


def test_a_pdf_name_escape_is_decoded():
    """`#20` is a space in a PDF name. Left encoded it is unreadable in a report and unmatchable."""
    def draw(pdf):
        pdf.setFillColor(CMYKColorSep(0, 0, 0, 1, spotName="LINIA SZYCIA"))
        pdf.rect(5, 5, 20, 20, stroke=0, fill=1)

    facts = _facts(_pdf(draw))
    assert facts["spot_names"] == ["LINIA SZYCIA"]
    # A Polish finishing separation is technical, not an ink to convert — a stem match, not a word.
    assert structure.ink_spots(facts["spot_names"]) == []


def test_an_unreadable_file_is_an_answer_not_a_crash():
    facts = structure.facts(b"this is not a pdf", "nope.pdf")
    assert facts["readable"] is False and facts["reason"]
    assert facts["fonts"] == [] and facts["pages"] == 0


def test_the_filename_rules_read_the_name_only():
    assert structure.filename_facts("baner-800x2000mm.pdf") == {
        "extension": "pdf", "has_diacritics": False, "extra_dots": False}
    assert structure.filename_facts("ulotka.wersja2.pdf")["extra_dots"] is True
    assert structure.filename_facts("łąka.pdf")["has_diacritics"] is True


# ── Where the rasters land, and at what resolution ──────────────────────────

def _textured_image(width=400, height=200):
    """A real raster, textured so it is not mistaken for a flat fill."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    raster = Image.new("RGB", (width, height), (200, 30, 30))
    for x in range(0, width, 8):
        for y in range(0, height, 8):
            raster.putpixel((x, y), (20, 20, 200))
    holder = io.BytesIO()
    raster.save(holder, format="PNG")
    holder.seek(0)
    return ImageReader(holder)


def _page_with_three_placements():
    """One page, three placements whose true resolution we chose, so the answer is known.

    400 px across 200 pt is 144 DPI; the same image rotated is still 144; drawn inside a form at half
    scale it is 100 pt wide, so 288.
    """
    image = _textured_image()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(1000, 1000))
    pdf.drawImage(image, 0, 800, width=200, height=100)

    pdf.saveState()
    pdf.translate(500, 500)
    pdf.rotate(90)
    pdf.drawImage(image, 0, 0, width=200, height=100)
    pdf.restoreState()

    form = pdf.beginForm("nested")
    pdf.drawImage(image, 0, 0, width=200, height=100)
    pdf.endForm()
    pdf.saveState()
    pdf.translate(100, 100)
    pdf.scale(0.5, 0.5)
    pdf.doForm("nested")
    pdf.restoreState()

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_resolution_comes_from_the_placement_not_from_the_image():
    """A 4000 px image is 400 DPI on a 250 mm page and 40 DPI on a 2.5 m one, so the pixel count
    alone answers nothing — the placement transform does."""
    placements = structure.placed_images(_page_with_three_placements())
    assert [p["dpi"] for p in placements] == [144, 144, 288]
    assert placements[0]["placed_mm"] == (70.6, 35.3)


def test_a_rotated_placement_is_not_measured_by_its_bounding_box():
    """The trap: a bounding box is axis-aligned, so under a 90° rotation its width belongs to the
    image's pixel HEIGHT. Pairing them reported 112 DPI for a banner that was really 75."""
    rotated = structure.placed_images(_page_with_three_placements())[1]
    assert rotated["dpi"] == 144
    assert rotated["placed_mm"] == (70.6, 35.3)


def test_a_placement_inside_a_form_is_scaled_twice():
    nested = structure.placed_images(_page_with_three_placements())[2]
    assert nested["dpi"] == 288, "the form's own matrix has to compose with the page's CTM"


def test_a_decorative_icon_does_not_condemn_the_artwork():
    """A 36×27 px icon at 72 DPI must not decide the verdict for a 200 DPI banner."""
    artwork = {"px": (2000, 1000), "placed_mm": (500, 250), "dpi": 200, "area_fraction": 0.9}
    icon = {"px": (36, 27), "placed_mm": (12, 9), "dpi": 72, "area_fraction": 0.004}
    assert structure.min_significant_dpi([artwork, icon]) == 200
    # With nothing significant at all, the small ones are all there is to go on.
    assert structure.min_significant_dpi([icon]) == 72
    assert structure.min_significant_dpi([]) is None


def test_a_vector_only_page_has_no_resolution_to_report():
    """None means "no raster", and the rule says so rather than inventing a number."""
    vector_only = _pdf(lambda pdf: pdf.rect(10, 10, 100, 100, stroke=1, fill=0))
    assert structure.placed_images(vector_only) == []
    assert structure.min_significant_dpi(structure.placed_images(vector_only)) is None


# ── The ported rules ────────────────────────────────────────────────────────

def test_an_office_export_is_refused_because_magic_bytes_cannot_catch_it():
    """A Word document saved as PDF *is* a valid PDF; the producer is the only tell."""
    facts = {"producer": "Microsoft Word for Microsoft 365", "creator": ""}
    finding = rules.check_office_origin(facts)
    assert finding["level"] == "red" and finding["values"]["application"] == "Word"
    assert rules.check_office_origin({"producer": "Adobe Illustrator 28.0", "creator": ""}) is None
    # No metadata at all is not evidence of anything.
    assert rules.check_office_origin({}) is None


def test_rgb_is_judged_against_the_material_not_a_constant():
    """`ICCBased` alone says nothing — the profile's component count is what makes it RGB."""
    facts = {"colour_spaces": ["ICCBased(RGB)"]}
    assert rules.check_colour_mode(facts, material=BANNER)["level"] == "amber"
    # A material the shop did not declare CMYK is not judged on colour at all.
    assert rules.check_colour_mode(facts, material=ANY_COLOUR) is None
    assert rules.check_colour_mode({"colour_spaces": ["DeviceCMYK"]},
                                   material=BANNER)["level"] == "green"
    # A CMYK ICC profile is worth a look but is not a fault.
    assert rules.check_colour_mode({"colour_spaces": ["ICCBased(CMYK)"]},
                                   material=BANNER)["level"] == "info"


def test_nothing_declared_means_nothing_said_about_colour():
    """A fully vector file that sets colour inline declares no colour space. Inventing a verdict from
    that absence would be a guess, and `structure.py` states the limit rather than hiding it."""
    assert rules.check_colour_mode({"colour_spaces": []}, material=BANNER) is None


def test_a_cut_separation_is_never_reported_as_an_ink_to_convert():
    """The destructive advice this guard exists for: telling a customer to flatten their die line."""
    finding = rules.check_spot_inks({"spot_names": ["Cut", "Regmark"]})
    assert finding is None
    with_ink = rules.check_spot_inks({"spot_names": ["Cut", "PANTONE 711 C"]})
    assert with_ink["level"] == "amber"
    assert "Cut" not in with_ink["values"]["inks"]


def test_process_inks_under_their_own_names_are_not_extra_inks():
    """A DeviceN image separated into /Cyan /Magenta /Yellow /Black produced the advice "convert
    Cyan to CMYK", which means nothing."""
    assert rules.check_spot_inks({"spot_names": ["Cyan", "Magenta", "Yellow", "Black"]}) is None


def test_the_die_line_is_only_required_where_the_shop_says_it_is():
    no_spots = {"spot_names": []}
    assert rules.check_cut_path(no_spots, material=BANNER) is None
    assert rules.check_cut_path(no_spots, material=STICKER)["level"] == "amber"
    present = rules.check_cut_path({"spot_names": ["Cut"]}, material=STICKER)
    assert present["level"] == "green" and present["values"]["cut"] == "Cut"


def test_live_text_not_a_font_resource_is_what_means_not_converted():
    """A `/Font` resource only proves a font is AVAILABLE. reportlab — which draws this project's own
    templates — registers Helvetica whether or not anything is typed, so a customer who pasted
    artwork over our spec panel would otherwise be told to convert text that is not there."""
    assert rules.check_fonts_converted({"fonts": [], "shows_text": False})["level"] == "green"
    assert rules.check_fonts_converted({"fonts": ["Helvetica"],
                                        "shows_text": False})["level"] == "green"
    finding = rules.check_fonts_converted({"fonts": ["AAAAAA+Montserrat-Bold", "Helvetica"],
                                           "shows_text": True})
    assert finding["level"] == "amber"
    # The subset prefix is how the FILE names a font, not how a human does.
    assert finding["values"]["fonts"] == "Montserrat-Bold, Helvetica"
    # Facts that were never read produce no finding, rather than a pass.
    assert rules.check_fonts_converted({}) is None


def test_text_is_found_inside_a_form_xobject_too():
    """Illustrator nests artwork in forms, and text one level down is still text."""
    def draw(pdf):
        form = pdf.beginForm("label")
        pdf.setFont("Helvetica", 12)
        pdf.drawString(10, 10, "NAPIS")
        pdf.endForm()
        pdf.doForm("label")

    assert _facts(_pdf(draw))["shows_text"] is True
    assert _facts(_pdf(lambda pdf: pdf.rect(5, 5, 20, 20)))["shows_text"] is False


def test_overprint_is_read_off_the_graphics_state():
    """Injected with pikepdf, because no drawing API turns it on — and a file in the wild has it."""
    import pikepdf

    pdf_bytes = _pdf(lambda pdf: pdf.rect(10, 10, 50, 50, stroke=1, fill=0))
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        state = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/ExtGState"), OP=True))
        page.obj["/Resources"]["/ExtGState"] = pikepdf.Dictionary(GS0=state)
        out = io.BytesIO()
        pdf.save(out)
    assert _facts(out.getvalue())["overprint"] is True
    assert rules.check_overprint(_facts(out.getvalue()))["level"] == "amber"
    assert rules.check_overprint(_facts(pdf_bytes)) is None


def test_a_second_page_is_a_mistake_except_on_cut_work():
    """Where the die line lives on its own page, a two-page file is correct by design."""
    facts = _facts(_pdf(lambda pdf: pdf.rect(10, 10, 50, 50), pages=3))
    assert facts["pages"] == 3
    assert rules.check_page_count(facts, material=BANNER)["level"] == "amber"
    assert rules.check_page_count(facts, material=STICKER)["level"] == "info"
    assert rules.check_page_count({"pages": 1}, material=BANNER) is None


def test_a_name_with_both_problems_reports_the_one_that_mangles():
    both = {"has_diacritics": True, "extra_dots": True}
    assert rules.check_filename(both)["code"] == "check.filename.diacritics"
    assert rules.check_filename({"extra_dots": True})["code"] == "check.filename.dots"
    assert rules.check_filename({}) is None


# ── The shop owns the severity ──────────────────────────────────────────────

def test_a_shop_can_silence_a_rule_entirely():
    facts = {"page_mm": (1040, 2040), "fonts": ["Helvetica"], "shows_text": True}
    assert _one(rules.run(facts, _expected(), BANNER), "fonts")["level"] == "amber"
    assert _one(rules.run(facts, _expected(), BANNER, levels={"fonts": "off"}), "fonts") is None


def test_a_shop_can_move_a_rule_between_amber_and_red():
    facts = {"page_mm": (1040, 2040), "colour_spaces": ["ICCBased(RGB)"]}
    findings = rules.run(facts, _expected(), BANNER, levels={"colour_mode": "red"})
    assert _one(findings, "colour_mode")["level"] == "red"
    # And the report's one-line summary follows, because it counts levels rather than rule names.
    assert rules.summarise(findings).startswith("BŁĘDY")


def test_a_pass_is_never_re_graded_into_a_failure():
    """A shop asking for "fonts: red" wants a file WITH fonts flagged, not a clean file called
    broken."""
    facts = {"page_mm": (1040, 2040), "fonts": [], "shows_text": False}
    finding = _one(rules.run(facts, _expected(), BANNER, levels={"fonts": "red"}), "fonts")
    assert finding["level"] == "green"


def test_a_severity_for_a_rule_this_build_does_not_have_is_harmless():
    facts = {"page_mm": (1040, 2040)}
    findings = rules.run(facts, _expected(), BANNER, levels={"a_rule_from_the_future": "red"})
    assert [f for f in findings if f["level"] == "red"] == []


def test_the_severities_survive_a_reword_because_both_are_keyed_on_the_rule_id():
    for rule_id in rules.RULE_IDS:
        assert any(code.startswith(f"check.{rule_id}.")
                   for code in messages.DEFAULT_MESSAGES), rule_id


def test_every_rule_label_points_at_a_message_that_exists():
    """A typo here puts `check.fnots.present` on a control in the admin panel."""
    for rule_id, code in rules.RULE_LABELS.items():
        assert code in messages.DEFAULT_MESSAGES, (rule_id, code)
        assert code.startswith(f"check.{rule_id}."), (rule_id, code)


def test_a_label_elides_its_numbers_instead_of_showing_the_template():
    """A control labelled `… {dpi} DPI` is a template leaking into the interface."""
    assert messages.render_label("check.resolution.low") == "Rozdzielczość za niska: … DPI"
    assert "{" not in messages.render_label("check.bleed_coverage.short")
    # A shop's own wording is labelled the same way.
    assert messages.render_label("check.fonts.present",
                                 {"check.fonts.present": "Na krzywe, proszę."}) == "Na krzywe, proszę."


def test_no_check_message_is_keyed_on_a_rule_that_does_not_exist():
    """The typo this catches puts `check.fnots.present` in front of a customer, because an unknown
    code renders as itself rather than raising."""
    known = set(rules.RULE_IDS) | {"rule"}          # `check.rule.failed` is the runner's own
    for code in messages.CHECK_MESSAGES:
        if code.startswith("summary."):
            continue
        assert code.split(".")[1] in known, code


def test_every_rule_the_panel_offers_is_a_rule_that_runs():
    """RULE_IDS is what an admin's saved severities are keyed on, so a stale entry there is a control
    that silently does nothing."""
    assert len(rules.RULE_IDS) == len(rules.RULES)
    facts = {"page_mm": (900, 1800), "fonts": ["X"], "shows_text": True,
             "overprint": True, "pages": 4,
             "has_diacritics": True, "colour_spaces": ["ICCBased(RGB)"],
             "spot_names": ["PANTONE 711 C"], "producer": "Microsoft Word",
             "blank_edges_mm": (9, 0, 0, 0), "safe_intrusion_mm": 4.0, "min_dpi": 20,
             "guides_present": True, "declared_boxes_mm": {"trimbox": (980, 1980)}}
    produced = {f["id"] for f in rules.run(facts, _expected(), STICKER)}
    assert set(rules.RULE_IDS) - produced == set(), set(rules.RULE_IDS) - produced


# ── The shop owns the wording ───────────────────────────────────────────────

def test_a_finding_carries_a_code_and_numbers_not_only_a_sentence():
    facts = {"page_mm": (1040, 2040), "fonts": ["Helvetica"], "shows_text": True}
    finding = _one(rules.run(facts, _expected(), BANNER), "fonts")
    assert finding["code"] == "check.fonts.present"
    assert finding["values"] == {"fonts": "Helvetica"}


def test_a_shop_can_reword_a_finding_without_touching_python():
    facts = {"page_mm": (1040, 2040), "fonts": ["Helvetica"], "shows_text": True}
    wording = {"check.fonts.present": "Zamień teksty na krzywe ({fonts})."}
    finding = _one(rules.run(facts, _expected(), BANNER, wording=wording), "fonts")
    assert finding["title"] == "Zamień teksty na krzywe (Helvetica)."


def test_a_finding_with_no_advice_line_gets_an_empty_detail_not_a_code():
    """`check.fonts.ok` has no `.detail`, and printing the code at a customer would be worse than
    printing nothing."""
    finding = _one(rules.run({"page_mm": (1040, 2040), "fonts": [], "shows_text": False},
                             _expected(), BANNER), "fonts")
    assert finding["detail"] == ""


def test_every_default_message_renders_with_the_values_its_rule_supplies():
    """A placeholder nobody fills is a `{like_this}` in a customer's face."""
    facts = {"page_mm": (900, 1800), "fonts": ["X"], "shows_text": True,
             "overprint": True, "pages": 4,
             "has_diacritics": True, "colour_spaces": ["ICCBased(RGB)"],
             "spot_names": ["PANTONE 711 C"], "producer": "Microsoft Word",
             "blank_edges_mm": (9, 0, 0, 0), "safe_intrusion_mm": 4.0, "min_dpi": 20,
             "guides_present": True,
             "declared_boxes_mm": {"trimbox": (980, 1980)}}
    findings = rules.run(facts, _expected(), STICKER)
    assert len(findings) >= 12, [f["id"] for f in findings]
    for finding in findings:
        assert "{" not in finding["title"], finding
        assert "{" not in finding["detail"], finding


# ── The whole loop, on a file that looks like a real return ─────────────────

def _designer_export(netto_mm, dpi, material=BANNER):
    """What a competent designer sends back: our page size, full-bleed CMYK artwork at a chosen
    resolution, our guides gone, our stamp still on the page."""
    import json

    import pikepdf
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    resolved = item.resolve(material, *netto_mm)
    page_w, page_h = item.page_size_mm(resolved)
    pixels_wide = int(page_w / 25.4 * dpi)
    pixels_high = int(pixels_wide * page_h / page_w)
    raster = Image.new("CMYK", (pixels_wide, pixels_high), (10, 200, 200, 5))
    for x in range(0, pixels_wide, 5):
        for y in range(0, pixels_high, 5):
            raster.putpixel((x, y), (200, 30, 30, 10))
    holder = io.BytesIO()
    raster.save(holder, format="TIFF")
    holder.seek(0)

    buffer = io.BytesIO()
    points = 72 / 25.4
    pdf = canvas.Canvas(buffer, pagesize=(page_w * points, page_h * points))
    pdf.drawImage(ImageReader(holder), 0, 0, width=page_w * points, height=page_h * points)
    pdf.showPage()
    pdf.save()
    with pikepdf.open(io.BytesIO(buffer.getvalue())) as exported:
        exported.pages[0].obj[pikepdf.Name(generate.STAMP_KEY)] = pikepdf.String(
            json.dumps(generate.stamp_payload(resolved), ensure_ascii=False))
        final = io.BytesIO()
        exported.save(final)
        return final.getvalue()


def _verdict(pdf_bytes, material=BANNER, filename="projekt-500x700mm.pdf"):
    from prepress import measure

    expected = identify.stamped_geometry(identify.read_stamp(pdf_bytes)["stamp"])
    facts = dict(measure.measure(pdf_bytes, expected))
    facts["page_mm"] = identify.page_size_mm(pdf_bytes)
    facts["declared_boxes_mm"] = identify.declared_boxes_mm(pdf_bytes)
    facts.update({key: value for key, value in structure.facts(pdf_bytes, filename).items()
                  if key not in ("readable", "reason")})
    return rules.run(facts, expected, material)


def test_a_correct_return_passes_every_single_rule():
    """The calibration test. A rule set that cries wolf on good work is worse than none, and this is
    the only case that proves it does not: real artwork, measured, fourteen rules, no complaints."""
    findings = _verdict(_designer_export((500, 700), dpi=150))
    assert [f["level"] for f in findings] == ["green"] * len(findings), \
        [(f["id"], f["level"], f["title"]) for f in findings if f["level"] != "green"]
    assert {f["id"] for f in findings} >= {"page_size", "bleed_coverage", "safe_area", "resolution",
                                           "colour_mode", "fonts", "template_guides"}


def test_the_same_file_at_a_quarter_of_the_resolution_is_a_hard_error():
    """And nothing else changes, which is what makes the resolution number trustworthy."""
    findings = _verdict(_designer_export((500, 700), dpi=40))
    failed = [(f["id"], f["level"]) for f in findings if f["level"] != "green"]
    assert failed == [("resolution", "red")], failed


# ── The store keeps all three blocks ────────────────────────────────────────

def test_cut_path_survives_the_form_as_a_real_boolean(tmp_path):
    store = str(tmp_path / "materials.json")
    # A select posts strings; "0" and "" are the two that must not mean "yes".
    materials.save_all([dict(STICKER, cut_path="1"), dict(BANNER, cut_path="0")], store)
    stored = {m["id"]: m["cut_path"] for m in materials.load(store)}
    assert stored == {"naklejka-cieta": True, "banner-frontlit-510": False}


def test_rule_severities_round_trip_and_survive_a_material_save(tmp_path):
    store = str(tmp_path / "materials.json")
    materials.save_all([BANNER], store)
    materials.save_messages({"check.fonts.present": "Na krzywe."}, store)
    materials.save_rule_levels({"fonts": "off", "colour_mode": "red", "bogus": "purple"}, store)
    # An unknown LEVEL is dropped; an unknown RULE is kept, so a downgrade survives a build change.
    assert materials.load_rule_levels(store) == {"fonts": "off", "colour_mode": "red"}

    materials.save_all([BANNER, STICKER], store)
    assert materials.load_rule_levels(store) == {"fonts": "off", "colour_mode": "red"}
    assert materials.load_messages(store) == {"check.fonts.present": "Na krzywe."}
    assert len(materials.load(store)) == 2


@pytest.mark.parametrize("level", ["off", "info", "amber", "red"])
def test_every_offered_severity_is_actually_accepted(level, tmp_path):
    store = str(tmp_path / "materials.json")
    materials.save_all([BANNER], store)
    assert materials.save_rule_levels({"fonts": level}, store) == {"fonts": level}
