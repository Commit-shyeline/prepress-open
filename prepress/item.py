"""One queued item: a material plus the size the customer typed, resolved into real geometry.

Everything the generator and the checker need is computed here, once, so a template and the check of
the file that comes back can never disagree about where the boxes were:

    NETTO   the finished size — what the customer ordered, what gets trimmed to
    BRUTTO  netto plus the material's bleed on every side — the page the designer works on
    SAFE    netto minus the material's safe margin — nothing important may sit outside it

Two constraints come from the material rather than the artwork, and both are better caught here than
after someone has designed for a day:

* the roll. A material has a printable width. Exceeding it in ONE direction is a non-event — the job
  is turned on the roll and nothing is said, because how we fit it is production's business and
  announcing it made a perfectly good job look broken (shop rule, 2026-08-23). Exceeding it in BOTH
  directions is different: the graphic is then panelled, printed in strips and welded into one piece.
  That is worth telling a customer because the welds are visible, and it has a hard ceiling — past
  25 × 15 m the technique runs out and the job is refused.
* the page ceiling. PDF's own limit is 200 inches (5080 mm) per side. reportlab writes larger and
  PDFium reads it back (measured), but the DESIGNER's tools are the consumer here and Adobe's stop at
  that limit — which is why real production files in the wild carry names like
  `..._[skala.1do2]...pdf`. So anything past the ceiling is emitted at a declared scale, with the
  scale printed on the page, because a designer silently working at the wrong scale is expensive.
"""
import math

from . import messages

# PDF's maximum page dimension: 14400 units at 72 per inch = 200 in = 5080 mm.
PDF_MAX_SIDE_MM = 5080.0
# Stay under it with room for the bleed and the label strip.
SAFE_PAGE_CEILING_MM = 4800.0
# Scales a print shop actually uses, so a generated template matches the shop's own convention.
PREFERRED_SCALES = (1, 2, 4, 5, 10, 20)

# The spec strip under the artwork: tall enough for two readable lines at any scale, because a strip
# that shrinks with the scale becomes unreadable on exactly the large jobs that need it most.
SPEC_STRIP_MM = 22.0
# Below this much bleed there is no margin to put a bordered label box in, so `margin` degrades to
# `below` rather than drawing text over the artwork.
MIN_BLEED_FOR_MARGIN_SPEC_MM = 12.0

# The welding technique's ceiling: past this a graphic cannot be panelled into one piece at all.
# 25 × 15 m is this shop's limit (shop rule, 2026-08-23); a material may override either side.
DEFAULT_PANEL_MAX_LONG_MM = 25_000.0
DEFAULT_PANEL_MAX_SHORT_MM = 15_000.0


class ItemError(ValueError):
    """Something about this item the customer must fix before a template makes sense.

    Carries a NOTICE (code plus values), not a sentence — the wording lives in messages.py so a shop
    can rewrite it. `str()` still gives the default text, so a bare traceback stays readable.
    """

    def __init__(self, notice):
        self.notice = notice if isinstance(notice, dict) else {"code": "error", "values": {}}
        super().__init__(messages.render(self.notice["code"], self.notice.get("values")))


def parse_dimension(value, unit="mm"):
    """A dimension the customer typed, in millimetres.

    Accepts `800`, `80,5`, `80.5` and a unit of mm/cm/m, because a Polish print customer types
    centimetres and a spec sheet says millimetres, and guessing between them silently is how a
    template comes out ten times too small.
    """
    if value is None or str(value).strip() == "":
        raise ItemError(messages.notice("dimensions_required"))
    text = str(value).strip().replace(",", ".").replace(" ", "")
    try:
        number = float(text)
    except ValueError:
        raise ItemError(messages.notice("not_a_number", value=value))
    if number <= 0:
        raise ItemError(messages.notice("must_be_positive"))
    factor = {"mm": 1.0, "cm": 10.0, "m": 1000.0}.get(str(unit).strip().lower())
    if factor is None:
        raise ItemError(messages.notice("unknown_unit", unit=unit))
    millimetres = number * factor
    if millimetres > 100_000:
        raise ItemError(messages.notice("absurd_dimension", millimetres=_mm(millimetres)))
    return millimetres


def choose_scale(width_mm, height_mm):
    """The smallest preferred scale that fits the page ceiling. 1 means full size."""
    longest = max(width_mm, height_mm)
    for scale in PREFERRED_SCALES:
        if longest / scale <= SAFE_PAGE_CEILING_MM:
            return scale
    raise ItemError(messages.notice("too_big_for_pdf", longest=_mm(longest)))


