"""Phase 2: measure what the ink rules need, by rendering the page.

Three numbers, all in millimetres of the artwork area:

    blank_edges_mm     untouched paper on each side — how far the design stops short of the bleed
    safe_intrusion_mm  how far ink reaches into the keep-out ring outside the safe box
    min_dpi            effective resolution of the artwork, from `structure.placed_images`
    text_min_height_mm the smallest live glyph on the page, at full size (None when there is no text)

Rendering is pypdfium2 (Apache-2.0 / BSD-3), so the measurement layer adds no copyleft dependency.

Resolution is the one number here that is NOT measured off pixels: it comes from the placement
transforms, which `structure.py` walks. Rendering cannot answer it — a page rasterised at 2 px/mm
tells you nothing about the resolution of the image placed on it.

⚠️ THE CONFOUNDER, and why this file is more careful than it looks: our own template guides are ink
too. A customer who leaves the magenta trim line in their export would otherwise measure as "artwork
reaches the edge" and pass a check they should fail.

Colour is the WRONG way to exclude them — measured, not assumed: a 0.75 pt line rendered at 2 px/mm is
about half a pixel, so almost every guide pixel is an antialiased blend matching neither the guide
colour nor paper.

Nor is "is there any ink in this row" enough, which is the trap the first version fell into: a guide
RECTANGLE has vertical sides, so every single row contains ink and a blank template measured exactly
like full-bleed artwork. What separates them is COVERAGE — a guide's vertical sides ink about 0.1 % of
a row, its horizontal line inks one row at 100 %, and real artwork inks most of a row across many
rows. So an edge counts as reached only when a row is inked across most of its width AND stays that
way for a run of rows. Guides are then found by the opposite signature: one well-covered row at an
inset the stamp already told us, with clean paper either side.

The safe area is not measured as "ink in the ring" either, and that was the second conceptual error: a
full-bleed background is SUPPOSED to fill the ring, so any-ink flagged every correct file. The rule is
about type and logos, so what is measured is DETAIL — local contrast — because flat colour has almost
none and lettering has a lot.
"""
from . import structure

# Render fine enough that a millimetre is two pixels, which is well under any tolerance we judge on,
# and coarse enough that a 5 m banner stays a sane raster.
TARGET_PX_PER_MM = 2.0
MAX_RENDER_PX = 4000

# Lighter than this on every channel is paper, not artwork. Generous on purpose: a pale tint that
# reaches the edge IS artwork reaching the edge.
PAPER_MIN_CHANNEL = 250

# An edge counts as reached only when this much ink runs consecutively inward...
SOLID_RUN_MM = 2.0
# ...and only rows inked across at least this share of their length count at all. A guide rectangle's
# vertical sides ink roughly 0.1 % of every row; real artwork covers most of it.
MIN_ROW_COVERAGE = 0.55
# A guide is one well-covered line with clean paper either side; how far either side we look.
GUIDE_CLEARANCE_MM = 1.5
# Local contrast above this counts as DETAIL — lettering, edges, a logo — rather than flat colour.
DETAIL_CONTRAST = 34
# The intrusion map is judged in tiles this big. Chunky on purpose: the regions exist to be PAINTED
# on a preview or a 3D model, and forty confetti rectangles say less than three honest blocks.
REGION_TILE_MM = 10.0
# A tile counts as intruding when this share of it is detail — same calibration idea as the band
# threshold below it: photographic texture sits well under it, lettering sits well over.
REGION_TILE_DETAIL_SHARE = 0.06


def _render(pdf_bytes, page_index, artwork_height_mm=None):
    """(numpy RGB array, pixels-per-mm). The spec strip is cropped off before anything is measured,
    because page furniture is not artwork and would read as ink at the bottom edge."""
    import numpy
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        page = document[page_index]
        width_pt, height_pt = page.get_size()
        width_mm = width_pt * 25.4 / 72
        scale_px_per_mm = min(TARGET_PX_PER_MM, MAX_RENDER_PX / max(width_mm, 1))
        image = page.render(scale=scale_px_per_mm * 25.4 / 72).to_pil().convert("RGB")
    finally:
        document.close()

    array = numpy.asarray(image)
    if artwork_height_mm:
        # The strip sits at the BOTTOM of the page, which is the TOP of nothing in image space —
        # image row 0 is the page top, so the artwork occupies the first `artwork_height_mm`.
        keep_rows = int(round(artwork_height_mm * scale_px_per_mm))
        if 0 < keep_rows < array.shape[0]:
            array = array[:keep_rows, :, :]
    return array, scale_px_per_mm


