"""The rule runner: fourteen rules, none of whose words or severities live in this file.

A rule is a function of `(facts, expected, material)` returning a finding or None.

    facts     what was measured or read off the returned file — `measure.py` for what the ink does,
              `structure.py` for what the PDF declares about itself
    expected  the geometry the template was issued with, from the stamp
    material  the shop's own numbers, which is where every threshold comes from

Three things are deliberately NOT in this file:

* the thresholds — bleed, safe margin, resolution floor and whether a material is cut work all come
  from the material an admin edits;
* the words — a rule emits a CODE plus numbers, and `messages.py` turns that into a sentence the shop
  can rewrite. The engine was never allowed to write a customer-facing sentence, and the rules used
  to be the one place that broke that;
* the severity — a shop may silence any rule or move it between amber and red, because "how bad is
  this" is a business judgement. RGB on a backlit banner and RGB on a fine-art print are not the same
  problem, and only the shop knows which is which.

A finding always carries its `id`, so a shop's severity choices survive a rewording, and the `values`
behind the text, so a UI can show the numbers without parsing a sentence.
"""
from . import messages, structure

LEVELS = ("red", "amber", "green", "info")
LEVEL_ORDER = {"red": 0, "amber": 1, "info": 2, "green": 3}

# How close is close enough. A designer's export rounds, and a PDF stores points, so demanding exact
# millimetres would fail every real file — 0.5 mm is under the width of a trim line.
TOLERANCE_MM = 0.5


def _size_tolerance_mm(dimension_mm):
    """A size comparison's tolerance, proportional to what is being compared.

    0.5 mm is the right bar for a business card and paper-thinking for a flag: this morning's real
    a customer's files measure 800.0 x 2400.0 against a 800.8 x 2400.8 template — 0.03 % out, less than
    textile stretches when you look at it — and a flat TOLERANCE_MM flagged them RED. 0.1 % of the
    dimension, floored at the flat tolerance, keeps the card strict and stops failing flags over
    less than a millimetre per metre.
    """
    return max(TOLERANCE_MM, dimension_mm * 0.001)

# How many names to list before a report turns into a wall of text.
MAX_NAMES_LISTED = 4


def _finding(rule_id, level, outcome, regions=None, **values):
    """A finding as a code plus its numbers. The words are looked up later, from the shop's wording.

    `regions` — [x, y, w, h] rectangles in artwork millimetres, top-left frame — is WHERE the
    finding sits, when the measurement knows. A page overlay or a 3D model paints them; the text
    report simply ignores them. Absent (not an empty list) when a finding has no geometry.
    """
    found = {"id": rule_id, "level": level, "code": f"check.{rule_id}.{outcome}", "values": values}
    if regions:
        found["regions"] = regions
    return found


def _mm(value):
    return f"{value:.0f}"


# ── Geometry: the template against what came back ────────────────────────────

def check_page_size(facts, expected, material=None):
    """The returned page must be a size the template could have produced.

    TWO heights are acceptable, not one. The template page is the artwork plus a spec strip below it,
    and a designer who exports the artwork alone — dropping the strip — has done nothing wrong.
    Failing that would reject correct work, so both pass, in either orientation.
    """
    strip = expected.get("strip_mm", 0.0)
    width = expected["brutto_mm"][0] / expected["scale"]
    art_height = expected["brutto_mm"][1] / expected["scale"]
    candidates = {(width, art_height + strip), (width, art_height)}
    actual_w, actual_h = facts["page_mm"]

    def close(a, b):
        return (abs(a[0] - b[0]) <= _size_tolerance_mm(b[0])
                and abs(a[1] - b[1]) <= _size_tolerance_mm(b[1]))

    for candidate in candidates:
        if close((actual_w, actual_h), candidate):
            return _finding("page_size", "green", "ok",
                            page_w=_mm(actual_w), page_h=_mm(actual_h))
    for candidate in candidates:
        if close((actual_h, actual_w), candidate):
            return _finding("page_size", "amber", "rotated",
                            expected_w=_mm(candidate[0]), expected_h=_mm(candidate[1]),
                            page_w=_mm(actual_w), page_h=_mm(actual_h))
    return _finding("page_size", "red", "wrong",
                    expected_w=_mm(width), expected_h=_mm(art_height + strip),
                    page_w=_mm(actual_w), page_h=_mm(actual_h))


