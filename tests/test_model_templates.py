"""3D products without Blender work: a .glb imported with its page becomes a sheet template with a model
beside it; the pairing view's map is stored on the record; /model/<token> serves the model viewer, the
model file and the map; the editor links to it; deleting the template removes the model.

Run: python3.13 -m pytest tests/test_model_templates.py -q
"""
import io
import json
import os
import struct
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import app as app_module  # noqa: E402
from prepress import materials  # noqa: E402

TOKEN = "test-token-not-a-real-secret"
FABRIC = {"id": "tkanina-napinana", "name": "Tkanina napinana", "bleed_mm": 0, "safe_mm": 0,
          "min_dpi": 50, "max_width_mm": 5000, "colour": "cmyk", "notes": ""}


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = str(tmp_path / "materials.json")
    monkeypatch.setattr(materials, "DEFAULT_STORE", store)
    materials.save_all([FABRIC], store)
    monkeypatch.setenv(app_module.ADMIN_TOKEN_ENV, TOKEN)
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def _glb():
    """The smallest glTF 2.0 binary: a header and one JSON chunk. The viewer is not run here; the
    route checks the magic and the version, and keeps the bytes."""
    body = json.dumps({"asset": {"version": "2.0"}}).encode()
    body += b" " * (-len(body) % 4)
    chunk = struct.pack("<II", len(body), 0x4E4F534A) + body
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunk)) + chunk


def _png(width=300, height=240):
    holder = io.BytesIO()
    Image.new("RGB", (width, height), (31, 107, 51)).save(holder, format="PNG")
    return holder.getvalue()


def _pdf_pages(sizes_mm):
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    holder = io.BytesIO()
    pdf = canvas.Canvas(holder, pagesize=(sizes_mm[0][0] * mm, sizes_mm[0][1] * mm))
    for width, height in sizes_mm:
        pdf.setPageSize((width * mm, height * mm))
        pdf.rect(10 * mm, 10 * mm, (width - 20) * mm, (height - 20) * mm)
        pdf.showPage()
    pdf.save()
    return holder.getvalue()


def _import(client, **fields):
    data = {"model": (io.BytesIO(_glb()), "sciana.glb"), **fields}
    return client.post("/api/admin/models", headers={"X-Admin-Token": TOKEN}, data=data,
                       content_type="multipart/form-data")


MAP = {"faces": [{"mesh": 0, "island": 0, "name": "Front", "space": "uv", "source": "auto",
                  "affine": [[1, 0, 0], [0, 1, 0]], "turn": 0, "mirror": False,
                  "pairs": [{"from": [0, 0], "to": [0, 0]}]}]}


def test_a_png_page_and_a_glb_become_a_sheet_template_with_a_model(client):
    answer = _import(client, page=(io.BytesIO(_png()), "sciana.png"), width_mm="3000", height_mm="2400",
                     name="Ścianka 3 × 2,4 m", material="tkanina-napinana")
    assert answer.status_code == 200, answer.get_json()
    template = answer.get_json()["template"]
    token = template["token"]
    assert template["kind"] == "sheet" and template["page_mm"] == [3000.0, 2400.0]
    assert template["material"] == "tkanina-napinana"
    assert os.path.isfile(materials.source_path(token))                                   # the page, as a PDF
    assert os.path.isfile(materials.source_path(token, suffix=materials.MODEL_SUFFIX))    # the model beside it

    # the sheet is the picture, with the stamp over it; the viewer page is the model one
    assert client.get(f"/api/templates/{token}/pdf").status_code == 200
    page = client.get(f"/model/{token}").get_data(as_text=True)
    assert "product-model.js" in page and "api/templates/" in page
    assert client.get(f"/api/templates/{token}/model.glb").data == _glb()
    info = client.get(f"/api/templates/{token}/model").get_json()
    assert info["page_mm"] == [3000.0, 2400.0] and info["map"] == {"faces": []}
    assert info["boxes"] == [[0.0, 0.0, 1.0, 1.0]]                                        # the whole page is the print
    assert info["glb"].endswith(f"/api/templates/{token}/model.glb")
    listed = next(t for t in client.get("/api/templates").get_json()["templates"] if t["token"] == token)
    assert listed["has_model"] is True
    # the editor links the 3D view for a sheet that has a model
    assert 'id="modelLink"' in client.get(f"/projekt/{token}").get_data(as_text=True)


def test_a_multipage_pdf_keeps_the_chosen_page_alone(client):
    pdf = _pdf_pages([(1000, 500), (2000, 800)])
    answer = _import(client, page=(io.BytesIO(pdf), "szablon.pdf"), page_number="2", name="Tył")
    assert answer.status_code == 200, answer.get_json()
    token = answer.get_json()["template"]["token"]
    assert answer.get_json()["template"]["page_mm"] == [2000.0, 800.0]
    import pikepdf
    with pikepdf.open(io.BytesIO(materials.load_template_source(token))) as kept:
        assert len(kept.pages) == 1
    assert _import(client, page=(io.BytesIO(pdf), "szablon.pdf"), page_number="3", name="X").status_code == 400


