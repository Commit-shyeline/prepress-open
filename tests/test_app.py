"""The web surfaces: the customer generator is open, the admin panel is not.

Runs through Flask's test client — no server is started.

Run: python3.13 -m pytest tests -q
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import app as app_module  # noqa: E402
from prepress import identify, materials  # noqa: E402

TOKEN = "test-token-not-a-real-secret"
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


# ── The optional session gate ───────────────────────────────────────────────
# A shop can publish this behind its own login. Unset, none of it exists — which is the state every
# other test in this file runs under, so these tests configure it explicitly.

SESSION_SECRET = "session-secret-not-a-real-one"


def _bearer(subject="1234567890", secret=SESSION_SECRET, **claims):
    import jwt
    return {"Authorization": "Bearer " + jwt.encode({"nip": subject, **claims},
                                                    secret, algorithm="HS256")}


def test_without_a_secret_the_app_is_open(client):
    """The standalone default: no gate configured, so nothing asks for a token."""
    answer = client.get("/api/session").get_json()
    assert answer["gated"] is False
    assert answer["authenticated"] is True


def test_a_gated_check_refuses_an_anonymous_upload(client, monkeypatch):
    monkeypatch.setenv(app_module.SESSION_SECRET_ENV, SESSION_SECRET)
    answer = client.post("/api/check", data={"file": (io.BytesIO(b"%PDF-1.7"), "x.pdf")},
                         content_type="multipart/form-data")
    assert answer.status_code == 401
    assert client.get("/api/session").get_json()["authenticated"] is False


def test_a_gated_check_accepts_a_signed_token(client, monkeypatch):
    monkeypatch.setenv(app_module.SESSION_SECRET_ENV, SESSION_SECRET)
    answer = client.post("/api/check", data={"file": (io.BytesIO(b"%PDF-1.7 not ours"), "x.pdf")},
                         content_type="multipart/form-data", headers=_bearer())
    # Not a verdict about the file — only that the gate let it through to be judged.
    assert answer.status_code == 200
    assert client.get("/api/session", headers=_bearer()).get_json()["authenticated"] is True


def test_a_token_signed_with_another_secret_is_refused(client, monkeypatch):
    """The whole point of a signature: a token minted by somebody else does not open this."""
    monkeypatch.setenv(app_module.SESSION_SECRET_ENV, SESSION_SECRET)
    answer = client.get("/api/session", headers=_bearer(secret="a-different-secret"))
    assert answer.get_json()["authenticated"] is False


def test_a_reserved_subject_is_not_a_customer(client, monkeypatch):
    """The issuing app keeps some subjects for itself; one of those is not a logged-in customer."""
    monkeypatch.setenv(app_module.SESSION_SECRET_ENV, SESSION_SECRET)
    answer = client.get("/api/session", headers=_bearer(subject="__status_admin__"))
    assert answer.get_json()["authenticated"] is False


def test_an_expired_token_is_refused(client, monkeypatch):
    monkeypatch.setenv(app_module.SESSION_SECRET_ENV, SESSION_SECRET)
    answer = client.get("/api/session", headers=_bearer(exp=1))
    assert answer.get_json()["authenticated"] is False


def test_the_pages_stay_reachable_behind_the_gate(client, monkeypatch):
    """A visitor who followed a link meets the page and is told to log in — not a bare 401."""
    monkeypatch.setenv(app_module.SESSION_SECRET_ENV, SESSION_SECRET)
    assert client.get("/plik").status_code == 200
    assert client.get("/").status_code == 200


# ── Mounted under a path ────────────────────────────────────────────────────

def test_a_mounted_page_prefixes_its_own_links(client):
    """Behind a proxy the app is not at the root, and every link it writes has to say so or it
    walks out of the mount and hits whatever else lives on that host."""
    body = client.get("/plik", headers={"X-Forwarded-Prefix": "/sprawdz"}).get_data(as_text=True)
    assert "/sprawdz/api/check" in body
    assert '"/api/check"' not in body and "'/api/check'" not in body


def test_unmounted_pages_carry_no_prefix(client):
    body = client.get("/plik").get_data(as_text=True)
    assert "/api/check" in body and "//api/check" not in body


# ── The customer surface needs no login ─────────────────────────────────────

def test_the_generator_page_and_material_list_are_open(client):
    assert client.get("/").status_code == 200
    body = client.get("/api/materials").get_json()
    assert body["materials"][0]["id"] == "banner-frontlit-510"
    # The rules are public on purpose: a customer choosing a material should see what it demands.
    assert body["materials"][0]["bleed_mm"] == 20


def test_resolve_reports_the_boxes_before_anything_is_downloaded(client):
    answer = client.post("/api/resolve", json={"material": "banner-frontlit-510",
                                               "width": "100", "height": "300", "unit": "cm"})
    resolved = answer.get_json()["item"]
    assert resolved["netto_mm"] == [1000, 3000]
    assert resolved["brutto_mm"] == [1040, 3040]
    assert resolved["safe_mm_box"] == [940, 2940]


@pytest.mark.parametrize("payload, code", [
    ({"material": "nope", "width": "1", "height": "1", "unit": "m"}, "unknown_material"),
    ({"material": "banner-frontlit-510", "width": "", "height": "300", "unit": "cm"},
     "dimensions_required"),
    ({"material": "banner-frontlit-510", "width": "abc", "height": "3", "unit": "m"},
     "not_a_number"),
    ({"material": "banner-frontlit-510", "width": "3000", "height": "2000", "unit": "cm"},
     "too_big_to_panel"),
])
def test_bad_input_is_refused_with_a_CODE_so_the_shop_owns_the_wording(client, payload, code):
    answer = client.post("/api/resolve", json=payload)
    assert answer.status_code == 400
    body = answer.get_json()
    assert body["code"] == code
    assert body["error"], "a code with no rendered text would leave the UI blank"


def test_the_queue_downloads_one_pdf_with_a_page_per_item(client):
    answer = client.post("/api/template", json={"items": [
        {"material": "banner-frontlit-510", "width": "100", "height": "300", "unit": "cm",
         "label": "Baner A"},
        {"material": "banner-frontlit-510", "width": "60", "height": "200", "unit": "cm"},
    ]})
    assert answer.status_code == 200
    assert answer.mimetype == "application/pdf"
    pdf_bytes = answer.data
    # Every page must identify itself, which is the point of the whole design.
    first = identify.read_stamp(pdf_bytes, 0)
    second = identify.read_stamp(pdf_bytes, 1)
    assert first and second
    assert first["stamp"]["netto_mm"] == [1000, 3000]
    assert first["stamp"]["label"] == "Baner A"
    assert second["stamp"]["netto_mm"] == [600, 2000]


def test_an_empty_or_oversized_queue_is_refused(client):
    assert client.post("/api/template", json={"items": []}).status_code == 400
    too_many = [{"material": "banner-frontlit-510", "width": "10", "height": "10", "unit": "cm"}] * 60
    answer = client.post("/api/template", json={"items": too_many})
    assert answer.status_code == 400
    assert "At most" in answer.get_json()["error"]


# ── The round trip through the HTTP surface ─────────────────────────────────

def test_a_generated_template_comes_back_recognised(client):
    generated = client.post("/api/template", json={"items": [
        {"material": "banner-frontlit-510", "width": "100", "height": "300", "unit": "cm"}]}).data

    answer = client.post("/api/check", data={"file": (io.BytesIO(generated), "returned.pdf")},
                         content_type="multipart/form-data")
    body = answer.get_json()
    assert body["recognised"] is True
    assert body["expected"]["netto_mm"] == [1000, 3000]
    assert body["material"]["id"] == "banner-frontlit-510"
    # The unchanged template comes back as exactly what it is: the bare template — recognised,
    # with nothing to judge yet.
    assert body["bare_template"] is True
    assert body["checks"] == []


def test_a_foreign_file_is_reported_as_unrecognised_not_guessed_at(client):
    answer = client.post("/api/check",
                         data={"file": (io.BytesIO(b"%PDF-1.7\nnot ours"), "x.pdf")},
                         content_type="multipart/form-data")
    body = answer.get_json()
    assert body["recognised"] is False
    assert body["reason"]


def test_check_without_a_file_is_a_400(client):
    assert client.post("/api/check", data={}, content_type="multipart/form-data").status_code == 400


def test_a_returned_blank_template_is_caught_not_passed(client):
    """Sending our own empty template back must never read as a pass. It comes back recognised as
    the BARE template — guides still in, no artwork — and the summary says design-first, never the
    OK line a blank file could ride into production on."""
    generated = client.post("/api/template", json={"items": [
        {"material": "banner-frontlit-510", "width": "100", "height": "300", "unit": "cm"}]}).data
    body = client.post("/api/check", data={"file": (io.BytesIO(generated), "r.pdf")},
                       content_type="multipart/form-data").get_json()
    assert body["bare_template"] is True
    assert body["measured"]["guides_present"] is True
    assert "szablon" in body["summary"].lower()
    assert not body["summary"].lower().startswith("ok")


# ── The admin surface is gated ──────────────────────────────────────────────

def test_admin_writes_are_refused_without_the_token(client):
    answer = client.post("/api/admin/materials", json={"material": BANNER})
    assert answer.status_code == 403
    assert client.delete("/api/admin/materials/banner-frontlit-510").status_code == 403


def test_admin_writes_are_refused_with_the_wrong_token(client):
    answer = client.post("/api/admin/materials", json={"material": BANNER},
                         headers={"X-Admin-Token": "wrong"})
    assert answer.status_code == 403


def test_the_panel_is_DISABLED_when_no_token_is_configured(client, monkeypatch):
    """Fail closed. An unauthenticated panel that edits print rules must not be the fallback."""
    monkeypatch.delenv(app_module.ADMIN_TOKEN_ENV, raising=False)
    answer = client.post("/api/admin/materials", json={"material": BANNER},
                         headers={"X-Admin-Token": "anything"})
    assert answer.status_code == 503
    assert "disabled" in answer.get_json()["error"]
    # And the page says so rather than showing a form that cannot work.
    assert b"Panel wy" in client.get("/admin").data


def test_an_admin_can_add_edit_and_remove_a_material(client):
    headers = {"X-Admin-Token": TOKEN}
    added = client.post("/api/admin/materials", headers=headers, json={"material": {
        "id": "folia-solwent", "name": "Folia solwentowa", "bleed_mm": 3, "safe_mm": 3,
        "min_dpi": 300, "colour": "cmyk"}})
    assert added.status_code == 200
    assert len(added.get_json()["materials"]) == 2

    edited = client.post("/api/admin/materials", headers=headers, json={"material": {
        "id": "folia-solwent", "name": "Folia solwentowa PREMIUM", "bleed_mm": 5, "safe_mm": 4,
        "colour": "cmyk"}})
    assert edited.get_json()["material"]["bleed_mm"] == 5
    assert len(edited.get_json()["materials"]) == 2, "editing duplicated the material"

    removed = client.delete("/api/admin/materials/folia-solwent", headers=headers)
    assert [m["id"] for m in removed.get_json()["materials"]] == ["banner-frontlit-510"]
    assert client.delete("/api/admin/materials/folia-solwent", headers=headers).status_code == 404


def test_an_invalid_material_is_refused_with_the_reason(client):
    answer = client.post("/api/admin/materials", headers={"X-Admin-Token": TOKEN},
                         json={"material": {"id": "Bad Id", "name": "x", "bleed_mm": 3,
                                            "safe_mm": 3}})
    assert answer.status_code == 400
    assert "Id must be" in answer.get_json()["error"]


def test_an_admin_edit_is_visible_to_the_customer_surface_immediately(client):
    """No restart: the store reloads on mtime, which is the reason rules live in a file."""
    client.post("/api/admin/materials", headers={"X-Admin-Token": TOKEN}, json={"material": dict(
        BANNER, bleed_mm=35)})
    resolved = client.post("/api/resolve", json={"material": "banner-frontlit-510",
                                                 "width": "100", "height": "100",
                                                 "unit": "cm"}).get_json()["item"]
    assert resolved["brutto_mm"] == [1070, 1070]


def test_health_reports_whether_the_admin_panel_is_usable(client):
    body = client.get("/health").get_json()
    assert body["ok"] is True and body["materials"] == 1 and body["admin_configured"] is True


# ── The shop edits its own customer-facing text ──────────────────────────────

def test_an_admin_can_read_and_replace_the_customer_messages(client):
    headers = {"X-Admin-Token": TOKEN}
    listed = client.get("/api/admin/messages", headers=headers).get_json()
    assert "panelled" in listed["defaults"]
    assert "panelled" in listed["info_codes"], "panelling is advice, not an error"
    assert listed["overrides"] == {}

    saved = client.post("/api/admin/messages", headers=headers, json={
        "messages": {"panelled": "Drukujemy w {panels} brytach i zgrzewamy."}})
    assert saved.get_json()["overrides"]["panelled"].startswith("Drukujemy")


def test_the_customer_sees_the_shop_wording_not_ours(client):
    """The whole point of the message layer."""
    client.post("/api/admin/messages", headers={"X-Admin-Token": TOKEN}, json={
        "messages": {"panelled": "SKLEP: {panels} bryty."}})
    # 4 x 4 m on a 1600 mm roll: neither way round fits, so it is panelled.
    answer = client.post("/api/resolve", json={"material": "banner-frontlit-510",
                                               "width": "400", "height": "400", "unit": "cm"})
    notices = answer.get_json()["notices"]
    assert [n["code"] for n in notices] == ["panelled"]
    assert notices[0]["text"].startswith("SKLEP:")
    assert notices[0]["level"] == "info"


def test_editing_messages_does_not_wipe_the_materials_and_vice_versa(client):
    """Both live in one file, so each save must preserve the other half."""
    headers = {"X-Admin-Token": TOKEN}
    client.post("/api/admin/messages", headers=headers, json={"messages": {"panelled": "keep me"}})
    client.post("/api/admin/materials", headers=headers, json={"material": dict(BANNER, safe_mm=44)})

    assert client.get("/api/admin/messages", headers=headers).get_json()["overrides"] == {
        "panelled": "keep me"}
    assert client.get("/api/materials").get_json()["materials"][0]["safe_mm"] == 44


def test_fitting_the_roll_one_way_says_nothing_through_the_api(client):
    """1.8 x 1.0 m on a 1.6 m roll: rotated it fits, and the customer hears nothing about it."""
    answer = client.post("/api/resolve", json={"material": "banner-frontlit-510",
                                               "width": "180", "height": "100", "unit": "cm"})
    body = answer.get_json()
    assert body["notices"] == []
    assert body["item"]["panels"] == 1


def test_messages_are_admin_only(client):
    assert client.get("/api/admin/messages").status_code == 403
    assert client.post("/api/admin/messages", json={"messages": {}}).status_code == 403


# ── The shop sets how serious each rule is ───────────────────────────────────

def test_an_admin_can_read_the_rules_with_the_wording_each_one_uses(client):
    """The panel labels a severity control with the sentence the customer would get, so there is no
    second list of human names to drift out of date."""
    listed = client.get("/api/admin/rules", headers={"X-Admin-Token": TOKEN}).get_json()
    assert "colour_mode" in listed["rule_ids"]
    assert "off" in listed["levels"]
    assert "RGB" in listed["labels"]["colour_mode"]
    assert listed["overrides"] == {}


def test_a_reworded_rule_relabels_its_own_control(client):
    headers = {"X-Admin-Token": TOKEN}
    client.post("/api/admin/messages", headers=headers,
                json={"messages": {"check.colour_mode.rgb": "Plik jest w RGB, poprawimy."}})
    listed = client.get("/api/admin/rules", headers=headers).get_json()
    assert listed["labels"]["colour_mode"] == "Plik jest w RGB, poprawimy."


def test_severities_round_trip_and_an_unknown_level_is_dropped(client):
    headers = {"X-Admin-Token": TOKEN}
    saved = client.post("/api/admin/rules", headers=headers,
                        json={"rules": {"fonts": "off", "colour_mode": "chartreuse"}})
    assert saved.get_json()["overrides"] == {"fonts": "off"}
    assert client.get("/api/admin/rules",
                      headers=headers).get_json()["overrides"] == {"fonts": "off"}


def _painted_over(template_pdf):
    """The template with a full-page rectangle of "artwork" inked over it — so the checker sees a
    design (not the bare template) while the template's own fonts and stamp stay in the file."""
    import pikepdf
    from reportlab.pdfgen import canvas as rl_canvas

    source = pikepdf.open(io.BytesIO(template_pdf))
    page = source.pages[0]
    width, height = float(page.mediabox[2]), float(page.mediabox[3])
    ink = io.BytesIO()
    painter = rl_canvas.Canvas(ink, pagesize=(width, height))
    painter.setFillColorRGB(0.2, 0.4, 0.8)
    painter.rect(0, 0, width, height, fill=1, stroke=0)
    painter.save()
    overlay = pikepdf.open(ink)                  # kept alive: add_overlay borrows the page
    page.add_overlay(overlay.pages[0])
    out = io.BytesIO()
    source.save(out)
    return out.getvalue()


