"""The design editor's server side (ROADMAP „Step 3: the design editor"): the sheet as a picture, the design back as a
stamped page, held for the check and for download.

Runs through Flask's test client — no server is started. A template is made the way the admin makes
one: a production PDF inspected, its cut line chosen, saved under a token.

Run: python3.13 -m pytest tests/test_design.py -q
"""
import io
import os
import sys

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import app as app_module  # noqa: E402
from prepress import design, identify, materials  # noqa: E402

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


def _production_pdf(width_mm=500, height_mm=800):
    """A production template like the real ones: sheet frame, bleed, cut, safe (tests/test_templates.py)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width_mm * PT, height_mm * PT))
    pdf.rect(0.4 * PT, 0.4 * PT, (width_mm - 0.8) * PT, (height_mm - 0.8) * PT, stroke=1, fill=0)
    pdf.rect(20 * PT, 20 * PT, 460 * PT, 760 * PT, stroke=1, fill=0)     # bleed
    pdf.rect(30 * PT, 30 * PT, 440 * PT, 740 * PT, stroke=1, fill=0)     # cut
    pdf.rect(70 * PT, 70 * PT, 360 * PT, 660 * PT, stroke=1, fill=0)     # safe
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def template_token(client):
    """A template saved the admin's way, so every route under test sees a real one."""
    inspected = client.post("/api/admin/inspect", headers={"X-Admin-Token": TOKEN},
                            data={"file": (io.BytesIO(_production_pdf()), "szablon.pdf"), "page": "0"},
                            content_type="multipart/form-data")
    body = inspected.get_json()
    assert inspected.status_code == 200, body
    cut = next(candidate for candidate in body["candidates"] if candidate["outline"]["width_mm"] == 440.0)
    saved = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": "Flaga testowa", "bleed_mm": 20, "safe_mm": 45, "material": "banner-frontlit-510",
        "note": "", "page_mm": body["page_mm"], "outlines": [{**cut["outline"], "type": "cut"}]})
    assert saved.status_code == 200, saved.get_json()
    return saved.get_json()["template"]["token"]


def _artwork_png(width=400, height=640):
    """What the editor sends: the design on white, as a PNG of the sheet's pixel grid."""
    image = Image.new("RGB", (width, height), "white")
    image.paste((31, 107, 51), (40, 40, 200, 200))
    holder = io.BytesIO()
    image.save(holder, format="PNG")
    return holder.getvalue()


# ── The sheet as a picture ──────────────────────────────────────────────────

def test_the_sheet_comes_back_as_a_png_of_the_asked_size(client, template_token):
    answer = client.get(f"/api/templates/{template_token}/sheet.png")
    assert answer.status_code == 200
    assert answer.mimetype == "image/png"
    picture = Image.open(io.BytesIO(answer.data))
    assert max(picture.size) == app_module.DESIGN_SHEET_LONG_EDGE_PX
    # the sheet is 500 × 800 mm: the picture keeps the page's proportions
    assert abs(picture.size[0] / picture.size[1] - 500 / 800) < 0.01


def test_an_unknown_template_has_no_sheet_and_no_editor(client):
    assert client.get("/api/templates/nie-ma/sheet.png").status_code == 404
    assert client.get("/projekt/nie-ma").status_code == 404


# ── The editor page ─────────────────────────────────────────────────────────

def test_the_editor_page_carries_the_template_and_its_scripts(client, template_token):
    answer = client.get(f"/projekt/{template_token}")
    assert answer.status_code == 200
    page = answer.get_data(as_text=True)
    assert f'data-token="{template_token}"' in page
    assert f"/api/templates/{template_token}/sheet.png" in page
    assert "designer-model.js" in page and "designer.js" in page
    assert 'id="design"' in page            # the canvas


# ── The design back as the filled template page ─────────────────────────────

