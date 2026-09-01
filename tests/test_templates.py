"""Importing a production template: upload, point at the cut, store it, publish it.

The flow this covers is the project's actual goal — the shop brings a real production template and the
app turns it into something a customer can design on. What is deliberately NOT tested is any automatic
choice of which outline is the cut, because there isn't one: a human picks, for the reasons measured in
`prepress/outline.py`.

Run: python3.13 -m pytest tests -q
"""
import io
import os
import sys

import pytest
from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import app as app_module  # noqa: E402
from prepress import from_template, lines, materials, outline, shape  # noqa: E402

TOKEN = "test-token-not-a-real-secret"
PT = 72 / 25.4
BANNER = {"id": "banner-frontlit-510", "name": "Banner frontlit 510 g", "bleed_mm": 20,
          "safe_mm": 30, "min_dpi": 100, "max_width_mm": 1600, "colour": "cmyk", "notes": ""}


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = str(tmp_path / "materials.json")
    monkeypatch.setattr(materials, "DEFAULT_STORE", store)
    materials.save_all([BANNER], store)
    monkeypatch.setenv(app_module.ADMIN_TOKEN_ENV, TOKEN)
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def _template_pdf(width_mm=500, height_mm=800):
    """A production template shaped like the real ones: a sheet frame, a bleed line, a cut, a safe
    area, and a small marker that must not be offered as a candidate."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width_mm * PT, height_mm * PT))
    pdf.rect(0.4 * PT, 0.4 * PT, (width_mm - 0.8) * PT, (height_mm - 0.8) * PT, stroke=1, fill=0)
    pdf.rect(20 * PT, 20 * PT, 460 * PT, 760 * PT, stroke=1, fill=0)     # bleed
    pdf.rect(30 * PT, 30 * PT, 440 * PT, 740 * PT, stroke=1, fill=0)     # cut
    pdf.rect(70 * PT, 70 * PT, 360 * PT, 660 * PT, stroke=1, fill=0)     # safe
    pdf.rect(40 * PT, 40 * PT, 30 * PT, 400 * PT, stroke=1, fill=0)      # sleeve marker
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _upload(client, data, page=0):
    return client.post("/api/admin/inspect", headers={"X-Admin-Token": TOKEN},
                       data={"file": (io.BytesIO(data), "szablon.pdf"), "page": str(page)},
                       content_type="multipart/form-data")


# ── Inspecting an upload ────────────────────────────────────────────────────

def test_an_upload_comes_back_as_a_list_of_outlines_with_a_picture(client):
    answer = _upload(client, _template_pdf())
    body = answer.get_json()
    assert answer.status_code == 200
    assert [round(v) for v in body["page_mm"]] == [500, 800]
    sizes = [(c["width_mm"], c["height_mm"]) for c in body["candidates"]]
    assert (440.0, 740.0) in sizes, sizes
    assert (360.0, 660.0) in sizes
    # The sheet frame is offered but LABELLED; the 30 x 400 marker is below the size floor.
    assert body["candidates"][0]["page_sized"] is True
    assert (30.0, 400.0) not in sizes
    assert body["preview_png"], "the admin has to see the shape, not only numbers"
    assert any(t["id"] == "cut" for t in body["line_types"]), "the dropdown is built from this"
    assert [t["id"] for t in body["line_types"] if t["defines_trim"]] == list(lines.TRIM_TYPES)
    assert all(c["svg_path"].startswith("M ") for c in body["candidates"])


def test_inspecting_is_admin_only(client):
    answer = client.post("/api/admin/inspect",
                         data={"file": (io.BytesIO(_template_pdf()), "x.pdf")},
                         content_type="multipart/form-data")
    assert answer.status_code == 403


def test_a_file_that_is_not_a_pdf_is_refused_with_a_reason(client):
    answer = _upload(client, b"this is not a pdf")
    assert answer.status_code == 400
    assert "PDF" in answer.get_json()["error"]


def test_a_page_with_nothing_large_enough_says_so(client):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(500 * PT, 800 * PT))
    pdf.rect(10 * PT, 10 * PT, 20 * PT, 20 * PT, stroke=1, fill=0)
    pdf.showPage()
    pdf.save()
    answer = _upload(client, buffer.getvalue())
    assert answer.status_code == 400
    assert "obrysu" in answer.get_json()["error"]


def test_a_later_page_can_be_inspected(client):
    """The VENTO templates keep four sizes in one file, one per page."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(500 * PT, 800 * PT))
    pdf.rect(30 * PT, 30 * PT, 440 * PT, 740 * PT, stroke=1, fill=0)
    pdf.showPage()
    pdf.setPageSize((300 * PT, 400 * PT))
    pdf.rect(20 * PT, 20 * PT, 260 * PT, 360 * PT, stroke=1, fill=0)
    pdf.showPage()
    pdf.save()
    body = _upload(client, buffer.getvalue(), page=1).get_json()
    assert [round(v) for v in body["page_mm"]] == [300, 400]
    assert body["pages"] == 2
    assert (260.0, 360.0) in [(c["width_mm"], c["height_mm"]) for c in body["candidates"]]


