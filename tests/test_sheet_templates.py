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
