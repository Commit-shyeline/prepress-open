"""Growing and shrinking a real outline — "create what we do not have".

A production template rarely draws all three boxes. VENTO S draws its brutto and its cut but not its
safe area, so the safe outline has to be COMPUTED by taking the body edge in by the margin. This is
that computation, and it is the only genuinely mathematical thing in the project.

It works on a flattened polygon, not on the cubics: offsetting a bezier exactly produces a curve of
much higher degree, and nobody needs that when the tolerance a print shop judges on is half a
millimetre. So the outline is flattened FINELY (`FLATTEN_TOLERANCE_MM`), offset, and handed back as a
polyline — which reportlab strokes just as happily.

The method is the angle bisector at each vertex, capped by a miter limit. Two guards, because a wrong
answer here is a wrong cut line:

* a spike whose miter would shoot off to infinity is bevelled instead;
* if the result self-intersects or collapses, this refuses and says so. A safe area that folded
  through itself would be drawn as nonsense on a customer's template, and "I cannot offset this
  shape" is a usable answer where a bow-tie polygon is not.
"""
import math

# Chord tolerance when flattening. 0.05 mm is a tenth of the tolerance any rule judges on, and it
# keeps a 5 m sail well under a few thousand points.
FLATTEN_TOLERANCE_MM = 0.05

# How far a sharp corner may extend, as a multiple of the offset distance. Beyond this the corner is
# cut off square — the standard miter limit, and without it a 5-degree spike offsets to a spear.
MITER_LIMIT = 4.0

# Below this share of the original area the shrink has eaten the shape and the answer is refusal
# rather than a sliver.
MIN_AREA_FRACTION = 0.02

# How much closer than the offset distance a vertex may sit before it is treated as spurious. A
# mitred corner stands FURTHER off, never closer, so this only ever trims the bad ones.
_DISTANCE_TOLERANCE = 0.02


def flatten(entry, tolerance_mm=FLATTEN_TOLERANCE_MM):
    """An outline as a list of points in millimetres, curves subdivided to the tolerance."""
    points = [tuple(entry["start"])]
    here = points[0]
    for segment in entry["segments"]:
        if segment[0] == "l":
            here = tuple(segment[1])
            points.append(here)
            continue
        _kind, one, two, end = segment
        steps = _steps_for(here, one, two, end, tolerance_mm)
        for step in range(1, steps + 1):
            points.append(_bezier(here, one, two, end, step / steps))
        here = tuple(end)
    return _dedupe(points)


def offset_polygon(points, distance_mm):
    """Move every edge outward (positive) or inward (negative). Returns (points, problem).

    `problem` is a sentence for the shop when the shape cannot be offset honestly, and None when it
    can. Callers must check it: drawing a failed offset is worse than drawing nothing.
    """
    if abs(distance_mm) < 1e-9:
        return list(points), None
    ring = _closed_ring(points)
    if len(ring) < 3:
        return None, "Ten obrys ma za mało punktów, żeby go odsunąć."

    # The gross case, refused before any geometry: taking 60 mm off both sides of a 100 mm shape
    # cannot leave anything. An area-sign check misses this — the collapsed ring stays
    # counter-clockwise — so it is caught here on the bounding box, which cannot lie about it.
    if distance_mm < 0:
        width = max(p[0] for p in ring) - min(p[0] for p in ring)
        height = max(p[1] for p in ring) - min(p[1] for p in ring)
        if 2 * abs(distance_mm) >= min(width, height):
            return None, (f"Margines {abs(distance_mm):.0f} mm nie zmieści się w kształcie "
                          f"{width:.0f} × {height:.0f} mm — zostałoby zero.")

    # Work in a known orientation so "outward" means the same thing for every template.
    area = _signed_area(ring)
    if abs(area) < 1e-6:
        return None, "Ten obrys nie zamyka żadnej powierzchni."
    reversed_input = area < 0
    if reversed_input:
        ring = ring[::-1]

    moved = []
    for index, point in enumerate(ring):
        before = ring[index - 1]
        after = ring[(index + 1) % len(ring)]
        shifted = _offset_vertex(before, point, after, distance_mm)
        if shifted is not None:
            moved.append(shifted)

    # Keep only the vertices that really are `distance` inside (or outside) the original. This is the
    # definition of an offset, so it validates rather than guesses — and a loop vertex thrown out
    # into a concave notch fails it, which is how the self-intersections disappear instead of merely
    # being detected.
    kept = _only_truly_offset(moved, ring, distance_mm)
    if len(kept) < 3:
        return None, ("Przy tym marginesie z kształtu nie zostaje użyteczna powierzchnia — "
                      "podaj mniejszy albo wskaż narysowany obszar bezpieczny.")

    new_area = abs(_signed_area(kept))
    if new_area < abs(area) * MIN_AREA_FRACTION:
        return None, ("Przy tym marginesie kształt zapada się w siebie — "
                      "podaj mniejszy albo wskaż narysowany obszar bezpieczny.")
    if reversed_input:
        kept = kept[::-1]
    return kept, None


