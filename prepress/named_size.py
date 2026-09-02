"""The size a FILENAME claims: `baner_800x2000mm.pdf`, `76cm x 37cm`, `1,5m x 3m`, `300x150a.tif`.

Same grammar as the in-house parser this shop's order tools use (digits, optional unit on EITHER
number, `x`/`×` between), so a name reads the same everywhere. Two things this module is careful
about, both learned on real production files:

* the unit alternative `m` must not swallow the `m` of `makieta` — hence the negative lookahead;
* a name with NO unit is as likely centimetres as millimetres (`300x150a.tif` was a 3009×1504 mm
  print). Bare numbers are reported in mm and marked `unit_stated=False`; `reconcile` lets a caller
  who KNOWS the real size decide whether the name meant centimetres.
"""
import re

_DIMENSION = re.compile(
    r"(\d+(?:[.,]\d+)?)(?:\s*(mm|cm|m)(?![a-z]))?"
    r"\s*[xX×]\s*"
    r"(\d+(?:[.,]\d+)?)(?:\s*(mm|cm|m)(?![a-z]))?",
    re.IGNORECASE,
)
_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0}
# How close the name has to come to a known size to be called the same size.
MATCH_TOLERANCE = 0.02


def parse_mm(filename):
    """((width_mm, height_mm), unit_stated) or (None, False). Bare numbers are read as millimetres."""
    match = _DIMENSION.search(filename or "")
    if not match:
        return None, False
    width_text, width_unit, height_text, height_unit = match.groups()
    stated = bool(width_unit or height_unit)
    # One stated unit covers a bare neighbour ("760x370mm"); each keeps its own when both are stated.
    width_factor = _TO_MM[(width_unit or height_unit or "mm").lower()]
    height_factor = _TO_MM[(height_unit or width_unit or "mm").lower()]
    size = (round(float(width_text.replace(",", ".")) * width_factor, 2),
            round(float(height_text.replace(",", ".")) * height_factor, 2))
    if size[0] <= 0 or size[1] <= 0:
        return None, False
    return size, stated


def same_size(a, b, tolerance=MATCH_TOLERANCE):
    """Two (w, h) sizes within tolerance of each other, in either orientation."""
    def close(p, q):
        return all(abs(p[i] - q[i]) <= max(0.5, q[i] * tolerance) for i in (0, 1))
    return close(a, b) or close((a[1], a[0]), b)


def reconcile(named_mm, unit_stated, reference_mm):
    """The named size in mm, read as centimetres when that is what makes it match the reference.

    `reference_mm` is a size the caller trusts more — the page, or the finished size a customer
    typed. A stated unit is never second-guessed. Nothing matches: the name stays as read, in mm.
    """
    if not named_mm or not reference_mm:
        return named_mm
    if unit_stated:
        return named_mm
    as_cm = (named_mm[0] * 10, named_mm[1] * 10)
    if same_size(as_cm, reference_mm) and not same_size(named_mm, reference_mm):
        return as_cm
    # A 1:10 page under a name in millimetres, or a name in cm over a full-size page: both leave the
    # name as read — the page-size rule reports scale, this function only settles the unit.
    return named_mm