# ── Storing what the admin pointed at ───────────────────────────────────────

def _pick_cut(client, data=None):
    body = _upload(client, data or _template_pdf()).get_json()
    chosen = next(c for c in body["candidates"]
                  if (c["width_mm"], c["height_mm"]) == (440.0, 740.0))
    return body, chosen


def test_the_chosen_outline_is_stored_under_a_token(client):
    body, chosen = _pick_cut(client)
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Flaga testowa", "bleed_mm": 20, "safe_mm": 45,
        "material": "banner-frontlit-510", "note": "tunel zjada lewą krawędź",
        "page_mm": body["page_mm"],
        "outlines": [{**chosen["outline"], "type": "cut"}]})
    stored = answer.get_json()["template"]
    assert answer.status_code == 200
    assert stored["token"] and len(stored["token"]) >= 6
    assert stored["outlines"][0]["width_mm"] == 440.0
    assert stored["outlines"][0]["type"] == "cut"
    assert stored["outlines"][0]["segments"], "geometry must survive or nothing can be redrawn"
    assert stored["trim_mm"] == [440.0, 740.0]
    assert stored["bleed_mm"] == 20.0 and stored["safe_mm"] == 45.0


@pytest.mark.parametrize("missing, because", [
    ({"name": ""}, "nazwę"),
    ({"bleed_mm": "nie liczba"}, "liczbami"),
    ({"safe_mm": -5}, "ujemne"),
])
def test_a_half_filled_template_is_refused_with_the_reason(client, missing, because):
    body, chosen = _pick_cut(client)
    payload = {"name": "Coś", "bleed_mm": 20, "safe_mm": 30,
               "page_mm": body["page_mm"],
               "outlines": [{**chosen["outline"], "type": "cut"}]}
    payload.update(missing)
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json=payload)
    assert answer.status_code == 400
    assert because in answer.get_json()["error"]


def test_saving_without_marking_any_line_is_refused(client):
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Bez obrysu", "bleed_mm": 20, "safe_mm": 30, "page_mm": [500, 800]})
    assert answer.status_code == 400
    assert "linii" in answer.get_json()["error"]


def test_marking_lines_but_no_cut_is_a_different_refusal(client):
    """An admin who marked three folds and no cut needs to be told WHICH mistake they made."""
    body, chosen = _pick_cut(client)
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Same zagięcia", "bleed_mm": 20, "safe_mm": 30, "page_mm": body["page_mm"],
        "outlines": [{**chosen["outline"], "type": "crease"}]})
    assert answer.status_code == 400
    assert "rozmiaru gotowego" in answer.get_json()["error"]


def test_an_unknown_line_type_is_refused(client):
    body, chosen = _pick_cut(client)
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Zły typ", "bleed_mm": 20, "safe_mm": 30, "page_mm": body["page_mm"],
        "outlines": [{**chosen["outline"], "type": "laser-death-ray"}]})
    assert answer.status_code == 400
    assert "Nieznany typ" in answer.get_json()["error"]