def check_declared_trim(facts, expected, material=None):
    """If the file declares a TrimBox, it must agree with the template's netto size.

    A declared TrimBox is the designer stating where they expect the cut, so a disagreement is a
    different failure from a resized page: the sheet may be exactly right while the trim intent is
    wrong, which is how a job comes back cut to the wrong finished size. Most files declare no boxes
    at all — that is normal, and produces no finding rather than a complaint.
    """
    boxes = facts.get("declared_boxes_mm") or {}
    trim = boxes.get("trimbox")
    if not trim:
        return None
    # A TrimBox equal to the MediaBox is the exporter's default, not the designer stating anything —
    # real customer files set every box to the page. Treating that as a declaration
    # flagged every legacy file red over information it never contained.
    media = boxes.get("mediabox")
    if media and abs(trim[0] - media[0]) <= 0.2 and abs(trim[1] - media[1]) <= 0.2:
        return None
    expected_w, expected_h = (v / expected["scale"] for v in expected["netto_mm"])
    values = {"trim_w": _mm(trim[0]), "trim_h": _mm(trim[1]),
              "expected_w": _mm(expected_w), "expected_h": _mm(expected_h)}
    if (abs(trim[0] - expected_w) <= _size_tolerance_mm(expected_w)
            and abs(trim[1] - expected_h) <= _size_tolerance_mm(expected_h)):
        return _finding("declared_trim", "green", "ok", **values)
    if (abs(trim[0] - expected_h) <= _size_tolerance_mm(expected_h)
            and abs(trim[1] - expected_w) <= _size_tolerance_mm(expected_w)):
        return _finding("declared_trim", "amber", "rotated", **values)
    return _finding("declared_trim", "red", "wrong", **values)


def check_template_guides_removed(facts, expected, material=None):
    """The template's own guide lines must not survive into the artwork.

    They print. A trim line left in the file comes out as a magenta rectangle on a customer's banner,
    and it is also why the ink measurements exclude guide colours — otherwise a file full of guides
    would pass "artwork reaches the bleed" on the strength of our own lines.
    """
    present = facts.get("guides_present")
    if present is None:
        return None
    if not present:
        return _finding("template_guides", "green", "ok")
    return _finding("template_guides", "red", "present")


def check_artwork_reaches_bleed(facts, expected, material=None):
    """Artwork must run to the page edge, or the trimmed job shows a white sliver.

    `blank_edges_mm` is how much untouched paper sits on each side; anything more than a rounding
    error means the design stops short of the bleed.
    """
    blank = facts.get("blank_edges_mm")
    if blank is None:
        return _finding("bleed_coverage", "info", "unmeasured")
    worst = max(blank)
    if worst <= TOLERANCE_MM:
        return _finding("bleed_coverage", "green", "ok")
    level = "red" if worst > expected["bleed_mm"] / 2 else "amber"
    return _finding("bleed_coverage", level, "short",
                    missing=f"{worst:.1f}", bleed=_mm(expected["bleed_mm"]))


def check_safe_area(facts, expected, material=None):
    """Nothing important outside the safe box, measured as DETAIL in the keep-out ring — a full-bleed
    background is supposed to fill that ring, so any-ink would flag every correct file."""
    intrusion = facts.get("safe_intrusion_mm")
    if intrusion is None:
        return _finding("safe_area", "info", "unmeasured")
    if intrusion <= TOLERANCE_MM:
        return _finding("safe_area", "green", "ok")
    return _finding("safe_area", "amber", "intrusion",
                    regions=facts.get("safe_intrusion_regions_mm"),
                    intrusion=f"{intrusion:.1f}", safe=_mm(expected["safe_mm"]))


def check_resolution(facts, expected, material=None):
    """Resolution floor, when the MATERIAL declares one. No material rule, no finding."""
    floor = (material or {}).get("min_dpi")
    if not floor:
        return None
    measured = facts.get("min_dpi")
    if measured is None:
        return _finding("resolution", "info", "unmeasured")
    if measured >= floor:
        return _finding("resolution", "green", "ok", dpi=_mm(measured))
    # The rectangles of the placements actually below the floor, so the verdict can point at the
    # offending image instead of shrugging at the page.
    low = [p["rect_mm"] for p in (facts.get("image_placements") or [])
           if p.get("dpi") is not None and p["dpi"] < floor and p.get("rect_mm")]
    return _finding("resolution", "red", "low", regions=low or None,
                    dpi=_mm(measured), floor=_mm(floor))


