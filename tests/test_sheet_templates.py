"""Sheet templates: a page of panels with no single cut (an inflatable's siatka drawn at 1:N), stored with
`kind: "sheet"` and a `scale`. The page's rectangle stands in as the cut; the stamp carries full-size
geometry; the check judges resolution at full size; the editor shows no 3D link.

Run: python3.13 -m pytest tests/test_sheet_templates.py -q
"""
import io
import json
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import app as app_module  # noqa: E402
from prepress import identify, materials  # noqa: E402

TOKEN = "test-token-not-a-real-secret"
FABRIC = {"id": "tkanina-dmuchana", "name": "Tkanina na reklamę dmuchaną", "bleed_mm": 0, "safe_mm": 0,
          "min_dpi": 50, "max_width_mm": 5000, "colour": "cmyk", "notes": ""}


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = str(tmp_path / "materials.json")
    monkeypatch.setattr(materials, "DEFAULT_STORE", store)
    materials.save_all([FABRIC], store)
    monkeypatch.setenv(app_module.ADMIN_TOKEN_ENV, TOKEN)
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def _save_sheet(client, **overrides):
    payload = {"name": "Brama 5 × 3 m (siatka 1:10)", "kind": "sheet", "scale": 10, "bleed_mm": 0, "safe_mm": 0,
               "material": "tkanina-dmuchana", "note": "", "page_mm": [2300, 2300], "outlines": [], **overrides}
    return client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json=payload)


def test_a_sheet_template_needs_no_marked_cut_and_keeps_its_scale(client):
    saved = _save_sheet(client)
    assert saved.status_code == 200, saved.get_json()
    template = saved.get_json()["template"]
    assert template["kind"] == "sheet" and template["scale"] == 10
    assert template["trim_mm"] == [2300, 2300]                      # the page is the finished size
    assert [outline["type"] for outline in template["outlines"]] == ["cut"]
    # a flat template still has to mark a cut
    flat = _save_sheet(client, kind="flat")
    assert flat.status_code == 400


def test_a_sheet_s_stamp_states_full_size_geometry(client):
    token = _save_sheet(client).get_json()["template"]["token"]
    sheet = client.get(f"/api/templates/{token}/pdf")
    assert sheet.status_code == 200
    stamp = identify.read_stamp(sheet.data)["stamp"]
    assert stamp["scale"] == 10
    assert stamp["page_mm"] == [2300, 2300]
    assert stamp["netto_mm"] == [23000, 23000]


def _production_sheet():
    """A siatka like the real ones: two panels and a label on a 2300 × 2300 mm page."""
    from reportlab.pdfgen import canvas

    pt = 72 / 25.4
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(2300 * pt, 2300 * pt))
    pdf.rect(900 * pt, 1100 * pt, 725 * pt, 353 * pt, stroke=1, fill=0)
    pdf.rect(546 * pt, 1487 * pt, 353 * pt, 412 * pt, stroke=1, fill=0)
    pdf.setFont("Helvetica", 40)
    pdf.drawString(1000 * pt, 1200 * pt, "SIATKA BRYT 1")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_a_sheet_with_its_production_file_is_issued_as_that_page(client, tmp_path, monkeypatch):
    token = _save_sheet(client).get_json()["template"]["token"]
    uploaded = client.post(f"/api/admin/templates/{token}/source", headers={"X-Admin-Token": TOKEN},
                           data={"file": (io.BytesIO(_production_sheet()), "szablon.pdf")},
                           content_type="multipart/form-data")
    assert uploaded.status_code == 200, uploaded.get_json()
    assert uploaded.get_json()["pages"] == 1
    assert os.path.isfile(materials.source_path(token))      # beside the store, one file per token

    sheet = client.get(f"/api/templates/{token}/pdf")
    assert sheet.status_code == 200
    import pypdfium2
    document = pypdfium2.PdfDocument(io.BytesIO(sheet.data))
    assert len(document) == 1
    text = document[0].get_textpage().get_text_bounded()
    assert "SIATKA BRYT 1" in text                           # the production drawing, not a redrawn outline
    assert f"prepress-open:{token}" in text                  # the printed token laid over it
    stamp = identify.read_stamp(sheet.data)["stamp"]
    assert stamp["template"] == token and stamp["scale"] == 10

    picture = client.get(f"/api/templates/{token}/sheet.png")
    image = Image.open(io.BytesIO(picture.data)).convert("L")
    dark = sum(1 for value in image.getdata() if value < 128)
    assert dark > 2000                                       # the editor's canvas shows the panels

    # not a PDF, a flat template, an unknown token: refused
    assert client.post(f"/api/admin/templates/{token}/source", headers={"X-Admin-Token": TOKEN},
                       data={"file": (io.BytesIO(b"not a pdf"), "x.pdf")}, content_type="multipart/form-data").status_code == 400
    assert client.post("/api/admin/templates/nie-ma/source", headers={"X-Admin-Token": TOKEN},
                       data={"file": (io.BytesIO(_production_sheet()), "x.pdf")}, content_type="multipart/form-data").status_code == 404
    # deleting the template removes its file
    assert client.delete(f"/api/admin/templates/{token}", headers={"X-Admin-Token": TOKEN}).status_code == 200
    assert not os.path.isfile(materials.source_path(token))


