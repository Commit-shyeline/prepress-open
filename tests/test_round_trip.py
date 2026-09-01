"""The loop, proven: material → item → template PDF → stamp read back → rules.

The round-trip test is the one that matters. If a template cannot identify itself on the way back, the
whole design collapses into the filename-and-size guessing this project exists to replace.

Run: python3.13 -m pytest tests -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import generate, identify, item, materials, messages, rules  # noqa: E402

# `panel` (the default) draws the spec INSIDE the artwork, so the page is exactly the brutto box.
BANNER = {"id": "banner-frontlit-510", "name": "Banner frontlit 510 g", "bleed_mm": 20,
          "safe_mm": 30, "min_dpi": 100, "max_width_mm": 1600, "colour": "cmyk",
          "spec_position": "panel", "notes": "Zgrzew i oczka"}
# The same material with the spec in a strip below, which ADDS to the page height.
BANNER_STRIP = dict(BANNER, spec_position="below")
FILM = {"id": "folia-solwent", "name": "Folia solwentowa", "bleed_mm": 3, "safe_mm": 3,
        "min_dpi": 300, "max_width_mm": None, "colour": "cmyk", "notes": ""}


# ── Materials are data an admin edits ───────────────────────────────────────

def test_a_material_round_trips_through_the_store(tmp_path):
    store = str(tmp_path / "materials.json")
    materials.save_all([BANNER, FILM], store)
    assert [m["id"] for m in materials.load(store)] == ["banner-frontlit-510", "folia-solwent"]
    assert materials.get("folia-solwent", store)["bleed_mm"] == 3


def test_editing_a_material_is_not_rejected_as_its_own_duplicate(tmp_path):
    """The mistake that makes an admin panel infuriating: saving an edit and being told the id
    already exists."""
    store = str(tmp_path / "materials.json")
    materials.save_all([BANNER], store)
    edited = dict(BANNER, bleed_mm=25, name="Banner frontlit 510 g (nowy)")
    stored = materials.upsert(edited, store)
    assert stored["bleed_mm"] == 25
    assert len(materials.load(store)) == 1, "the edit duplicated the material"


def test_removing_a_material_reports_whether_it_existed(tmp_path):
    store = str(tmp_path / "materials.json")
    materials.save_all([BANNER], store)
    assert materials.remove("banner-frontlit-510", store) is True
    assert materials.remove("banner-frontlit-510", store) is False
    assert materials.load(store) == []


def test_the_store_reloads_when_the_file_changes(tmp_path):
    """The admin panel writes the file; every other process must see it without a restart."""
    store = str(tmp_path / "materials.json")
    materials.save_all([BANNER], store)
    assert materials.get("folia-solwent", store) is None
    materials.save_all([BANNER, FILM], store)
    assert materials.get("folia-solwent", store) is not None


@pytest.mark.parametrize("bad, because", [
    ({"id": "A B", "name": "x", "bleed_mm": 3, "safe_mm": 3}, "spaces and capitals in the id"),
    ({"id": "ok-id", "name": "", "bleed_mm": 3, "safe_mm": 3}, "empty name"),
    ({"id": "ok-id", "name": "x", "bleed_mm": -1, "safe_mm": 3}, "negative bleed"),
    ({"id": "ok-id", "name": "x", "bleed_mm": 3}, "missing safe_mm"),
    ({"id": "ok-id", "name": "x", "bleed_mm": 9000, "safe_mm": 3}, "bleed typo"),
    ({"id": "ok-id", "name": "x", "bleed_mm": 3, "safe_mm": 3, "colour": "pantone"}, "bad colour"),
])
def test_a_bad_material_is_refused_with_a_reason(bad, because, tmp_path):
    with pytest.raises(materials.MaterialError):
        materials.save_all([bad], str(tmp_path / "m.json"))


def test_a_missing_store_is_empty_not_an_error(tmp_path):
    assert materials.load(str(tmp_path / "nope.json")) == []


# ── Dimensions as a human types them ────────────────────────────────────────

@pytest.mark.parametrize("value, unit, expected", [
    ("800", "mm", 800), ("80", "cm", 800), ("0,8", "m", 800),
    ("80,5", "cm", 805), (" 300 ", "cm", 3000),
])
def test_dimensions_accept_what_a_customer_actually_types(value, unit, expected):
    assert item.parse_dimension(value, unit) == pytest.approx(expected)


@pytest.mark.parametrize("value, unit", [("", "mm"), ("abc", "mm"), ("0", "mm"),
                                         ("-5", "cm"), ("5", "furlong"), ("200", "m")])
def test_nonsense_dimensions_are_refused(value, unit):
    with pytest.raises(item.ItemError):
        item.parse_dimension(value, unit)


# ── Geometry, roll width and the page ceiling ───────────────────────────────

def test_the_three_boxes_come_from_the_material():
    resolved = item.resolve(BANNER, 1000, 2000)
    assert resolved["netto_mm"] == (1000, 2000)
    assert resolved["brutto_mm"] == (1040, 2040)       # + 20 mm bleed per side
    assert resolved["safe_mm_box"] == (940, 1940)      # − 30 mm safe per side
    assert resolved["scale"] == 1


def test_fitting_the_roll_one_way_round_says_NOTHING_to_the_customer():
    """Shop rule, 2026-08-23: telling a customer their job will be "printed rotated" made a perfectly
    good job look broken. How we fit it on the roll is production's business, so exceeding the roll
    in ONE direction must produce no notice at all."""
    # 1800 mm exceeds the 1600 mm roll, but rotated the 1000 mm side fits.
    rotated = item.resolve(BANNER, 1800, 1000)
    assert rotated["notices"] == [], rotated["notices"]
    assert rotated["panels"] == 1


def test_exceeding_the_roll_BOTH_ways_means_panelling_not_refusal():
    """Neither orientation fits, so the graphic is printed in strips and welded into one piece. That
    is a real product characteristic (the welds show), so it IS said — and it is not an error."""
    panelled = item.resolve(BANNER, 1800, 1700)
    assert panelled["panels"] == 2
    codes = [n["code"] for n in panelled["notices"]]
    assert codes == ["panelled"], codes
    assert messages.level_for("panelled") == "info", "panelling is advice, not a problem"


def test_the_panel_count_follows_the_short_side():
    """Strips run along the long side, so the count is driven by the short one."""
    wide = dict(BANNER, max_width_mm=3000, bleed_mm=0)
    assert item.resolve(wide, 12000, 3000)["panels"] == 1        # short side fits the roll
    assert item.resolve(wide, 12000, 6000)["panels"] == 2
    assert item.resolve(wide, 12000, 8500)["panels"] == 3


def test_past_the_welding_ceiling_the_job_is_genuinely_refused():
    """25 x 15 m is where the technique runs out. Compared long-to-long and short-to-short, so the
    limit does not depend on which way the customer typed the dimensions."""
    roll = dict(BANNER, max_width_mm=3000, bleed_mm=0)
    with pytest.raises(item.ItemError) as too_long:
        item.resolve(roll, 26000, 8000)
    assert too_long.value.notice["code"] == "too_big_to_panel"

    with pytest.raises(item.ItemError):
        item.resolve(roll, 20000, 16000)          # short side past 15 m

    # 6 x 16 m fits: the 16 m side is the LONG one and sits inside the 25 m limit.
    assert item.resolve(roll, 6000, 16000)["panels"] == 2


def test_a_material_may_raise_its_own_welding_ceiling():
    generous = dict(BANNER, max_width_mm=3000, bleed_mm=0,
                    panel_max_long_mm=40000, panel_max_short_mm=20000)
    assert item.resolve(generous, 30000, 18000)["panels"] == 6


def test_a_safe_margin_that_eats_the_whole_job_is_refused():
    with pytest.raises(item.ItemError):
        item.resolve(BANNER, 50, 50)      # 30 mm safe per side on a 50 mm job


def test_oversize_items_are_scaled_because_a_pdf_page_stops_at_200_inches():
    """Measured: reportlab writes an 18 m page and PDFium reads it, but the DESIGNER's tools stop at
    5080 mm — which is why real production files carry names like `[skala.1do2]`.

    Uses a roll-less material on purpose. An 18 m banner on a 1.6 m roll is refused earlier and
    rightly so — that job is printed in panels, which this phase does not do.
    """
    wide = dict(BANNER, id="mesh-wide", max_width_mm=5000)
    big = item.resolve(wide, 18000, 4000)
    assert big["scale"] == 4, big["scale"]
    page_w, page_h = item.page_size_mm(big)
    assert page_w <= item.SAFE_PAGE_CEILING_MM and page_h <= item.SAFE_PAGE_CEILING_MM
    assert [n["code"] for n in big["notices"]] == ["scaled"]
    assert big["notices"][0]["values"]["scale"] == 4
    assert item.resolve(BANNER, 1000, 2000)["scale"] == 1, "small jobs must stay 1:1"


# ── Generate → identify: the round trip ─────────────────────────────────────

def test_a_queue_becomes_one_pdf_with_a_page_per_item():
    import pikepdf

    queue = [item.resolve(BANNER, 1000, 3000, label="Baner A"),
             item.resolve(FILM, 500, 500, label="Naklejka")]
    pdf_bytes = generate.build_pdf(queue)
    with pikepdf.open(_stream(pdf_bytes)) as pdf:
        assert len(pdf.pages) == 2
        sizes = [tuple(round(float(v) * 25.4 / 72) for v in (p.mediabox[2], p.mediabox[3]))
                 for p in pdf.pages]
    # With the spec panel inside the artwork, the page is exactly the brutto box.
    assert sizes == [(1040, 3040), (506, 506)], sizes


def test_the_stamp_survives_and_rebuilds_the_geometry():
    """The whole design rests on this: a returned page says which template it is."""
    original = item.resolve(BANNER, 800, 3000, label="Flaga")
    pdf_bytes = generate.build_pdf([original])

    found = identify.read_stamp(pdf_bytes)
    assert found, found.get("reason")
    geometry = identify.stamped_geometry(found["stamp"])
    assert geometry["netto_mm"] == (800, 3000)
    assert geometry["brutto_mm"] == original["brutto_mm"]
    assert geometry["safe_mm_box"] == original["safe_mm_box"]
    assert geometry["material_id"] == "banner-frontlit-510"
    assert geometry["label"] == "Flaga"


def test_each_page_of_a_mixed_queue_identifies_itself():
    """A queue may mix materials, so a document-wide stamp could not say which page is which."""
    queue = [item.resolve(BANNER, 1000, 2000), item.resolve(FILM, 400, 600)]
    pdf_bytes = generate.build_pdf(queue)
    first = identify.read_stamp(pdf_bytes, 0)
    second = identify.read_stamp(pdf_bytes, 1)
    assert first["stamp"]["material"] == "banner-frontlit-510"
    assert second["stamp"]["material"] == "folia-solwent"


def test_a_file_without_a_stamp_says_so_instead_of_guessing():
    from reportlab.pdfgen import canvas
    import io

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(200, 200))
    pdf.rect(10, 10, 50, 50)
    pdf.save()

    found = identify.read_stamp(buffer.getvalue())
    assert not found
    assert "not made from one of our templates" in found["reason"]


def test_a_non_pdf_is_an_answer_not_a_crash():
    found = identify.read_stamp(b"this is not a pdf at all")
    assert not found
    assert "could not be opened" in found["reason"]


def test_asking_for_a_page_that_is_not_there():
    pdf_bytes = generate.build_pdf([item.resolve(FILM, 300, 300)])
    assert not identify.read_stamp(pdf_bytes, page_index=5)


def test_the_page_size_of_a_returned_file_is_readable():
    pdf_bytes = generate.build_pdf([item.resolve(BANNER, 1000, 2000)])
    width, height = identify.page_size_mm(pdf_bytes)
    assert (round(width), round(height)) == (1040, 2040)


def test_an_empty_queue_is_refused():
    with pytest.raises(item.ItemError):
        generate.build_pdf([])


# ── The rules, driven by material data ──────────────────────────────────────

def _facts(page_mm, blank=(0, 0, 0, 0), intrusion=0.0, dpi=None):
    return {"page_mm": page_mm, "blank_edges_mm": blank, "safe_intrusion_mm": intrusion,
            "min_dpi": dpi}


def test_a_correct_file_passes_every_rule():
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))
    findings = rules.run(_facts((1040, 2040), dpi=150), expected, BANNER)
    assert [f["level"] for f in findings] == ["green"] * len(findings)
    assert rules.summarise(findings).startswith("OK")


def test_a_resized_page_is_a_hard_error_but_a_rotated_one_is_only_a_warning():
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))
    wrong = rules.run(_facts((900, 1800), dpi=150), expected, BANNER)
    assert wrong[0]["level"] == "red" and wrong[0]["id"] == "page_size"
    for rotated_page in ((2040, 1040),):
        rotated = rules.run(_facts(rotated_page, dpi=150), expected, BANNER)
        assert [f for f in rotated if f["id"] == "page_size"][0]["level"] == "amber", rotated_page


def test_exporting_the_artwork_WITHOUT_the_spec_strip_is_not_an_error():
    """A designer who drops the strip on export has done nothing wrong, and failing that would
    reject correct work. Only applies to materials whose spec sits in a strip."""
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER_STRIP, 1000, 2000)))
    assert expected["strip_mm"] == 22.0
    for page in ((1040, 2062), (1040, 2040)):
        findings = rules.run(_facts(page, dpi=150), expected, BANNER)
        assert [f for f in findings if f["id"] == "page_size"][0]["level"] == "green", page


def test_artwork_stopping_short_of_the_bleed_is_caught_and_graded():
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))
    mild = rules.run(_facts((1040, 2040), blank=(2, 0, 0, 0), dpi=150), expected, BANNER)
    severe = rules.run(_facts((1040, 2040), blank=(18, 0, 0, 0), dpi=150), expected, BANNER)
    assert [f for f in mild if f["id"] == "bleed_coverage"][0]["level"] == "amber"
    assert [f for f in severe if f["id"] == "bleed_coverage"][0]["level"] == "red"


def test_resolution_is_only_judged_when_the_material_asks_for_it():
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))
    no_floor = dict(BANNER, min_dpi=None)
    assert not [f for f in rules.run(_facts((1040, 2040), dpi=20), expected, no_floor)
                if f["id"] == "resolution"]
    judged = rules.run(_facts((1040, 2040), dpi=20), expected, BANNER)
    assert [f for f in judged if f["id"] == "resolution"][0]["level"] == "red"


def test_a_rule_that_throws_does_not_take_the_verdict_down(monkeypatch):
    def exploding(facts, expected):
        raise RuntimeError("boom")

    monkeypatch.setattr(rules, "RULES", (exploding, rules.check_page_size))
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))
    findings = rules.run(_facts((1040, 2040)), expected, BANNER)
    assert any(f["level"] == "info" for f in findings)
    assert any(f["id"] == "page_size" for f in findings)


def _stream(data):
    import io

    return io.BytesIO(data)


# ── Declared page boxes, read with pikepdf ──────────────────────────────────

def test_declared_boxes_are_read_when_present_and_absent_is_normal():
    """Roughly one real file in six declares any box, so absence must not read as an error."""
    pdf_bytes = generate.build_pdf([item.resolve(BANNER, 1000, 2000)])
    boxes = identify.declared_boxes_mm(pdf_bytes)
    assert "mediabox" in boxes
    assert tuple(round(v) for v in boxes["mediabox"]) == (1040, 2040)
    assert "trimbox" not in boxes, "our own template declares no trim box yet"


def test_a_trimbox_that_disagrees_with_netto_is_caught_even_when_the_page_is_right():
    """The failure this rule exists for: the sheet is exactly right, the declared cut is not, and
    the job comes back trimmed to the wrong finished size."""
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))
    good = rules.run(_facts_with_trim((1040, 2040), (1000, 2000)), expected, BANNER)
    assert [f for f in good if f["id"] == "declared_trim"][0]["level"] == "green"

    wrong = rules.run(_facts_with_trim((1040, 2040), (980, 1980)), expected, BANNER)
    trim = [f for f in wrong if f["id"] == "declared_trim"][0]
    assert trim["level"] == "red"
    # Page size still passes — which is exactly why this rule is not redundant.
    assert [f for f in wrong if f["id"] == "page_size"][0]["level"] == "green"

    rotated = rules.run(_facts_with_trim((1040, 2040), (2000, 1000)), expected, BANNER)
    assert [f for f in rotated if f["id"] == "declared_trim"][0]["level"] == "amber"


def test_no_declared_trim_means_no_finding_rather_than_a_complaint():
    expected = identify.stamped_geometry(
        generate.stamp_payload(item.resolve(BANNER, 1000, 2000)))
    findings = rules.run(_facts((1040, 2040)), expected, BANNER)
    assert not [f for f in findings if f["id"] == "declared_trim"]


def _facts_with_trim(page_mm, trim_mm):
    facts = _facts(page_mm)
    facts["declared_boxes_mm"] = {"mediabox": page_mm, "trimbox": trim_mm}
    return facts


# ── The shop owns the wording ───────────────────────────────────────────────

def test_the_engine_emits_codes_and_numbers_never_sentences():
    """The separation that makes the text customisable at all."""
    panelled = item.resolve(dict(BANNER, max_width_mm=3000), 5000, 4000)
    notice = panelled["notices"][0]
    assert set(notice) == {"code", "values"}
    assert notice["code"] == "panelled"
    assert notice["values"]["panels"] == 2


def test_a_shop_can_replace_a_message_without_touching_python():
    override = {"panelled": "Grafika w {panels} brytach — zgrzewane u nas na hali."}
    rendered = messages.render_all([messages.notice("panelled", panels=3)], override)
    assert rendered[0]["text"] == "Grafika w 3 brytach — zgrzewane u nas na hali."
    assert rendered[0]["level"] == "info"


def test_a_partly_customised_install_still_makes_whole_sentences():
    """Only one message overridden: the rest must fall back to the defaults, not vanish."""
    rendered = messages.render_all(
        [messages.notice("panelled", panels=2), messages.notice("scaled", scale=4,
                                                                netto_w="1", netto_h="2")],
        {"panelled": "custom"})
    assert rendered[0]["text"] == "custom"
    assert "1:4" in rendered[1]["text"]


def test_a_broken_custom_message_falls_back_instead_of_crashing():
    """An admin typo must not take the generator down."""
    for bad in ("{unclosed", "{nonexistent_placeholder} left alone", "{0} positional"):
        text = messages.render("panelled", {"panels": 2}, {"panelled": bad})
        assert isinstance(text, str) and text