# ── Structure: what the file declares about itself ───────────────────────────

def check_office_origin(facts, expected=None, material=None):
    """A PDF exported from an office application is almost never artwork.

    Magic bytes cannot catch this — a Word document saved as PDF *is* a valid PDF. The producer
    metadata is the reliable tell, and in the in-house engine it caught a real case: a letter to a
    city office sitting among the print files of an order.
    """
    if "producer" not in facts and "creator" not in facts:
        return None
    application = structure.office_producer(facts.get("producer"), facts.get("creator"))
    if not application:
        return None
    return _finding("office_origin", "red", "office", application=application.title())


def check_colour_mode(facts, expected=None, material=None):
    """RGB is a finding only for materials the shop declared CMYK.

    Nothing declared, nothing said: a fully vector file that sets its colours inline declares no
    colour space at all, and a verdict invented from that absence would be a guess (see
    `structure.py`, which states the limit rather than hiding it).
    """
    if (material or {}).get("colour") != "cmyk":
        return None
    spaces = facts.get("colour_spaces")
    if not spaces:
        return None
    rgb = structure.rgb_colour_spaces(spaces)
    if rgb:
        return _finding("colour_mode", "amber", "rgb", spaces=", ".join(rgb[:MAX_NAMES_LISTED]))
    if any("ICCBased" in space for space in spaces):
        return _finding("colour_mode", "info", "icc")
    return _finding("colour_mode", "green", "ok")


def check_spot_inks(facts, expected=None, material=None):
    """Extra INKS should become CMYK. Technical separations must NOT.

    Advising otherwise tells a customer to flatten their own cut path into printed artwork, and in
    the in-house corpus 69 files carry a `Cut` separation that this check used to sweep up with the
    Pantones.
    """
    spots = facts.get("spot_names")
    if not spots:
        return None
    inks = structure.ink_spots(spots)
    if not inks:
        return None
    return _finding("spot_inks", "amber", "found", inks=", ".join(inks[:MAX_NAMES_LISTED]))


def check_cut_path(facts, expected=None, material=None):
    """Whether the die line is present, for materials the shop marked as cut work.

    The reliable signal is the colorant NAME. Stroke-only geometry cannot stand in for it — 88 % of
    PDFs contain stroke-only paths, so it identifies almost nothing. When the cut is drawn in plain
    black or an unnamed Pantone there is genuinely no way to tell it from artwork, and the message
    says that instead of guessing.
    """
    if not (material or {}).get("cut_path"):
        return None
    spots = facts.get("spot_names")
    if spots is None:
        return None
    found = structure.cut_spots(spots)
    if found:
        return _finding("cut_path", "green", "ok", cut=", ".join(found[:MAX_NAMES_LISTED]))
    return _finding("cut_path", "amber", "missing")


def check_fonts_converted(facts, expected=None, material=None):
    """Live text means the text was not converted to curves.

    The inverse of the usual preflight question: not "is the font embedded" but "is there text at
    all". Fires on 35.5 % of real PDFs in the in-house corpus.

    A `/Font` resource alone is not enough, and this tool of all tools has to get that right: its own
    templates are drawn by reportlab, which registers Helvetica whether anything is typed or not. So
    the trigger is a text-showing operator, and the font names are only used to say WHICH.
    """
    fonts = facts.get("fonts")
    if fonts is None:
        return None
    if not fonts or not facts.get("shows_text"):
        return _finding("fonts", "green", "ok")
    # The subset prefix (`AAAAAA+`) is how the FILE names a font, not how a human does.
    readable = [name.split("+")[-1] for name in fonts[:MAX_NAMES_LISTED]]
    return _finding("fonts", "amber", "present", fonts=", ".join(readable))


def check_overprint(facts, expected=None, material=None):
    """Overprint left on can drop an element or shift a colour, and it is almost never intended."""
    if not facts.get("overprint"):
        return None
    return _finding("overprint", "amber", "on")