def _panel(x, y, width, height):
    """A stored outline: one of a siatka's panels, marked as a cut."""
    return {"type": "cut", "safe_base": False, "start": [x, y], "closed": True,
            "segments": [["l", x + width, y], ["l", x + width, y + height], ["l", x, y + height]],
            "width_mm": width, "height_mm": height, "origin_mm": [x, y]}


def test_a_sheet_s_panels_marked_as_cuts_get_brutto_and_safe_lines_and_the_panel_moves_aside(client):
    panels = [_panel(900, 1100, 725, 353), _panel(546, 1487, 353, 412)]
    saved = _save_sheet(client, outlines=panels, bleed_mm=2, safe_mm=5)
    assert saved.status_code == 200, saved.get_json()
    template = saved.get_json()["template"]
    assert len(template["outlines"]) == 2
    assert template["trim_mm"] == [1079, 799]                   # the union of the panels, as a flag's hem + body
    client.post(f"/api/admin/templates/{template['token']}/source", headers={"X-Admin-Token": TOKEN},
                data={"file": (io.BytesIO(_production_sheet()), "szablon.pdf")}, content_type="multipart/form-data")
    sheet = client.get(f"/api/templates/{template['token']}/pdf")
    assert sheet.status_code == 200
    stamp = identify.read_stamp(sheet.data)["stamp"]
    assert any("brutto: dorobione" in note for note in stamp["notes"])
    assert any("obszar bezpieczny: dorobiony" in note for note in stamp["notes"])
    # the stamp's finished size is the panels' union at full size, the page as drawn
    assert stamp["netto_mm"] == [10790, 7990] and stamp["page_mm"] == [2300, 2300]
    # the spec panel's home is clear of both panels
    from prepress import from_template
    home = from_template._free_box_mm(2300, 2300, template["outlines"])
    assert home is not None
    for panel in panels:
        left, bottom = panel["origin_mm"]
        right, top = left + panel["width_mm"], bottom + panel["height_mm"]
        assert home[2] <= left or home[0] >= right or home[3] <= bottom or home[1] >= top, (home, panel["origin_mm"])
    assert (home[2] - home[0]) * (home[3] - home[1]) > 0.1 * 2300 * 2300