def test_two_cut_lines_make_one_finished_size(client):
    """The case the shop found: on VENTO S the cut is the tunnel hem AND the main body, and the flag
    that leaves the shop is as wide as the hem — so the finished size is their UNION."""
    body = _upload(client, _template_pdf()).get_json()
    hem = next(c for c in body["candidates"] if (c["width_mm"], c["height_mm"]) == (440.0, 740.0))
    core = next(c for c in body["candidates"] if (c["width_mm"], c["height_mm"]) == (360.0, 660.0))
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Flaga z tunelem", "bleed_mm": 15, "safe_mm": 40, "page_mm": body["page_mm"],
        "outlines": [{**hem["outline"], "type": "cut"}, {**core["outline"], "type": "cut"}]})
    stored = answer.get_json()["template"]
    assert len(stored["outlines"]) == 2
    assert stored["trim_mm"] == [440.0, 740.0]


def test_a_drawn_safe_area_can_be_pointed_at_instead_of_typed(client):
    """When the template already draws its safe area, pointing beats typing a number — and it
    sidesteps having to offset a curved outline inward at all."""
    body = _upload(client, _template_pdf()).get_json()
    cut = next(c for c in body["candidates"] if (c["width_mm"], c["height_mm"]) == (440.0, 740.0))
    safe = next(c for c in body["candidates"] if (c["width_mm"], c["height_mm"]) == (360.0, 660.0))
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Z narysowanym safe", "bleed_mm": 20, "safe_mm": 0, "page_mm": body["page_mm"],
        "outlines": [{**cut["outline"], "type": "cut"}, {**safe["outline"], "type": "safe"}]})
    stored = answer.get_json()["template"]
    assert answer.status_code == 200
    assert [o["type"] for o in stored["outlines"]] == ["cut", "safe"]
    # A drawn safe area does NOT change the finished size.
    assert stored["trim_mm"] == [440.0, 740.0]


def test_templates_are_admin_only_to_write_and_public_to_list(client):
    assert client.post("/api/admin/templates", json={}).status_code == 403
    assert client.delete("/api/admin/templates/whatever").status_code == 403
    assert client.get("/api/templates").status_code == 200


def test_the_customer_list_shows_the_name_and_size_but_not_the_geometry(client):
    """A customer picks a template; they do not need its shape, and somebody's die outline is not a
    thing to publish for free."""
    body, chosen = _pick_cut(client)
    client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Flaga testowa", "bleed_mm": 20, "safe_mm": 45,
        "page_mm": body["page_mm"],
        "outlines": [{**chosen["outline"], "type": "cut"}]})
    published = client.get("/api/templates").get_json()["templates"]
    assert len(published) == 1
    assert published[0]["name"] == "Flaga testowa"
    assert published[0]["trim_mm"] == [440.0, 740.0]
    assert published[0]["lines"] == ["cut"]
    assert "segments" not in str(published[0])
    assert "outlines" not in published[0]


def test_a_template_can_be_removed_and_a_missing_one_is_a_404(client):
    body, chosen = _pick_cut(client)
    token = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Do usunięcia", "bleed_mm": 10, "safe_mm": 10,
        "page_mm": body["page_mm"],
        "outlines": [{**chosen["outline"], "type": "cut"}]}).get_json()["template"]["token"]
    assert client.delete(f"/api/admin/templates/{token}",
                         headers={"X-Admin-Token": TOKEN}).status_code == 200
    assert client.get("/api/templates").get_json()["templates"] == []
    assert client.delete(f"/api/admin/templates/{token}",
                         headers={"X-Admin-Token": TOKEN}).status_code == 404


def _save(client, name, **extra):
    body, chosen = _pick_cut(client)
    payload = {"name": name, "bleed_mm": 10, "safe_mm": 10, "page_mm": body["page_mm"],
               "outlines": [{**chosen["outline"], "type": "cut"}], **extra}
    return client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN},
                       json=payload).get_json()["template"]