def _only_truly_offset(moved, original, distance):
    """The offset vertices that really sit `|distance|` from the original outline, on the right side.

    The tolerance is generous on purpose: a mitred corner legitimately stands further off than the
    offset distance, so only vertices that are too CLOSE — or on the wrong side — are dropped.
    """
    inward = distance < 0
    limit = abs(distance) * (1 - _DISTANCE_TOLERANCE)
    kept = []
    for point in moved:
        if _distance_to_ring(point, original) < limit:
            continue
        if inward and not _inside(point, original):
            continue
        if not inward and _inside(point, original):
            continue
        kept.append(point)
    return kept


def _distance_to_ring(point, ring):
    best = float("inf")
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        best = min(best, _distance_to_segment(point, start, end))
        if best == 0.0:
            break
    return best


def _distance_to_segment(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared < 1e-12:
        return math.dist(point, start)
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared
    t = max(0.0, min(1.0, t))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def _inside(point, ring):
    """Ray casting. Good enough: the answer only has to be right away from the boundary, and the
    distance test above has already excluded everything near it."""
    x, y = point
    inside = False
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        if (start[1] > y) != (end[1] > y):
            crossing = start[0] + (y - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
            if x < crossing:
                inside = not inside
    return inside


def all_inside(rings, point):
    """Inside the outer ring and outside every hole — the crossing test taken over all rings.

    Counting crossings against every ring together is the even-odd rule, so a point sitting in a
    hole comes back outside without the holes having to be identified first.
    """
    inside = False
    for ring in rings:
        if _inside(point, ring):
            inside = not inside
    return inside


def largest_inscribed_box(rings, samples=160):
    """The biggest axis-aligned rectangle that fits INSIDE an outline, as (x0, y0, x1, y1) mm.

    A bounding box is the wrong home for anything drawn onto a shaped product. A drop flag's safe
    area is a teardrop, so most of its bounding box is off the fabric, and a spec panel centred in
    that box hangs over the cut line and off the flag (shop rule, 2026-08-31). This answers the
    question actually being asked: where can something go and still be ON the shape.

    Rasterises the outline and takes the largest all-inside rectangle — exact to the grid, and
    indifferent to how concave the shape is. None when nothing fits.
    """
    points = [point for ring in rings for point in ring]
    if len(points) < 3:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return None

    # Square-ish cells, so a 1:3 flag is not sampled coarsely down its long side.
    longest = max(width, height)
    columns = max(8, int(round(samples * width / longest)))
    rows = max(8, int(round(samples * height / longest)))
    cell_w, cell_h = width / columns, height / rows
    # Cell CENTRES: a cell counts only when its middle is genuinely inside the shape.
    mask = [[all_inside(rings, (x0 + (col + 0.5) * cell_w, y0 + (row + 0.5) * cell_h))
             for col in range(columns)] for row in range(rows)]

    best = None                                     # (cells, row, first column, last, height)
    heights = [0] * columns
    for row in range(rows):
        for col in range(columns):
            heights[col] = heights[col] + 1 if mask[row][col] else 0
        # Largest rectangle under this histogram, by the usual stack sweep.
        stack = []
        for col in range(columns + 1):
            current = heights[col] if col < columns else 0
            start = col
            while stack and stack[-1][1] >= current:
                start, tall = stack.pop()
                area = tall * (col - start)
                if best is None or area > best[0]:
                    best = (area, row, start, col, tall)
            stack.append((start, current))
    if not best or not best[0]:
        return None
    _cells, row, first, last, tall = best
    return (x0 + first * cell_w, y0 + (row - tall + 1) * cell_h,
            x0 + last * cell_w, y0 + (row + 1) * cell_h)


def as_segments(points):
    """A polyline back in the segment form the drawing and storage code speaks."""
    return {"start": tuple(points[0]),
            "segments": [("l", tuple(p)) for p in points[1:]],
            "closed": True,
            "width_mm": round(max(p[0] for p in points) - min(p[0] for p in points), 2),
            "height_mm": round(max(p[1] for p in points) - min(p[1] for p in points), 2),
            "origin_mm": (round(min(p[0] for p in points), 2),
                          round(min(p[1] for p in points), 2))}


def offset_outline(entry, distance_mm):
    """The whole job: flatten an outline, offset it, hand back something drawable."""
    points, problem = offset_polygon(flatten(entry), distance_mm)
    if problem:
        return None, problem
    return as_segments(points), None


def clip_to_box(entry, box):
    """Trim an outline down to an axis-aligned rectangle. Returns (entry-or-None, problem-or-None).

    This is how a PER-SIDE safe margin is expressed on a real shape. "110 mm from the left" is not a
    distance to a curve — a mast tunnel is a straight band down one edge, and what the shop means is
    "keep the artwork out of that strip". So the caller offsets by the SMALLEST of the four margins,
    which respects every curve and notch, then trims with the rectangle the four describe.

    Sutherland-Hodgman against four half-planes. A rectangle is convex, so the classic algorithm is
    exact here and cannot produce the degenerate bridges it is known for on concave clip regions.
    """
    points = flatten(entry)
    if len(points) < 3:
        return None, "Ten obrys nie ma powierzchni do przycięcia."
    left, bottom, right, top = box
    if right - left <= 0 or top - bottom <= 0:
        return None, "Marginesy są większe niż sam kształt."
    for keeps, axis, value in ((True, 0, left), (True, 1, bottom),
                               (False, 0, right), (False, 1, top)):
        points = _clip_against(points, keeps, axis, value)
        if len(points) < 3:
            return None, "Marginesy zjadły cały obszar bezpieczny."
    return as_segments(_dedupe(points)), None


def _clip_against(points, keep_above, axis, value):
    """One half-plane: keep what is on the wanted side, and cut the edges that cross the line."""
    inside = ((lambda point: point[axis] >= value) if keep_above
              else (lambda point: point[axis] <= value))
    kept = []
    for index in range(len(points)):
        current = points[index]
        previous = points[index - 1]                 # -1 closes the ring on the first step
        if inside(current):
            if not inside(previous):
                kept.append(_crossing(previous, current, axis, value))
            kept.append(current)
        elif inside(previous):
            kept.append(_crossing(previous, current, axis, value))
    return kept


def _crossing(start, end, axis, value):
    """Where the segment meets the line `axis == value`."""
    span = end[axis] - start[axis]
    ratio = 0.0 if span == 0 else (value - start[axis]) / span
    other = 1 - axis
    landed = start[other] + (end[other] - start[other]) * ratio
    return (value, landed) if axis == 0 else (landed, value)


# ── the geometry ────────────────────────────────────────────────────────────

def _offset_vertex(before, point, after, distance):
    """Where this corner lands when both its edges move by `distance`.

    The offset edges meet on the angle bisector; how far along it depends on the angle, which is what
    makes a sharp corner shoot outward and why the miter limit exists.
    """
    into = _unit(point[0] - before[0], point[1] - before[1])
    out = _unit(after[0] - point[0], after[1] - point[1])
    if into is None or out is None:
        return None
    # RIGHT-hand normals. For the counter-clockwise ring this function normalises to, the LEFT
    # normal points INTO the shape — using it made a negative distance grow a square from 100 mm to
    # 120 mm, caught by the first test written against it.
    normal_in = (into[1], -into[0])
    normal_out = (out[1], -out[0])
    bisector = (normal_in[0] + normal_out[0], normal_in[1] + normal_out[1])
    length = math.hypot(*bisector)
    if length < 1e-9:
        # The edges double back on each other: no bisector exists, so move straight out.
        return (point[0] + normal_in[0] * distance, point[1] + normal_in[1] * distance)
    bisector = (bisector[0] / length, bisector[1] / length)
    # cos of half the turn angle; as the corner sharpens this goes to zero and the miter to infinity.
    half = bisector[0] * normal_in[0] + bisector[1] * normal_in[1]
    reach = distance / half if abs(half) > 1e-9 else distance * MITER_LIMIT
    if abs(reach) > abs(distance) * MITER_LIMIT:
        reach = math.copysign(abs(distance) * MITER_LIMIT, reach)
    return (point[0] + bisector[0] * reach, point[1] + bisector[1] * reach)


def _signed_area(ring):
    total = 0.0
    for index, point in enumerate(ring):
        nxt = ring[(index + 1) % len(ring)]
        total += point[0] * nxt[1] - nxt[0] * point[1]
    return total / 2.0


def _closed_ring(points):
    """Points with any duplicated closing point removed, so every vertex appears once."""
    ring = list(points)
    while len(ring) > 1 and math.dist(ring[0], ring[-1]) < 1e-6:
        ring.pop()
    return ring


def _unit(dx, dy):
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > 1e-9 else None


def _steps_for(start, one, two, end, tolerance_mm):
    """Enough subdivisions for this curve at this tolerance, from the control polygon's length."""
    rough = (math.dist(start, one) + math.dist(one, two) + math.dist(two, end)) or 1.0
    return max(2, min(512, int(math.sqrt(rough / max(tolerance_mm, 0.001)) + 1)))


def _bezier(start, one, two, end, t):
    inverse = 1.0 - t
    a, b = inverse ** 3, 3 * inverse ** 2 * t
    c, d = 3 * inverse * t ** 2, t ** 3
    return (a * start[0] + b * one[0] + c * two[0] + d * end[0],
            a * start[1] + b * one[1] + c * two[1] + d * end[1])


def _dedupe(points):
    out = [points[0]]
    for point in points[1:]:
        if math.dist(point, out[-1]) > 1e-7:
            out.append(point)
    return out