def test_a_sheet_s_faces_are_named_in_boxes_on_its_one_page(client):
    """A siatka carries both faces on its one page, and a panel wraps round the product: the arch band's lower half
    is the front, its upper half the back upside down. The template's `faces` draw those regions as labelled boxes."""
    panels = [_panel(900, 1100, 725, 353), _panel(546, 1487, 353, 412)]
    faces = [{"label": "PRZÓD", "box_mm": [900, 1100, 1625, 1276.5]},
             {"label": "TYŁ (obrócony o 180°)", "box_mm": [900, 1276.5, 1625, 1453]}]
    saved = _save_sheet(client, outlines=panels, bleed_mm=2, safe_mm=5, faces=faces)
    assert saved.status_code == 200, saved.get_json()
    token = saved.get_json()["template"]["token"]
    assert saved.get_json()["template"]["faces"] == faces
    client.post(f"/api/admin/templates/{token}/source", headers={"X-Admin-Token": TOKEN},
                data={"file": (io.BytesIO(_production_sheet()), "szablon.pdf")}, content_type="multipart/form-data")
    sheet = client.get(f"/api/templates/{token}/pdf")
    assert sheet.status_code == 200
    import pypdfium2
    document = pypdfium2.PdfDocument(io.BytesIO(sheet.data))
    assert len(document) == 1                                    # one page: both faces are on it
    text = document[0].get_textpage().get_text_bounded()
    assert "PRZÓD" in text and "TYŁ (obrócony o 180°)" in text and "SIATKA BRYT 1" in text
    stamp = identify.read_stamp(sheet.data)["stamp"]
    assert any("strony na arkuszu: PRZÓD, TYŁ" in note for note in stamp["notes"])
    # a face without a usable rectangle is refused
    assert _save_sheet(client, outlines=panels, faces=[{"label": "X", "box_mm": [1, 2, 3]}]).status_code == 400
    assert _save_sheet(client, outlines=panels, faces=[{"label": "", "box_mm": [0, 0, 1, 1]}]).status_code == 400


def test_a_face_may_state_its_rotation_and_its_own_true_areas(client):
    """A tent wall drawn on its side names where its top points, and a leg whose squares lie at 45°
    states its sizes itself; the sheet prints those, not the box's."""
    panels = [_panel(900, 1100, 725, 353)]
    faces = [{"label": "ŚCIANA", "box_mm": [900, 1100, 1625, 1453], "rotation": 180},
             {"label": "NOGA", "box_mm": [100, 100, 500, 500], "graphic_mm": [1170, 1170], "safe_mm": [1070, 1070], "frame": False}]
    saved = _save_sheet(client, outlines=panels, bleed_mm=2, safe_mm=5, faces=faces)
    assert saved.status_code == 200, saved.get_json()
    stored = saved.get_json()["template"]["faces"]
    assert stored[0]["rotation"] == 180 and stored[1]["graphic_mm"] == [1170, 1170] and stored[1]["safe_mm"] == [1070, 1070]
    assert stored[1]["frame"] is False and "frame" not in stored[0]          # no dashed frame round the leg chain
    token = saved.get_json()["template"]["token"]
    client.post(f"/api/admin/templates/{token}/source", headers={"X-Admin-Token": TOKEN},
                data={"file": (io.BytesIO(_production_sheet()), "szablon.pdf")}, content_type="multipart/form-data")
    sheet = client.get(f"/api/templates/{token}/pdf")
    assert sheet.status_code == 200
    import pypdfium2
    text = pypdfium2.PdfDocument(io.BytesIO(sheet.data))[0].get_textpage().get_text_bounded()
    assert "1170 x 1170" in text and "1070 x 1070" in text           # the leg's own sizes, not 4000 x 4000
    assert "7250 x 3530" in text                                      # the wall's, from its box
    # a rotation that is no number, a bad pair, a polygon of two points: refused
    assert _save_sheet(client, outlines=panels, faces=[{"label": "X", "box_mm": [0, 0, 1, 1], "rotation": "bok"}]).status_code == 400
    assert _save_sheet(client, outlines=panels, faces=[{"label": "X", "polygon_mm": [[0, 0], [1, 1]]}]).status_code == 400
    # a polygon face (a knee's diagonal strip) is stored with its derived box and any angle
    knee = _save_sheet(client, outlines=panels, faces=[{"label": "PRZÓD", "polygon_mm": [[100, 100], [300, 300], [280, 320], [80, 120]], "rotation": 45}])
    assert knee.status_code == 200, knee.get_json()
    assert knee.get_json()["template"]["faces"][0]["box_mm"] == [80, 100, 300, 320]
    assert knee.get_json()["template"]["faces"][0]["rotation"] == 45
    assert _save_sheet(client, outlines=panels, faces=[{"label": "X", "box_mm": [0, 0, 1, 1], "graphic_mm": [1]}]).status_code == 400