def test_a_sewn_tunnel_is_taken_off_BEFORE_the_margin_is_measured(client):
    """The Vento Regular case, and the correction that followed it.

    A mast sleeve is not a cautious margin — it is material that gets folded and SEWN, after which
    it is gone. So the safe area starts at the sewn edge and the shop's margin is measured from
    THERE. Modelling the two as one number put the safe line exactly on the seam.
    """
    body, _chosen = _pick_cut(client)
    by_width = {round(c["width_mm"], 1): c for c in body["candidates"]}
    saved = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Z tunelem", "bleed_mm": 10, "safe_mm": 30,
        "sewn_sides_mm": {"left": 110, "top": 110, "right": 0, "bottom": 0},
        "page_mm": body["page_mm"],
        "outlines": [{**by_width[440.0]["outline"], "type": "cut"}]}).get_json()["template"]
    assert saved["sewn_sides_mm"] == {"left": 110.0, "top": 110.0, "right": 0.0, "bottom": 0.0}

    drawing, notes = from_template.derive(saved)
    safe = drawing["safe"][0]
    # 440 wide, less (110 sewn + 30 margin) on the left and 30 on the right.
    assert safe["width_mm"] == pytest.approx(270.0, abs=0.5)
    assert safe["height_mm"] == pytest.approx(570.0, abs=0.5)
    # Sitting 140 mm in from the cut on the left, not centred on it — that is what a sleeve does.
    assert safe["origin_mm"][0] == pytest.approx(30.0 + 140.0, abs=0.5)
    assert any("wykończenia" in note and "lewa 110" in note for note in notes)


def test_the_margin_alone_still_governs_the_sides_that_are_not_sewn(client):
    """Changing the shared margin has to move all four sides, which is the point of adding rather
    than replacing: a shop that raises 30 to 40 should not have to redo four sums."""
    body, _chosen = _pick_cut(client)
    by_width = {round(c["width_mm"], 1): c for c in body["candidates"]}
    saved = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Tunel plus większy margines", "bleed_mm": 10, "safe_mm": 40,
        "sewn_sides_mm": {"left": 110, "top": 0, "right": 0, "bottom": 0},
        "page_mm": body["page_mm"],
        "outlines": [{**by_width[440.0]["outline"], "type": "cut"}]}).get_json()["template"]
    safe = from_template.derive(saved)[0]["safe"][0]
    # 440 less (110 + 40) less 40.
    assert safe["width_mm"] == pytest.approx(250.0, abs=0.5)
    assert safe["height_mm"] == pytest.approx(660.0, abs=0.5)


def test_nothing_sewn_is_stored_as_none(client):
    """Four zeroes say nothing, and a store full of them makes every later read interpret noise."""
    body, _chosen = _pick_cut(client)
    by_width = {round(c["width_mm"], 1): c for c in body["candidates"]}
    saved = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Nic nie zaszyte", "bleed_mm": 10, "safe_mm": 30,
        "sewn_sides_mm": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "page_mm": body["page_mm"],
        "outlines": [{**by_width[440.0]["outline"], "type": "cut"}]}).get_json()["template"]
    assert saved["sewn_sides_mm"] is None


def test_a_sewn_allowance_can_be_added_to_an_already_saved_template(client):
    """Three Regulars were imported before this existed, and re-importing them is the thing the edit
    path exists to avoid."""
    stored = _save(client, "Regular bez tunelu")
    answer = client.patch(f"/api/admin/templates/{stored['token']}",
                          headers={"X-Admin-Token": TOKEN},
                          json={"sewn_sides_mm": {"left": 110, "top": 110,
                                                  "right": 0, "bottom": 0}})
    assert answer.status_code == 200
    assert materials.get_template(stored["token"])["sewn_sides_mm"]["left"] == 110.0
    client.patch(f"/api/admin/templates/{stored['token']}", headers={"X-Admin-Token": TOKEN},
                 json={"sewn_sides_mm": None})
    assert materials.get_template(stored["token"])["sewn_sides_mm"] is None