def test_the_map_from_the_pairing_view_is_stored_and_served(client):
    token = _import(client, page=(io.BytesIO(_png()), "s.png"), width_mm="3000", height_mm="2400",
                    name="Ścianka").get_json()["template"]["token"]
    saved = client.put(f"/api/admin/templates/{token}/model-map", headers={"X-Admin-Token": TOKEN}, json=MAP)
    assert saved.status_code == 200, saved.get_json()
    served = client.get(f"/api/templates/{token}/model").get_json()["map"]
    assert served["faces"][0]["affine"] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert served["faces"][0]["pairs"] == MAP["faces"][0]["pairs"] and served["faces"][0]["name"] == "Front"
    # the template's other fields survive the map, and the map survives a rename
    client.patch(f"/api/admin/templates/{token}", headers={"X-Admin-Token": TOKEN}, json={"name": "Ścianka 3 m"})
    again = client.get(f"/api/templates/{token}/model").get_json()
    assert again["name"] == "Ścianka 3 m" and again["map"]["faces"][0]["mesh"] == 0

    bad = dict(MAP, faces=[{"mesh": 0, "island": 0, "affine": [[1, 0], [0, 1]]}])
    assert client.put(f"/api/admin/templates/{token}/model-map", headers={"X-Admin-Token": TOKEN}, json=bad).status_code == 400
    assert client.put(f"/api/admin/templates/{token}/model-map", headers={"X-Admin-Token": TOKEN},
                      json={"faces": "x"}).status_code == 400
    assert client.put("/api/admin/templates/nie-ma/model-map", headers={"X-Admin-Token": TOKEN}, json=MAP).status_code == 404


def test_a_model_may_join_an_existing_template_whose_boxes_are_its_cut_lines(client):
    outline = {"type": "cut", "safe_base": False, "start": [100, 100], "closed": True,
               "segments": [["l", 1100, 100], ["l", 1100, 600], ["l", 100, 600]],
               "width_mm": 1000, "height_mm": 500, "origin_mm": [100, 100]}
    saved = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN},
                        json={"name": "Baner", "bleed_mm": 10, "safe_mm": 20, "page_mm": [1200, 700],
                              "outlines": [outline]})
    token = saved.get_json()["template"]["token"]
    assert client.get(f"/api/templates/{token}/model").status_code == 404                 # no model yet
    assert "product-model.js" not in client.get(f"/model/{token}").get_data(as_text=True)   # the flag scene
    answer = _import(client, token=token)
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["template"]["name"] == "Baner"                                # the record, untouched
    # the cut's box in page fractions, top down: x 100..1100 of 1200, y 100..600 of 700 from the bottom
    [box] = client.get(f"/api/templates/{token}/model").get_json()["boxes"]
    assert box == pytest.approx([100 / 1200, 1 - 600 / 700, 1100 / 1200, 1 - 100 / 700], abs=1e-4)
    assert "product-model.js" in client.get(f"/model/{token}").get_data(as_text=True)


def test_what_is_refused_and_what_a_delete_removes(client):
    headers = {"X-Admin-Token": TOKEN}
    # not a glTF binary
    answer = client.post("/api/admin/models", headers=headers, content_type="multipart/form-data",
                         data={"model": (io.BytesIO(b"not a model"), "x.glb"), "page": (io.BytesIO(_png()), "s.png"),
                               "width_mm": "10", "height_mm": "10", "name": "X"})
    assert answer.status_code == 400 and "glTF" in answer.get_json()["error"]
    # a picture without a size, a page that is neither, no page at all, an unknown template
    assert _import(client, page=(io.BytesIO(_png()), "s.png"), name="X").status_code == 400
    assert _import(client, page=(io.BytesIO(b"plain text"), "s.txt"), width_mm="10", height_mm="10", name="X").status_code == 400
    assert _import(client, name="X").status_code == 400
    assert _import(client, token="nie-ma").status_code == 404
    # no token at all
    assert client.post("/api/admin/models", data={}, content_type="multipart/form-data").status_code == 403

    token = _import(client, page=(io.BytesIO(_png()), "s.png"), width_mm="3000", height_mm="2400",
                    name="Ścianka").get_json()["template"]["token"]
    model_path = materials.source_path(token, suffix=materials.MODEL_SUFFIX)
    assert client.delete(f"/api/admin/templates/{token}", headers=headers).status_code == 200
    assert not os.path.isfile(model_path) and not os.path.isfile(materials.source_path(token))
    assert client.get(f"/api/templates/{token}/model.glb").status_code == 404