def check_page_count(facts, expected=None, material=None):
    """Single-page files only — except cut work, where the die line is a separate page by design."""
    pages = facts.get("pages")
    if not pages or pages <= 1:
        return None
    if (material or {}).get("cut_path"):
        return _finding("page_count", "info", "cut_work", pages=pages)
    return _finding("page_count", "amber", "many", pages=pages)


def check_filename(facts, expected=None, material=None):
    """The two naming habits that travel badly through a file server and a RIP.

    A name with both problems reports only the diacritics: the advice is identical, and `ł` is the
    one with a history of arriving mangled.
    """
    if facts.get("has_diacritics"):
        return _finding("filename", "amber", "diacritics")
    if facts.get("extra_dots"):
        return _finding("filename", "amber", "dots")
    return None


RULES = (check_page_size, check_declared_trim, check_template_guides_removed,
         check_artwork_reaches_bleed, check_safe_area, check_resolution,
         check_office_origin, check_colour_mode, check_spot_inks, check_cut_path,
         check_fonts_converted, check_overprint, check_page_count, check_filename)

# Every rule a shop can silence or re-grade, in the order the rules run, mapped to the message code
# that best DESCRIBES it — the sentence a customer would get when the rule fires.
#
# Both halves are explicit on purpose. The id is what a shop's saved severities are keyed on, so
# renaming a function must not silently reset them; and the label code is chosen rather than guessed
# from the message list, because "the first message for this rule" picked "TrimBox is rotated" to
# describe the rule that catches a wrong trim size.
RULE_LABELS = {
    "page_size": "check.page_size.wrong",
    "declared_trim": "check.declared_trim.wrong",
    "template_guides": "check.template_guides.present",
    "bleed_coverage": "check.bleed_coverage.short",
    "safe_area": "check.safe_area.intrusion",
    "resolution": "check.resolution.low",
    "office_origin": "check.office_origin.office",
    "colour_mode": "check.colour_mode.rgb",
    "spot_inks": "check.spot_inks.found",
    "cut_path": "check.cut_path.missing",
    "fonts": "check.fonts.present",
    "overprint": "check.overprint.on",
    "page_count": "check.page_count.many",
    "filename": "check.filename.diacritics",
}
RULE_IDS = tuple(RULE_LABELS)


def run(facts, expected, material=None, levels=None, wording=None):
    """Every rule that applies, worst first, with the shop's severities and wording applied.

    `levels` is `{rule_id: "off"|"info"|"amber"|"red"}` — the shop's judgement, which replaces the
    rule's own for anything that is not a pass. A pass is never re-graded: a shop asking for "fonts:
    red" wants a file WITH fonts flagged red, not a clean file called broken.
    """
    levels = levels or {}
    findings = []
    for rule in RULES:
        try:
            finding = rule(facts, expected, material)
        except Exception as error:                  # noqa: BLE001 — one broken rule is not a verdict
            finding = {"id": getattr(rule, "__name__", "rule"), "level": "info",
                       "code": "check.rule.failed", "values": {"error": str(error)[:120]}}
        if not finding:
            continue
        override = levels.get(finding["id"])
        if override == "off":
            continue
        if override and finding["level"] != "green":
            finding["level"] = override
        findings.append(_worded(finding, wording))
    return sorted(findings, key=lambda f: LEVEL_ORDER.get(f["level"], 2))


def _worded(finding, wording):
    """Attach the sentences for a finding's code, from the shop's wording or the defaults."""
    finding["title"] = messages.render(finding["code"], finding["values"], wording)
    finding["detail"] = messages.render_optional(f"{finding['code']}.detail",
                                                 finding["values"], wording) or ""
    return finding


def summarise(findings, wording=None):
    """One line a customer can read, in the shape the in-house tool already proved useful."""
    for level, code in (("red", "summary.errors"), ("amber", "summary.warnings")):
        hits = [f for f in findings if f["level"] == level]
        if hits:
            return messages.render(code, {"count": len(hits),
                                          "titles": "; ".join(f["title"] for f in hits)},
                                   wording)
    return messages.render("summary.ok", {}, wording)