def test_an_allowance_bigger_than_the_shape_is_refused_rather_than_drawn(client):
    body, _chosen = _pick_cut(client)
    by_width = {round(c["width_mm"], 1): c for c in body["candidates"]}
    saved = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Za duża zakładka", "bleed_mm": 10, "safe_mm": 30,
        "sewn_sides_mm": {"left": 300, "top": 0, "right": 300, "bottom": 0},
        "page_mm": body["page_mm"],
        "outlines": [{**by_width[440.0]["outline"], "type": "cut"}]}).get_json()["template"]
    with pytest.raises(from_template.TemplateError):
        from_template.derive(saved)


def test_a_safe_line_drawn_outside_the_cut_is_refused(client):
    """From a real import: the bleed line was marked „obszar bezpieczny", so a 460 mm box became the
    safe area of a 440 mm flag and the generator invented a bleed to replace the one it had lost."""
    body, _chosen = _pick_cut(client)
    by_width = {round(c["width_mm"], 1): c for c in body["candidates"]}
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Spad wzięty za bezpieczny", "bleed_mm": 10, "safe_mm": 10,
        "page_mm": body["page_mm"],
        "outlines": [{**by_width[460.0]["outline"], "type": "safe"},
                     {**by_width[440.0]["outline"], "type": "cut"}]})
    assert answer.status_code == 400
    assert "POZA linią cięcia" in answer.get_json()["error"]
    assert materials.load_templates() == []


def test_a_safe_line_drawn_inside_the_cut_is_accepted(client):
    """The guard must not fire on the correct case, which is the whole point of a template that
    already carries its own safe area."""
    body, _chosen = _pick_cut(client)
    by_width = {round(c["width_mm"], 1): c for c in body["candidates"]}
    answer = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Bezpieczny w środku", "bleed_mm": 10, "safe_mm": 10,
        "page_mm": body["page_mm"],
        "outlines": [{**by_width[440.0]["outline"], "type": "cut"},
                     {**by_width[360.0]["outline"], "type": "safe"}]})
    assert answer.status_code == 200


def test_templates_can_be_dragged_into_a_new_order(client):
    """The order is not decoration — this list IS the customer's picker."""
    first = _save(client, "Pierwszy")
    second = _save(client, "Drugi")
    third = _save(client, "Trzeci")
    answer = client.put("/api/admin/templates/order", headers={"X-Admin-Token": TOKEN},
                        json={"order": [third["token"], first["token"], second["token"]]})
    assert answer.status_code == 200
    assert [t["name"] for t in materials.load_templates()] == ["Trzeci", "Pierwszy", "Drugi"]
    # And the customer surface serves that order, not the insertion order.
    assert [t["name"] for t in client.get("/api/templates").get_json()["templates"]] == [
        "Trzeci", "Pierwszy", "Drugi"]


def test_reordering_never_drops_a_template_the_browser_did_not_know_about(client):
    """A tab open since before a template was added would send a list missing it. Filtering by that
    list would delete a template as a side effect of a sort."""
    first = _save(client, "Pierwszy")
    second = _save(client, "Drugi")
    added_later = _save(client, "Dodany później")
    client.put("/api/admin/templates/order", headers={"X-Admin-Token": TOKEN},
               json={"order": [second["token"], first["token"]]})
    names = [t["name"] for t in materials.load_templates()]
    assert names == ["Drugi", "Pierwszy", "Dodany później"]
    assert added_later["token"] in [t["token"] for t in materials.load_templates()]


def test_reordering_ignores_a_token_that_does_not_exist(client):
    first = _save(client, "Pierwszy")
    client.put("/api/admin/templates/order", headers={"X-Admin-Token": TOKEN},
               json={"order": ["nie-ma-takiego", first["token"]]})
    assert [t["name"] for t in materials.load_templates()] == ["Pierwszy"]


def test_reordering_without_a_list_is_refused_and_needs_the_token(client):
    _save(client, "Pierwszy")
    assert client.put("/api/admin/templates/order", headers={"X-Admin-Token": TOKEN},
                      json={}).status_code == 400
    # 403, matching every other admin route in this app — the token is missing, not merely wrong.
    assert client.put("/api/admin/templates/order", json={"order": []}).status_code == 403