def _ink(array):
    """Boolean mask of anything darker than paper on any channel."""
    return (array < PAPER_MIN_CHANNEL).any(axis=2)


def _covered(ink, axis):
    """Per-row (or per-column) flags: True where ink covers at least MIN_ROW_COVERAGE of the line."""
    return ink.mean(axis=axis) >= MIN_ROW_COVERAGE


def _detail_mask(array):
    """Where the image has local contrast — the signature of type and logos rather than flat fill."""
    import numpy

    grey = array.astype(numpy.int16).mean(axis=2)
    vertical = numpy.abs(numpy.diff(grey, axis=0, prepend=grey[:1, :]))
    horizontal = numpy.abs(numpy.diff(grey, axis=1, prepend=grey[:, :1]))
    return numpy.maximum(vertical, horizontal) >= DETAIL_CONTRAST


def _first_solid_run(flags, run_length):
    """Index of the first position where `run_length` consecutive entries are True, or None.

    This is what separates artwork from a hairline: a 0.75 pt guide cannot produce two millimetres of
    consecutive inked rows, and real artwork reaching an edge always does.
    """
    import numpy

    if run_length <= 1:
        hits = numpy.flatnonzero(flags)
        return int(hits[0]) if hits.size else None
    window = numpy.convolve(flags.astype(numpy.int16), numpy.ones(run_length, dtype=numpy.int16),
                            mode="valid")
    hits = numpy.flatnonzero(window >= run_length)
    return int(hits[0]) if hits.size else None


def _looks_like_a_guide(flags, inset_px, clearance_px):
    """True when there is a thin inked line at `inset_px` with clean paper on both sides of it."""
    if inset_px <= 0 or inset_px >= len(flags):
        return False
    near = flags[max(0, inset_px - 1):inset_px + 2]
    if not near.any():
        return False
    before = flags[max(0, inset_px - 1 - clearance_px):max(0, inset_px - 1)]
    after = flags[inset_px + 2:inset_px + 2 + clearance_px]
    # A line with paper on both sides is furniture; a line with ink beside it is part of a design.
    return not before.any() and not after.any()


def measure(pdf_bytes, expected, page_index=0):
    """Everything the ink rules need, or Nones with a reason when it cannot be measured.

    `expected` is the stamped geometry, so the measurement knows where the boxes were without
    inferring anything from the file itself.
    """
    scale = expected.get("scale", 1) or 1
    artwork_height_mm = expected["brutto_mm"][1] / scale
    try:
        array, px_per_mm = _render(_bytes(pdf_bytes), page_index, artwork_height_mm)
    except Exception as error:                       # noqa: BLE001 — unmeasurable is an ANSWER
        return {"blank_edges_mm": None, "safe_intrusion_mm": None, "min_dpi": None,
                "guides_present": None, "reason": f"{type(error).__name__}: {error}"[:140]}

    ink = _ink(array)
    height_px, width_px = ink.shape
    run_px = max(2, int(round(SOLID_RUN_MM * px_per_mm)))
    clearance_px = max(1, int(round(GUIDE_CLEARANCE_MM * px_per_mm)))
    rows_inked = _covered(ink, axis=1)
    columns_inked = _covered(ink, axis=0)

    # Guides sit at the netto inset — a thin line with paper either side of it.
    netto_inset_px = int(round((expected["bleed_mm"] / scale) * px_per_mm))
    facts = {"reason": "",
             "guides_present": bool(
                 _looks_like_a_guide(rows_inked, netto_inset_px, clearance_px)
                 or _looks_like_a_guide(columns_inked, netto_inset_px, clearance_px))}

    if not ink.any():
        # A page with no artwork at all: every edge is blank by the full artwork depth.
        facts["blank_edges_mm"] = (width_px / px_per_mm / 2,) * 4
        facts["safe_intrusion_mm"] = 0.0
        facts["min_dpi"] = None
        return facts

    # Each edge: how far in before a SOLID run of artwork starts. None means no solid artwork at all
    # from that side, which is reported as the full half-depth rather than pretended to be zero.
    half_width_mm = width_px / px_per_mm / 2
    half_height_mm = height_px / px_per_mm / 2

    def inward(flags, limit_mm):
        found = _first_solid_run(flags, run_px)
        return limit_mm if found is None else found / px_per_mm

    facts["blank_edges_mm"] = (inward(columns_inked, half_width_mm),
                               inward(rows_inked, half_height_mm),
                               inward(columns_inked[::-1], half_width_mm),
                               inward(rows_inked[::-1], half_height_mm))
    regions, worst = _intrusion_regions(_detail_mask(array), px_per_mm, expected)
    facts["safe_intrusion_mm"] = worst
    facts["safe_intrusion_regions_mm"] = regions
    placements = structure.placed_images(_bytes(pdf_bytes), page_index)
    facts["min_dpi"] = structure.min_significant_dpi(placements)
    # Every placement big enough to judge by, WITH its rectangle — so a low-resolution verdict can
    # point at the image instead of shrugging at the whole page.
    facts["image_placements"] = [
        {"rect_mm": p.get("rect_mm"), "dpi": p["dpi"], "placed_mm": list(p["placed_mm"])}
        for p in structure.significant_placements(placements) if p.get("rect_mm")]
    facts["text_min_height_mm"] = _text_min_height_mm(_bytes(pdf_bytes), page_index, scale)
    return facts


