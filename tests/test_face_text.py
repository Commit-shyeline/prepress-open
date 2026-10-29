"""Where a face's name lands on a template page (prepress/from_template.py _place_face_text): inside the face where
there is room, outside it with a leader where the room would make the name unreadable, and never twice in one spot."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prepress import from_template, specblock  # noqa: E402

PAGE_W, PAGE_H = 400.0, 300.0
FACE = (100.0, 100.0, 200.0, 200.0)  # left, bottom, right, top, mm


def empty_ink():
    """The page's ink grid with nothing drawn: INK_CELL_MM cells, rows up the page."""
    return [[False] * int(PAGE_W / from_template.INK_CELL_MM) for _ in range(int(PAGE_H / from_template.INK_CELL_MM))]


def cells_under(ink, box):
    left, bottom, right, top = box
    cell = from_template.INK_CELL_MM
    return [ink[row][column] for row in range(int(bottom / cell) + 1, int(top / cell)) for column in range(int(left / cell) + 1, int(right / cell))]


def place(monkeypatch, label_pt, rotation=0):
    """Runs _place_face_text with the drawing helpers recording instead of drawing; the name sizes to `label_pt`."""
    drawn = {"inside": [], "outside": [], "leader": [], "along": []}
    monkeypatch.setattr(specblock, "_pick_size", lambda *args: label_pt)
    monkeypatch.setattr(from_template, "_draw_face_text", lambda pdf, label, box, template, home, short, rotation=0, areas=None: drawn["inside"].append((home, rotation)))
    monkeypatch.setattr(from_template, "_draw_leader", lambda pdf, rect, box, short: drawn["leader"].append(rect))
    monkeypatch.setattr(from_template, "_draw_face_text_along", lambda pdf, label, corners, rotation, short, areas: drawn["along"].append((corners, rotation)))
    ink = empty_ink()
    from_template._place_face_text(None, {"rotation": rotation}, "front", FACE, None, ink, PAGE_W, PAGE_H, ((1, 1), (1, 1)))
    return drawn, ink


def test_a_readable_name_sits_inside_the_face_and_takes_its_spot(monkeypatch):
    drawn, ink = place(monkeypatch, label_pt=10.0)
    assert drawn["leader"] == [] and drawn["along"] == []
    (home, rotation), = drawn["inside"]
    assert rotation == 0
    assert FACE[0] <= home[0] < home[2] <= FACE[2] and FACE[1] <= home[1] < home[3] <= FACE[3]
    assert all(cells_under(ink, home)), "the next face must not land on this name"


def test_an_unreadable_name_goes_outside_with_a_leader_and_leaves_the_face_clear(monkeypatch):
    drawn, ink = place(monkeypatch, label_pt=from_template.FACE_TEXT_READABLE_PT - 1)
    (outside,), (home, rotation) = drawn["leader"], drawn["inside"][0]
    assert home == outside and rotation == 0
    assert outside[2] <= FACE[0] or outside[0] >= FACE[2] or outside[3] <= FACE[1] or outside[1] >= FACE[3]
    assert all(cells_under(ink, outside)) and not any(cells_under(ink, FACE))


def test_a_name_at_an_odd_angle_runs_along_the_face(monkeypatch):
    drawn, _ = place(monkeypatch, label_pt=10.0, rotation=45)
    assert drawn["inside"] == [] and drawn["leader"] == []
    (corners, rotation), = drawn["along"]
    assert rotation == 45 and len(corners) == 4