def test_materials_can_be_dragged_too(client):
    """The material list drives the picker on the customer's own page."""
    for name in ("Drugi materiał", "Trzeci materiał"):
        client.post("/api/admin/materials", headers={"X-Admin-Token": TOKEN},
                    json={"material": {**BANNER, "id": name.split()[0].lower(), "name": name}})
    order = [m["id"] for m in materials.load()][::-1]
    answer = client.put("/api/admin/materials/order", headers={"X-Admin-Token": TOKEN},
                        json={"order": order})
    assert answer.status_code == 200
    assert [m["id"] for m in materials.load()] == order


def test_a_saved_template_can_be_renamed_without_touching_its_geometry(client):
    """The case that prompted this: a template saved under an auto-generated name, spotted later."""
    stored = _save(client, "GqQ5Lo36")
    before = materials.get_template(stored["token"])

    answer = client.patch(f"/api/admin/templates/{stored['token']}",
                          headers={"X-Admin-Token": TOKEN},
                          json={"name": "Flaga S Play B jednostronna"})
    assert answer.status_code == 200
    after = materials.get_template(stored["token"])
    assert after["name"] == "Flaga S Play B jednostronna"
    # The token stays: it is already inside every template PDF handed out, so re-minting it would
    # orphan them.
    assert after["token"] == before["token"]
    assert after["outlines"] == before["outlines"]
    assert after["page_mm"] == before["page_mm"]
    assert after["trim_mm"] == before["trim_mm"]


def test_an_edit_keeps_the_template_where_it_was_in_the_list(client):
    """Correcting names across sixteen templates should not shuffle them — `upsert_template`
    appends, which is why editing does not go through it."""
    first = _save(client, "Pierwszy")
    _save(client, "Drugi")
    third = _save(client, "Trzeci")
    client.patch(f"/api/admin/templates/{first['token']}", headers={"X-Admin-Token": TOKEN},
                 json={"name": "Pierwszy poprawiony"})
    order = [t["name"] for t in materials.load_templates()]
    assert order == ["Pierwszy poprawiony", "Drugi", "Trzeci"]
    assert third["token"] in [t["token"] for t in materials.load_templates()]


def test_an_edit_cannot_reach_the_outlines_even_if_asked(client):
    """Roles are judged against a rendered page. This request has no page, so the field is ignored
    rather than trusted."""
    stored = _save(client, "Nietykalny")
    before = materials.get_template(stored["token"])
    client.patch(f"/api/admin/templates/{stored['token']}", headers={"X-Admin-Token": TOKEN},
                 json={"name": "Nietykalny", "outlines": [], "page_mm": [1.0, 1.0],
                       "token": "podmieniony"})
    after = materials.get_template(stored["token"])
    assert after["outlines"] == before["outlines"]
    assert after["page_mm"] == before["page_mm"]
    assert materials.get_template("podmieniony") is None


def test_an_edit_refuses_an_empty_name_and_a_negative_margin(client):
    stored = _save(client, "Zostaje")
    for bad in ({"name": "   "}, {"safe_mm": -5}, {"bleed_mm": "nie liczba"}):
        assert client.patch(f"/api/admin/templates/{stored['token']}",
                            headers={"X-Admin-Token": TOKEN}, json=bad).status_code == 400
    assert materials.get_template(stored["token"])["name"] == "Zostaje"


def test_editing_a_template_that_does_not_exist_is_a_404(client):
    assert client.patch("/api/admin/templates/nie-ma-takiego",
                        headers={"X-Admin-Token": TOKEN},
                        json={"name": "cokolwiek"}).status_code == 404