# A glyph box thinner than this is a space, a dot leader or a degenerate char; not a letter to judge.
MIN_GLYPH_PT = 0.5
# Enough characters to cover any real design; a novel pasted into a banner is not the case here.
MAX_CHARS_MEASURED = 20000


def _text_min_height_mm(pdf_bytes, page_index, scale):
    """The smallest live glyph on the page in millimetres AT FULL SIZE, or None with no live text.

    Read from pdfium's text page, so it only sees text that is still text — lettering converted to
    curves is artwork, and its legibility is the designer's call, not a rule's.
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    try:
        textpage = document[page_index].get_textpage()
        try:
            smallest_pt = None
            for index in range(min(textpage.count_chars(), MAX_CHARS_MEASURED)):
                left, bottom, right, top = textpage.get_charbox(index)
                height_pt = top - bottom
                if height_pt < MIN_GLYPH_PT or right - left < MIN_GLYPH_PT:
                    continue
                smallest_pt = height_pt if smallest_pt is None else min(smallest_pt, height_pt)
        finally:
            textpage.close()
    except Exception:                                # noqa: BLE001 — unmeasurable text = no finding
        return None
    finally:
        document.close()
    if smallest_pt is None:
        return None
    return round(smallest_pt * 25.4 / 72 * (scale or 1), 2)


def _ring_depths_mm(expected):
    """The keep-out depth per side, from the artwork edge inward: bleed + that side's safe inset.

    Per side, which is what the 2026-08-25 per-side margins promised and this module deferred:
    a Vento Regular's 140 mm tunnel side is now measured at 140 mm, not at the 30 mm of its
    narrowest neighbour. One shared number still works — all four come out equal.
    """
    scale = expected.get("scale", 1) or 1
    sides = expected.get("safe_sides_mm") or {}
    return {side: (expected["bleed_mm"] + float(sides.get(side, expected["safe_mm"]))) / scale
            for side in ("left", "top", "right", "bottom")}


def _intrusion_regions(detail, px_per_mm, expected):
    """(regions, worst_intrusion_mm): WHERE detail sits in the keep-out ring, and how deep.

    Regions are [x, y, w, h] in artwork millimetres, top-left frame — the same frame the raster,
    the overlay and the 3D model use. The scalar verdict is derived FROM the regions (the deepest
    reach past the safe boundary), so there is one detector, not a band detector for the verdict
    and a tile detector for the picture.

    Tiles rather than connected components on purpose: these rectangles exist to be painted, and a
    logo dissolving into forty confetti boxes reads worse than one honest block. Adjacent marked
    tiles merge row-wise, then equal row-runs merge vertically.
    """
    import numpy

    height_px, width_px = detail.shape
    depths = _ring_depths_mm(expected)
    ring_px = {side: int(round(mm * px_per_mm)) for side, mm in depths.items()}
    if max(ring_px.values()) <= 0:
        return [], 0.0
    if ring_px["left"] + ring_px["right"] >= width_px             or ring_px["top"] + ring_px["bottom"] >= height_px:
        return [], 0.0

    # Detail INSIDE the ring only. The safe interior is blanked so a busy design centre never leaks
    # into a tile that merely straddles the boundary.
    ring_detail = detail.copy()
    ring_detail[ring_px["top"]:height_px - ring_px["bottom"],
                ring_px["left"]:width_px - ring_px["right"]] = False

    tile_px = max(2, int(round(REGION_TILE_MM * px_per_mm)))
    tiles_high = (height_px + tile_px - 1) // tile_px
    tiles_wide = (width_px + tile_px - 1) // tile_px
    marked = numpy.zeros((tiles_high, tiles_wide), dtype=bool)
    for row in range(tiles_high):
        for col in range(tiles_wide):
            patch = ring_detail[row * tile_px:(row + 1) * tile_px,
                                col * tile_px:(col + 1) * tile_px]
            if patch.size and float(patch.mean()) >= REGION_TILE_DETAIL_SHARE:
                marked[row, col] = True
    if not marked.any():
        return [], 0.0

    regions_px = _union_touching(_merge_tiles(marked, tile_px, width_px, height_px), tile_px)
    regions = [[round(x / px_per_mm, 1), round(y / px_per_mm, 1),
                round(w / px_per_mm, 1), round(h / px_per_mm, 1)]
               for x, y, w, h in regions_px]

    # The verdict: the deepest reach past the safe boundary, over every region and every side it
    # touches. Same meaning the band scan had — distance from the detail to the ring's inner edge.
    worst = 0.0
    for x, y, w, h in regions:
        width_mm = width_px / px_per_mm
        height_mm = height_px / px_per_mm
        for side, into in (("left", depths["left"] - x),
                           ("top", depths["top"] - y),
                           ("right", (x + w) - (width_mm - depths["right"])),
                           ("bottom", (y + h) - (height_mm - depths["bottom"]))):
            if depths[side] > 0:
                worst = max(worst, min(into, depths[side]))
    return regions, round(max(0.0, worst), 2)


def _union_touching(rectangles, gap_px):
    """Rectangles closer than one tile fuse into their bounding box, to a fixpoint.

    Row-run stacking alone left confetti: a checkerboard's runs alternate 50-wide and 40-wide, so
    nothing stacked and one solid block came out as eight strips — exactly the failure the plan
    predicted. Painted markers want the honest block. O(n²) per pass, n is single digits here.
    """
    rectangles = list(rectangles)
    fused = True
    while fused:
        fused = False
        for i in range(len(rectangles)):
            for j in range(i + 1, len(rectangles)):
                ax, ay, aw, ah = rectangles[i]
                bx, by, bw, bh = rectangles[j]
                if not (ax - gap_px < bx + bw and bx - gap_px < ax + aw
                        and ay - gap_px < by + bh and by - gap_px < ay + ah):
                    continue
                x = min(ax, bx)
                y = min(ay, by)
                width = max(ax + aw, bx + bw) - x
                height = max(ay + ah, by + bh) - y
                # Fuse only when the pieces genuinely FILL their union. A ring of border tiles
                # passes the touching test all the way around and its bbox is the whole page —
                # drawn on the model, that was one giant frame around everything, which reads as
                # "your entire file is wrong". Hollow unions stay separate strips instead.
                if (aw * ah + bw * bh) < 0.55 * width * height:
                    continue
                rectangles[i] = (x, y, width, height)
                rectangles.pop(j)
                fused = True
                break
            if fused:
                break
    return rectangles


def _merge_tiles(marked, tile_px, width_px, height_px):
    """Marked tiles → few axis-aligned pixel rectangles: row-runs, then equal runs stack."""
    runs_by_row = []
    for row_index, row in enumerate(marked):
        runs, start = [], None
        for col_index, flag in enumerate(list(row) + [False]):
            if flag and start is None:
                start = col_index
            elif not flag and start is not None:
                runs.append((start, col_index))
                start = None
        runs_by_row.append(runs)

    rectangles = []
    open_rects = {}                                   # (col_start, col_end) -> [row_start, row_end)
    for row_index in range(len(runs_by_row) + 1):
        runs = set(runs_by_row[row_index]) if row_index < len(runs_by_row) else set()
        for span, (row_start, _row_end) in list(open_rects.items()):
            if span in runs:
                open_rects[span] = [row_start, row_index + 1]
                runs.discard(span)
            else:
                rectangles.append((span, tuple(open_rects.pop(span))))
        for span in runs:
            open_rects[span] = [row_index, row_index + 1]

    out = []
    for (col_start, col_end), (row_start, row_end) in rectangles:
        x = col_start * tile_px
        y = row_start * tile_px
        out.append((x, y,
                    min(col_end * tile_px, width_px) - x,
                    min(row_end * tile_px, height_px) - y))
    return out


def _bytes(data):
    return bytes(data) if isinstance(data, (bytes, bytearray)) else data.read()
