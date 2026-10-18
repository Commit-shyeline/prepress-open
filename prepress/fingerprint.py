# -*- coding: utf-8 -*-
"""Which template a file belongs to, read off its own die-lines.

A file exported from a design app loses the page stamp and the printed token, and on a sheet of
panels the page size names a whole family (every gate's siatka is a 2300 × 2300 mm page). The
panels themselves are the fingerprint: a template's stored cut outlines have sizes no other die
shares, and a returned file that kept the template layer still carries them. Matching is by outline
size (width × height within a millimetre), which survives any export and costs nothing to compute.

Used by the checker's candidate list (the best match first) and by `/api/identify`, where a shop's
product page asks "is this file the siatka of the product the visitor is looking at?"
"""
from . import lines

SIZE_TOLERANCE_MM = 1.0
PAGE_TOLERANCE_MM = 3.0
# A match worth acting on: this many of the template's panels found, and more than the runner-up.
DECISIVE_MATCHES = 2


def rank(templates, page_mm, uploaded):
    """[{token, name, matched, outlines}, …] best first, for the templates on this page size.

    `uploaded` are the file's closed outlines (outline.candidates); `matched` counts the template's
    cut outlines a same-sized one was found for. Templates on another page size are left out.
    """
    sizes = [(float(o.get("width_mm") or 0), float(o.get("height_mm") or 0))
             for o in uploaded if o.get("closed")]
    ranked = []
    for template in templates:
        page = template.get("page_mm") or (0, 0)
        if (page_mm and (abs(float(page[0]) - page_mm[0]) > PAGE_TOLERANCE_MM
                         or abs(float(page[1]) - page_mm[1]) > PAGE_TOLERANCE_MM)):
            continue
        cuts = [(float(o.get("width_mm") or 0), float(o.get("height_mm") or 0))
                for o in template.get("outlines") or [] if lines.defines_trim(o.get("type"))]
        matched = sum(1 for width, height in cuts
                      if any(abs(width - w) <= SIZE_TOLERANCE_MM and abs(height - h) <= SIZE_TOLERANCE_MM
                             for w, h in sizes))
        ranked.append({"token": template.get("token"), "name": template.get("name"),
                       "matched": matched, "outlines": len(cuts)})
    ranked.sort(key=lambda entry: (-entry["matched"], entry["name"] or ""))
    return ranked


def best(ranked):
    """The one template the file belongs to, or None: a decisive lead over the runner-up."""
    if not ranked or ranked[0]["matched"] < DECISIVE_MATCHES:
        return None
    if len(ranked) > 1 and ranked[1]["matched"] >= ranked[0]["matched"]:
        return None
    return ranked[0]