def resolve(material, width_mm, height_mm, label="", scale=None):
    """Material + size → the geometry a template page and a check both read.

    Returns a dict with the three boxes in millimetres AT FULL SIZE, the scale the page will be drawn
    at, how many panels it needs, and NOTICES — codes plus numbers, never sentences. The wording lives
    in messages.py so the shop owns it.
    """
    if not material:
        raise ItemError(messages.notice("unknown_material"))
    for value in (width_mm, height_mm):
        if not isinstance(value, (int, float)) or value <= 0:
            raise ItemError(messages.notice("must_be_positive"))

    bleed = float(material.get("bleed_mm") or 0.0)
    safe = float(material.get("safe_mm") or 0.0)
    if safe * 2 >= min(width_mm, height_mm):
        raise ItemError(messages.notice("safe_area_eats_job", safe=_mm(safe),
                                        netto_w=_mm(width_mm), netto_h=_mm(height_mm)))

    notices = []
    panels = 1
    roll_mm = material.get("max_width_mm")
    if roll_mm:
        brutto_short = min(width_mm, height_mm) + 2 * bleed
        brutto_long = max(width_mm, height_mm) + 2 * bleed
        # Fitting the roll in EITHER direction is a non-event. Whether we turn the job on the roll is
        # our production business, and saying so made a perfectly fine job look broken to a customer
        # (shop rule, 2026-08-23). So: no notice at all in that case.
        if brutto_short > roll_mm:
            # Neither way round fits, so the graphic is panelled — printed in strips and welded into
            # one piece. That IS worth telling a customer, because the welds are visible in the
            # finished product. Strips run along the long side, so the count follows the short one.
            panels = math.ceil(brutto_short / roll_mm)
            long_limit = float(material.get("panel_max_long_mm") or DEFAULT_PANEL_MAX_LONG_MM)
            short_limit = float(material.get("panel_max_short_mm") or DEFAULT_PANEL_MAX_SHORT_MM)
            if brutto_long > long_limit or brutto_short > short_limit:
                raise ItemError(messages.notice(
                    "too_big_to_panel", netto_w=_mm(width_mm), netto_h=_mm(height_mm),
                    panel_max_long=_mm(long_limit), panel_max_short=_mm(short_limit)))
            notices.append(messages.notice("panelled", panels=panels, roll=_mm(roll_mm)))

    resolved_scale = int(scale) if scale else choose_scale(width_mm + 2 * bleed,
                                                          height_mm + 2 * bleed)
    if resolved_scale != 1:
        notices.append(messages.notice("scaled", scale=resolved_scale,
                                       netto_w=_mm(width_mm), netto_h=_mm(height_mm)))

    spec_position = material.get("spec_position") or "panel"
    if spec_position == "margin" and bleed < MIN_BLEED_FOR_MARGIN_SPEC_MM:
        spec_position = "below"
    # The strip is page furniture, not artwork: it is added to the PAGE and subtracted again by the
    # checker, so the artwork area stays exactly the brutto box.
    strip_mm = SPEC_STRIP_MM if spec_position == "below" else 0.0

    return {
        "material_id": material["id"],
        "material_name": material["name"],
        "label": str(label or "").strip(),
        "scale": resolved_scale,
        "netto_mm": (float(width_mm), float(height_mm)),
        "brutto_mm": (width_mm + 2 * bleed, height_mm + 2 * bleed),
        "safe_mm_box": (width_mm - 2 * safe, height_mm - 2 * safe),
        "bleed_mm": bleed,
        "safe_mm": safe,
        "min_dpi": material.get("min_dpi"),
        "colour": material.get("colour", "any"),
        "notes": material.get("notes", ""),
        "panels": panels,
        "spec_position": spec_position,
        "spec_strip_mm": strip_mm,
        "notices": notices,
    }


def page_size_mm(item):
    """The PDF page for this item: the scaled brutto box, plus the spec strip when there is one.

    The strip is NOT scaled — 22 mm of page furniture stays 22 mm whether the artwork is at 1:1 or
    1:10, because a caption that shrinks with the drawing is unreadable on exactly the biggest jobs.
    """
    width, height = item["brutto_mm"]
    return (width / item["scale"], height / item["scale"] + item.get("spec_strip_mm", 0.0))


def artwork_origin_mm(item):
    """Where the brutto box starts on the page. The strip sits BELOW the artwork, so the artwork is
    offset upward by exactly the strip height."""
    return (0.0, item.get("spec_strip_mm", 0.0))


def describe(item):
    """One line for the page label and the queue list."""
    width, height = item["netto_mm"]
    scale = "" if item["scale"] == 1 else f"  ·  1:{item['scale']}"
    label = f"{item['label']}  ·  " if item["label"] else ""
    return (f"{label}{item['material_name']}  ·  netto {_mm(width)}×{_mm(height)} mm  ·  "
            f"spad {_mm(item['bleed_mm'])} mm  ·  bezpieczny margines {_mm(item['safe_mm'])} mm"
            f"{scale}")


def _mm(value):
    return f"{value:.0f}" if math.isclose(value, round(value), abs_tol=0.05) else f"{value:.1f}"