def test_templates_survive_a_material_save_and_vice_versa(client):
    """Four blocks in one file now. Each save must preserve the other three — the same bug that
    would otherwise drop a shop's wording when it edits a material."""
    headers = {"X-Admin-Token": TOKEN}
    body, chosen = _pick_cut(client)
    client.post("/api/admin/templates", headers=headers, json={
        "name": "Zostaje", "bleed_mm": 20, "safe_mm": 30,
        "page_mm": body["page_mm"],
        "outlines": [{**chosen["outline"], "type": "cut"}]})
    client.post("/api/admin/messages", headers=headers, json={"messages": {"panelled": "zostaje"}})
    client.post("/api/admin/rules", headers=headers, json={"rules": {"fonts": "off"}})
    client.post("/api/admin/materials", headers=headers, json={"material": dict(BANNER, safe_mm=44)})

    assert len(client.get("/api/templates").get_json()["templates"]) == 1
    assert client.get("/api/admin/messages", headers=headers).get_json()["overrides"] == {
        "panelled": "zostaje"}
    assert client.get("/api/admin/rules", headers=headers).get_json()["overrides"] == {"fonts": "off"}
    assert client.get("/api/materials").get_json()["materials"][0]["safe_mm"] == 44


# ── The geometry that travels between them ──────────────────────────────────

def test_the_svg_overlay_flips_y_because_pdf_and_svg_disagree():
    """Getting this wrong mirrors the flag, and a mirrored flag looks plausible."""
    entry = {"start": (10.0, 20.0), "segments": [("l", (30.0, 700.0))], "closed": True,
             "width_mm": 20.0, "height_mm": 680.0, "origin_mm": (10.0, 20.0)}
    path = shape.to_svg_path(entry, page_height_mm=800.0)
    assert path == "M 10.0,780.0 L 30.0,100.0 Z"


def test_an_outline_survives_being_stored_and_read_back():
    data = _template_pdf()
    chosen = next(c for c in outline.candidates(data)
                  if (c["width_mm"], c["height_mm"]) == (440.0, 740.0))
    restored = shape.deserialise(shape.serialise(chosen))
    assert restored["width_mm"] == chosen["width_mm"]
    assert len(restored["segments"]) == len(chosen["segments"])
    assert restored["start"] == pytest.approx(chosen["start"], abs=0.001)


def test_a_stored_outline_can_be_drawn_again():
    """The point of keeping exact cubics: what was measured is what gets redrawn."""
    data = _template_pdf()
    chosen = next(c for c in outline.candidates(data)
                  if (c["width_mm"], c["height_mm"]) == (440.0, 740.0))
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(500 * PT, 800 * PT))
    shape.draw_on_canvas(pdf, shape.deserialise(shape.serialise(chosen)))
    pdf.showPage()
    pdf.save()
    redrawn = outline.candidates(buffer.getvalue())
    assert [(c["width_mm"], c["height_mm"]) for c in redrawn] == [(440.0, 740.0)]


# ── Where a spec panel may be drawn on a SHAPED template ────────────────────

def test_the_panel_home_is_a_rectangle_that_actually_fits_inside():
    """A bounding box is the wrong home on anything but a rectangle.

    A drop flag is a teardrop: most of its bounding box is off the fabric, so a panel centred there
    hangs over the cut line and off the flag (shop rule, 2026-08-31). The home has to be a rectangle
    that fits INSIDE the shape.
    """
    from prepress import offset

    # A right triangle: half its bounding box is outside it, which is the drop flag in miniature.
    triangle = [[(0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (0.0, 0.0)]]
    box = offset.largest_inscribed_box(triangle)
    assert box is not None
    # Every corner of the answer, pulled a hair inwards, must be on the shape.
    x0, y0, x1, y1 = box
    nudge = 0.5
    for point in ((x0 + nudge, y0 + nudge), (x1 - nudge, y0 + nudge),
                  (x0 + nudge, y1 - nudge), (x1 - nudge, y1 - nudge)):
        assert offset.all_inside(triangle, point), f"{point} is off the shape"
    # And it must be worth having: a degenerate sliver would satisfy the test above.
    assert (x1 - x0) * (y1 - y0) > 100 * 100 * 0.2


def test_a_rectangle_keeps_its_whole_area_as_the_home():
    """The fix must not shrink the ordinary case: on a rectangle the inscribed box IS the box."""
    from prepress import offset

    square = [[(0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0), (0.0, 0.0)]]
    x0, y0, x1, y1 = offset.largest_inscribed_box(square)
    assert (x1 - x0) > 200 * 0.95 and (y1 - y0) > 100 * 0.95
