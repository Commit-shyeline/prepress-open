"""A file is matched to its template by the sizes of its die-lines (prepress/fingerprint.py), which survive an
export that strips the stamp and the printed token.

Run: python3.13 -m pytest tests/test_fingerprint.py -q
"""
import io
import os
import sys

import pytest
from reportlab.pdfgen import canvas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import app as app_module, fingerprint, materials  # noqa: E402

TOKEN = "test-token-not-a-real-secret"
PT = 72 / 25.4


def _outline(width, height, kind="cut"):
    return {"type": kind, "safe_base": False, "start": [0, 0], "closed": True, "segments": [["l", width, 0], ["l", width, height], ["l", 0, height]],
            "width_mm": width, "height_mm": height, "origin_mm": [0, 0]}


GATE_5x3 = {"token": "gate53", "name": "Brama 5 × 3", "page_mm": [2300, 2300],
            "outlines": [_outline(725, 353), _outline(353, 412), _outline(353, 412), _outline(726, 112)]}
GATE_6x4 = {"token": "gate64", "name": "Brama 6 × 4", "page_mm": [2300, 2300],
            "outlines": [_outline(825, 403), _outline(403, 462), _outline(403, 462), _outline(826, 122)]}
FLAG = {"token": "flag", "name": "Flaga", "page_mm": [800, 2400], "outlines": [_outline(633, 2200)]}


def test_the_best_match_is_the_template_whose_panels_the_file_carries():
    uploaded = [{"closed": True, "width_mm": 725.3, "height_mm": 353.1}, {"closed": True, "width_mm": 353.0, "height_mm": 412.4},
                {"closed": False, "width_mm": 2000, "height_mm": 5}]
    ranked = fingerprint.rank([GATE_6x4, GATE_5x3, FLAG], (2300, 2300), uploaded)
    assert [entry["token"] for entry in ranked] == ["gate53", "gate64"]      # the flag's page is another size
    assert ranked[0]["matched"] == 3 and ranked[0]["outlines"] == 4           # both legs match the one leg found
    assert fingerprint.best(ranked)["token"] == "gate53"


def test_a_tie_or_a_single_size_is_not_decisive():
    ranked = fingerprint.rank([GATE_5x3, GATE_6x4], (2300, 2300), [{"closed": True, "width_mm": 726, "height_mm": 112}])
    assert ranked[0]["matched"] == 1 and fingerprint.best(ranked) is None
    assert fingerprint.best(fingerprint.rank([GATE_5x3, GATE_6x4], (2300, 2300), [])) is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = str(tmp_path / "materials.json")
    monkeypatch.setattr(materials, "DEFAULT_STORE", store)
    materials.save_all([{"id": "tkanina-dmuchana", "name": "T", "bleed_mm": 0, "safe_mm": 0, "min_dpi": None,
                         "max_width_mm": 50000, "colour": "cmyk", "notes": ""}], store)
    monkeypatch.setenv(app_module.ADMIN_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(app_module.RETURN_ORIGINS_ENV, "https://shop.example")
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def _sheet_pdf(panels):
    """A siatka page: the panels stroked on a 2300 × 2300 mm page."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(2300 * PT, 2300 * PT))
    for x, y, width, height in panels:
        pdf.rect(x * PT, y * PT, width * PT, height * PT, stroke=1, fill=0)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _save_sheet(client, name, panels):
    outlines = [{**_outline(w, h), "origin_mm": [x, y], "start": [x, y],
                 "segments": [["l", x + w, y], ["l", x + w, y + h], ["l", x, y + h]]} for x, y, w, h in panels]
    saved = client.post("/api/admin/templates", headers={"X-Admin-Token": TOKEN}, json={
        "name": name, "kind": "sheet", "scale": 10, "bleed_mm": 2, "safe_mm": 5, "material": "tkanina-dmuchana",
        "note": "", "page_mm": [2300, 2300], "outlines": outlines})
    assert saved.status_code == 200, saved.get_json()
    return saved.get_json()["template"]["token"]


def test_identify_names_the_die_a_file_was_drawn_on_and_answers_the_shop_s_origin(client):
    gate = _save_sheet(client, "Brama 5 × 3", [(900, 1100, 725, 353), (546, 1487, 353, 412), (1400, 1487, 353, 412)])
    other = _save_sheet(client, "Brama 6 × 4", [(850, 1050, 825, 403), (500, 1450, 403, 462), (1400, 1450, 403, 462)])
    upload = _sheet_pdf([(900, 1100, 725, 353), (546, 1487, 353, 412), (1400, 1487, 353, 412)])
    answer = client.post("/api/identify", data={"file": (io.BytesIO(upload), "projekt.pdf")},
                         content_type="multipart/form-data", headers={"Origin": "https://shop.example"})
    assert answer.status_code == 200, answer.get_json()
    body = answer.get_json()
    assert body["best"]["token"] == gate
    assert [m["token"] for m in body["matches"]] == [gate, other]
    assert body["matches"][0]["matched"] == 3 and body["matches"][1]["matched"] == 0
    assert answer.headers.get("Access-Control-Allow-Origin") == "https://shop.example"
    # an unlisted origin gets the answer without the CORS header; a preflight from a listed one is allowed
    plain = client.post("/api/identify", data={"file": (io.BytesIO(upload), "projekt.pdf")},
                        content_type="multipart/form-data", headers={"Origin": "https://elsewhere.example"})
    assert plain.status_code == 200 and "Access-Control-Allow-Origin" not in plain.headers
    preflight = client.options("/api/identify", headers={"Origin": "https://shop.example", "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 204 and preflight.headers.get("Access-Control-Allow-Origin") == "https://shop.example"
    # the checker's own candidate list is ranked the same way when the stamp is gone
    judged = client.post("/api/check", data={"file": (io.BytesIO(upload), "projekt.pdf")}, content_type="multipart/form-data").get_json()
    assert judged["recognised"] is False
    assert [c["token"] for c in judged["candidates"]][:2] == [gate, other]
    assert judged["candidates"][0]["matched"] == 3 and judged["best"] == gate