def test_a_silenced_rule_stops_appearing_in_a_real_check(client):
    """End to end: a designed-on template genuinely trips the font rule, and the shop turns it
    off. Painted over first — a BARE template short-circuits to no findings at all."""
    pdf = _painted_over(client.post("/api/template", json={"items": [
        {"material": "banner-frontlit-510", "width": "100", "height": "200", "unit": "cm"}]}).data)

    def font_findings():
        answer = client.post("/api/check", data={"file": (io.BytesIO(pdf), "szablon.pdf")},
                             content_type="multipart/form-data").get_json()
        return [c for c in answer["checks"] if c["id"] == "fonts"]

    assert font_findings()[0]["level"] == "amber"
    client.post("/api/admin/rules", headers={"X-Admin-Token": TOKEN},
                json={"rules": {"fonts": "off"}})
    assert font_findings() == []


def test_a_file_rebuilt_by_a_design_app_is_recognised_by_its_printed_token(client):
    """Illustrator/Affinity exports rebuild the PDF: the page-dictionary stamp dies, the drawn
    content survives. Stripping the stamp key simulates that export — the checker must still
    recognise the file from the printed `prepress-open:<token>` line in the bleed corner.
    Printed identity is a STORED-template feature (quick-generator sheets have no token), so the
    test stores one first."""
    import pikepdf

    from prepress import from_template, materials
    from prepress.generate import STAMP_KEY

    template = {"token": "PrintedT1", "name": "Baner testowy 100x300",
                "page_mm": [1006.0, 3006.0], "trim_mm": [1000.0, 3000.0],
                "bleed_mm": 3.0, "safe_mm": 30.0, "sides": 1,
                "material": "banner-frontlit-510", "source_name": "t.pdf", "note": "",
                "outlines": [{"closed": True, "width_mm": 1000, "height_mm": 3000,
                              "origin_mm": [3, 3], "start": [3, 3],
                              "segments": [["l", 1003, 3], ["l", 1003, 3003], ["l", 3, 3003]],
                              "type": "cut", "safe_base": False}]}
    materials.upsert_template(template)
    generated = from_template.build_pdf(materials.get_template("PrintedT1"))

    stripped = pikepdf.open(io.BytesIO(generated))
    for page in stripped.pages:
        del page.obj[pikepdf.Name(STAMP_KEY)]
    out = io.BytesIO()
    stripped.save(out)

    body = client.post("/api/check", data={"file": (io.BytesIO(out.getvalue()), "eksport.pdf")},
                       content_type="multipart/form-data").get_json()
    assert body["recognised"] is True, body
    assert body["bare_template"] is True            # still the bare template, just re-exported
    assert not body.get("assumed_template")         # printed identity is exact, not a human guess