def test_a_saved_design_is_a_stamped_page_of_the_template_size(client, template_token):
    saved = client.post(f"/api/designs/{template_token}",
                        data={"artwork": (io.BytesIO(_artwork_png()), "projekt.png"), "elements": "[]"},
                        content_type="multipart/form-data")
    body = saved.get_json()
    assert saved.status_code == 200, body
    assert body["held"] and body["pdf"].endswith(f"/api/designs/{body['held']}/pdf")

    pdf = client.get(body["pdf"])
    assert pdf.status_code == 200
    assert pdf.mimetype == "application/pdf"
    # the page is the template's page, and the stamp names the template, as the sheet's does
    assert [round(v) for v in identify.page_size_mm(pdf.data)] == [500, 800]
    stamp = identify.read_stamp(pdf.data)
    assert stamp, stamp["reason"]              # an Identification is truthy once a template was recognised
    assert stamp["stamp"]["template"] == template_token
    assert stamp["stamp"]["notes"] == [design.DEFAULT_NOTE]


def _save_design(client, template_token, headers=None):
    saved = client.post(f"/api/designs/{template_token}",
                        data={"artwork": (io.BytesIO(_artwork_png()), "projekt.png"), "elements": "[]"},
                        content_type="multipart/form-data", headers=headers or {})
    assert saved.status_code == 200, saved.get_json()
    return saved.get_json()


def test_a_saved_design_is_rechecked_as_the_template_it_was_drawn_on(client, template_token):
    """The ROADMAP's claim: after the editor, everything is the existing loop."""
    body = _save_design(client, template_token)
    assert body["check"].endswith(f"/plik?held={body['held']}&name=projekt-{template_token}.pdf")
    assert body["model"].endswith(f"/model/{template_token}?design={body['held']}")
    judged = client.post(f"/api/recheck/{body['held']}", data={}, content_type="multipart/form-data")
    assert judged.status_code == 200, judged.get_json()
    verdict = judged.get_json()
    assert verdict["recognised"] is True
    assert verdict["template_token"] == template_token
    assert verdict["token"] == body["held"]


def test_a_design_saved_without_a_login_is_the_token_holder_s(client, template_token, monkeypatch):
    """The public editor saves with no session; the check happens logged in. The token is the key."""
    import jwt

    secret = "session-secret-not-a-real-one"
    monkeypatch.setenv(app_module.SESSION_SECRET_ENV, secret)
    body = _save_design(client, template_token)
    bearer = {"Authorization": "Bearer " + jwt.encode({"nip": "1111111111"}, secret, algorithm="HS256")}
    judged = client.post(f"/api/recheck/{body['held']}", data={}, content_type="multipart/form-data",
                         headers=bearer)
    assert judged.status_code == 200, judged.get_json()
    assert judged.get_json()["template_token"] == template_token


def test_the_design_is_a_texture_the_3d_scene_wears(client, template_token):
    body = _save_design(client, template_token)
    texture = client.get(f"/api/designs/{body['held']}/png")
    assert texture.status_code == 200
    assert texture.mimetype == "image/png"
    picture = Image.open(io.BytesIO(texture.data))
    assert max(picture.size) == app_module.DESIGN_TEXTURE_LONG_EDGE_PX
    assert abs(picture.size[0] / picture.size[1] - 500 / 800) < 0.01    # the whole page, as the scene maps it
    assert client.get("/api/designs/nie-ma/png").status_code == 404
    scene = client.get(f"/model/{template_token}?design={body['held']}")
    assert scene.status_code == 200
    assert "/api/designs/" in scene.get_data(as_text=True)


def test_a_design_without_a_picture_is_refused(client, template_token):
    answer = client.post(f"/api/designs/{template_token}", data={"elements": "[]"},
                         content_type="multipart/form-data")
    assert answer.status_code == 400
    broken = client.post(f"/api/designs/{template_token}",
                         data={"artwork": (io.BytesIO(b"not a picture"), "projekt.png")},
                         content_type="multipart/form-data")
    assert broken.status_code == 400
    assert "zapisz" in broken.get_json()["error"].lower() or "read" in broken.get_json()["error"].lower()


def test_an_expired_or_unknown_design_has_no_pdf(client):
    assert client.get("/api/designs/nie-ma/pdf").status_code == 404


def test_compose_refuses_a_template_without_a_page_size():
    with pytest.raises(design.DesignError):
        design.compose_pdf({"name": "bez strony"}, _artwork_png())