def test_the_free_box_is_the_largest_empty_rectangle():
    from prepress import from_template
    # one panel in the top-left quarter: the free box is the bottom half or the right half, not the panel
    home = from_template._free_box_mm(1000, 1000, [_panel(0, 500, 500, 500)])
    assert home is not None
    assert not (home[0] < 500 - 1 and home[3] > 500 + 1)     # never inside the panel's quarter
    assert (home[2] - home[0]) * (home[3] - home[1]) >= 0.4 * 1000 * 1000
    assert from_template._free_box_mm(1000, 1000, []) is None


def test_a_bad_scale_is_refused(client):
    assert _save_sheet(client, scale=0).status_code == 400
    assert _save_sheet(client, scale="dziesięć").status_code == 400


def test_the_editor_on_a_sheet_has_no_3d_link_and_the_check_judges_at_full_size(client):
    token = _save_sheet(client).get_json()["template"]["token"]
    page = client.get(f"/projekt/{token}").get_data(as_text=True)
    assert 'id="modelLink"' not in page and 'id="design"' in page
    # a 230 px canvas for a 2300 mm sheet (10 mm per px); one logo at its 1000 own pixels, 100 canvas px wide:
    # 1000 px over 1000 sheet mm = 25.4 dpi on the sheet, 2.54 dpi on the 10 m gate
    canvas_png = io.BytesIO()
    Image.new("RGB", (230, 230), "white").save(canvas_png, format="PNG")
    logo = io.BytesIO()
    Image.new("RGB", (1000, 500), (31, 107, 51)).save(logo, format="PNG")
    elements = [{"id": "logo", "type": "image", "src": "data:,", "x": 115, "y": 115, "scale": 1, "rotation": 0,
                 "width": 100, "height": 50}]
    saved = client.post(f"/api/designs/{token}", content_type="multipart/form-data", data={
        "artwork": (io.BytesIO(canvas_png.getvalue()), "projekt.png"), "elements": json.dumps(elements),
        "element-logo": (io.BytesIO(logo.getvalue()), "logo.png")})
    assert saved.status_code == 200, saved.get_json()
    judged = client.post(f"/api/recheck/{saved.get_json()['held']}", data={}, content_type="multipart/form-data").get_json()
    assert judged["recognised"] is True and judged["template_token"] == token
    assert judged["measured"]["min_dpi"] == pytest.approx(2.5, abs=0.1)


def test_a_long_face_label_wraps_at_its_comma_only_when_one_line_would_be_unreadable():
    from prepress.from_template import LABEL_WRAP_BELOW_PT, _label_lines
    from prepress.specblock import BOLD, _pick_size

    label = "NOGA — 5 kwadratów 833 mm zewnątrz, 1 × 548 mm wewnątrz"
    assert _label_lines(label, 400.0, 40.0) == [label]                               # room enough: one line
    narrow = _label_lines(label, 60.0, 40.0)
    assert narrow == ["NOGA — 5 kwadratów 833 mm zewnątrz,", "1 × 548 mm wewnątrz"]   # split at the comma
    assert _pick_size(label, BOLD, 60.0, 40.0, 4.0) < LABEL_WRAP_BELOW_PT <= _pick_size(narrow[0], BOLD, 60.0, 40.0, 4.0)
    assert _label_lines("PRZÓD", 10.0, 40.0) == ["PRZÓD"]                            # no comma: never split