def test_the_back_of_a_pair_is_not_asked_for_a_wykrojnik(client):
    """The die line travels with the FRONT file of a two-file pair: the same painted file on the
    same cut-work material trips cut_path on a plain check, and saying side=back silences exactly
    that finding — nothing else."""
    cut_material = dict(BANNER, id="flaga-poliester", name="Flaga poliester", cut_path=True)
    client.post("/api/admin/materials", headers={"X-Admin-Token": TOKEN},
                json={"material": cut_material})
    pdf = _painted_over(client.post("/api/template", json={"items": [
        {"material": "flaga-poliester", "width": "100", "height": "200", "unit": "cm"}]}).data)

    def check_ids(**extra):
        body = client.post("/api/check",
                           data={"file": (io.BytesIO(pdf), "strona.pdf"), **extra},
                           content_type="multipart/form-data").get_json()
        return [c["id"] for c in body["checks"]]

    plain, back = check_ids(), check_ids(side="back")
    assert "cut_path" in plain
    assert "cut_path" not in back
    assert sorted(back) == sorted(c for c in plain if c != "cut_path")


def test_rule_severities_are_admin_only(client):
    assert client.get("/api/admin/rules").status_code == 403
    assert client.post("/api/admin/rules", json={"rules": {}}).status_code == 403
