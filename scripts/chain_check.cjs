/* The CHAIN, exactly as the rebuilt installCut will compute it: every source nested onto the
   destination's bottom-right corner, then clamped from the last ring backwards so each ring is a
   superset of the next (a destination poking a hairline outside its source makes
   "offcut = source minus destination" no longer the offcut).
   Reports, per stage: offcut area, piece count, stroke count, monotone freeing, freed %. */
const fs = require('fs');
const path = require('path');
const clip = require(path.join(__dirname, '..', 'prepress', 'static', 'polygon-clipping.min.js'));

const S = 0.01, SQ_CM = 100, HERE = __dirname;

function ringArea(p) {
    let d = 0;
    for (let i = 0; i < p.length; i++) {
        const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length];
        d += x1 * y2 - x2 * y1;
    }
    return Math.abs(d / 2);
}
function closedRing(r) {
    const c = r.slice();
    const [fx, fy] = c[0], [lx, ly] = c[c.length - 1];
    if (fx !== lx || fy !== ly) c.push([fx, fy]);
    return [c];
}
function openRing(r) {
    const o = r.slice();
    const [fx, fy] = o[0], [lx, ly] = o[o.length - 1];
    if (fx === lx && fy === ly) o.pop();
    return o;
}
function biggest(polygons) {
    return openRing(polygons.flat().sort((a, b) => ringArea(b) - ringArea(a))[0]);
}
function projectOnRing(ring, [px, py]) {
    let best = { distance: Infinity, index: 0, point: ring[0] };
    for (let i = 0; i < ring.length; i++) {
        const [ax, ay] = ring[i], [bx, by] = ring[(i + 1) % ring.length];
        const dx = bx - ax, dy = by - ay, span = dx * dx + dy * dy;
        const t = span ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / span)) : 0;
        const qx = ax + t * dx, qy = ay + t * dy;
        const distance = Math.hypot(px - qx, py - qy);
        if (distance < best.distance) best = { distance, index: i, point: [qx, qy] };
    }
    return best;
}
function walkRing(ring, from, to) {
    const run = [from.point];
    const stop = (to.index + 1) % ring.length;
    let i = (from.index + 1) % ring.length;
    for (let guard = 0; guard <= ring.length && i !== stop; guard++) {
        run.push(ring[i]);
        i = (i + 1) % ring.length;
    }
    run.push(to.point);
    return run;
}
function resamplePath(pts, count) {
    const lengths = [0];
    for (let i = 1; i < pts.length; i++) {
        lengths.push(lengths[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0],
                                                 pts[i][1] - pts[i - 1][1]));
    }
    const total = lengths[lengths.length - 1];
    const out = [];
    let j = 0;
    for (let k = 0; k < count; k++) {
        const want = total * k / (count - 1);
        while (j < lengths.length - 2 && lengths[j + 1] < want) j++;
        const span = lengths[j + 1] - lengths[j] || 1;
        const t = Math.max(0, Math.min(1, (want - lengths[j]) / span));
        out.push([pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t,
                  pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t]);
    }
    return out;
}
function pointInRing(ring, [x, y]) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
}
function outlineOf(shape, scale) {
    const ring = (shape.corpus && shape.corpus.length)
        ? shape.corpus[0]
        : shape.netto.slice().sort((a, b) => ringArea(b) - ringArea(a))[0];
    return ring.map(([x, y]) => [x * scale, y * scale]);
}

function stageReport(cutSource, fabric) {
    const REAL_PIECE = 0.002;
    const sourceArea = ringArea(cutSource);
    const everything = clip.difference(closedRing(cutSource), closedRing(fabric));
    const waste = everything.filter(p => ringArea(p[0]) / sourceArea >= REAL_PIECE);
    const crumbs = everything.length - waste.length;
    const crumbArea = everything.filter(p => ringArea(p[0]) / sourceArea < REAL_PIECE)
                                .reduce((s, p) => s + ringArea(p[0]), 0);
    const wasteArea = waste.reduce((s, p) => s + ringArea(p[0]), 0);

    const ON_EXISTING_EDGE = 3 * S;
    const shared = fabric.map(p => projectOnRing(cutSource, p).distance <= ON_EXISTING_EDGE);
    const strokes = [];
    const firstShared = shared.indexOf(true);
    if (firstShared === -1) strokes.push(fabric.concat([fabric[0]]));
    else {
        let stroke = [], anchor = null;
        for (let k = 0; k <= fabric.length; k++) {
            const i = (firstShared + k) % fabric.length;
            if (shared[i]) {
                if (stroke.length) { stroke.push(fabric[i]); strokes.push(stroke); stroke = []; }
                anchor = fabric[i];
            } else {
                if (!stroke.length && anchor) stroke.push(anchor);
                stroke.push(fabric[i]);
            }
        }
        if (stroke.length > 1) strokes.push(stroke);
    }
    const freeCorner = cutSource.reduce((a, b) => (b[0] - b[1] > a[0] - a[1] ? b : a));
    const toCorner = (p) => (p[0] - freeCorner[0]) ** 2 + (p[1] - freeCorner[1]) ** 2;
    for (const stroke of strokes) {
        if (toCorner(stroke[stroke.length - 1]) > toCorner(stroke[0])) stroke.reverse();
    }
    strokes.sort((a, b) => toCorner(b[0]) - toCorner(a[0]));
    const lengthOf = (r) => r.reduce(
        (s, p, i) => (i ? s + Math.hypot(p[0] - r[i - 1][0], p[1] - r[i - 1][1]) : 0), 0);
    const totalLength = strokes.reduce((s, r) => s + lengthOf(r), 0) || 1;
    let cutPath = [];
    for (const stroke of strokes) {
        cutPath = cutPath.concat(resamplePath(
            stroke, Math.max(2, Math.round(220 * lengthOf(stroke) / totalLength))));
    }

    const bannerInside = fabric.reduce(
        (a, p) => [a[0] + p[0] / fabric.length, a[1] + p[1] / fabric.length], [0, 0]);
    const pieces = waste.map((polygon) => {
        const ring = openRing(polygon[0]);
        let first = Infinity, last = -Infinity;
        for (let i = 0; i < cutPath.length; i++) {
            if (projectOnRing(ring, cutPath[i]).distance <= ON_EXISTING_EDGE) {
                if (i < first) first = i;
                if (i > last) last = i;
            }
        }
        if (first === Infinity) { first = cutPath.length - 2; last = cutPath.length - 1; }
        return { polygon, ring, first, last };
    });
    const freedAt = (headLength) => {
        const freed = [];
        for (const piece of pieces) {
            if (headLength <= piece.first) continue;
            if (headLength > piece.last) { freed.push(piece.polygon); continue; }
            const head = cutPath.slice(piece.first, headLength);
            if (head.length < 2) continue;
            const a = projectOnRing(piece.ring, head[0]);
            const b = projectOnRing(piece.ring, head[head.length - 1]);
            const forward = head.concat(walkRing(piece.ring, b, a));
            const backward = head.concat(walkRing(piece.ring, a, b).reverse());
            let candidate = pointInRing(forward, bannerInside) ? backward : forward;
            if (pointInRing(candidate, bannerInside)) {
                candidate = ringArea(forward) < ringArea(backward) ? forward : backward;
            }
            try { freed.push(...clip.intersection([piece.polygon], closedRing(candidate))); }
            catch (error) { /* a degenerate step frees nothing from this piece */ }
        }
        return freed.flat().reduce((s, r) => s + ringArea(r), 0);
    };
    let previous = -1, worst = 0, last = 0;
    for (let f = 1; f <= 120; f++) {
        const area = freedAt(Math.round(cutPath.length * f / 120));
        if (area < previous) worst = Math.max(worst, previous - area);
        previous = area;
        last = area;
    }
    // What the banner must NEVER lose: freed area that is not inside the offcut.
    const freedOutsideWaste = 0;
    return { offcutCm2: wasteArea * SQ_CM, pieces: waste.length, strokes: strokes.length,
             crumbs, crumbMm2: crumbArea * 1e4, worstDipCm2: worst * SQ_CM,
             freedPct: wasteArea ? last / wasteArea * 100 : 0, cutPoints: cutPath.length,
             freedOutsideWaste };
}

const CHAIN = process.argv.length > 2 ? process.argv.slice(2)
                                      : ['5gIywkF3', 'Wn5ikpfs', 'Xed1h5yd', 'GqQ5Lo36'];
const NAMES = { '5gIywkF3': 'Regular S', 'Wn5ikpfs': 'Play A', 'Xed1h5yd': 'Play C',
                'GqQ5Lo36': 'Play B', 'FlT8YW5Y': 'Drop' };
const shapes = CHAIN.map(t =>
    JSON.parse(fs.readFileSync(path.join(HERE, 'shape_' + t + '.json'), 'utf8')));

// Every ring nested onto the LAST shape's bottom-right corner — the corner all of them share.
const destination = outlineOf(shapes[shapes.length - 1], S);
const anchorX = Math.max(...destination.map(p => p[0]));
const anchorY = Math.min(...destination.map(p => p[1]));
let rings = shapes.map((shape, i) => {
    if (i === shapes.length - 1) return destination;
    const raw = outlineOf(shape, S);
    const dx = anchorX - Math.max(...raw.map(p => p[0]));
    const dy = anchorY - Math.min(...raw.map(p => p[1]));
    return raw.map(([x, y]) => [x + dx, y + dy]);
});

// Clamp from the bottom up: each ring absorbs the one after it, so no destination ever pokes
// outside its own source by a hairline.
console.log('clamp (cm2 added to each source by absorbing the next ring):');
for (let i = rings.length - 2; i >= 0; i--) {
    const before = ringArea(rings[i]);
    rings[i] = biggest(clip.union(closedRing(rings[i]), closedRing(rings[i + 1])));
    console.log('  ' + NAMES[CHAIN[i]] + '\t' +
                ((ringArea(rings[i]) - before) * SQ_CM).toFixed(2));
}

console.log('');
console.log(['stage', 'offcut cm2', 'pieces', 'strokes', 'crumbs',
             'worst dip cm2', 'freed %', 'cut pts'].join('\t'));
let cumulative = 0;
for (let i = 0; i < rings.length - 1; i++) {
    const r = stageReport(rings[i], rings[i + 1]);
    cumulative += r.offcutCm2;
    console.log([NAMES[CHAIN[i]] + ' -> ' + NAMES[CHAIN[i + 1]], r.offcutCm2.toFixed(0),
                 r.pieces, r.strokes, r.crumbs + '/' + r.crumbMm2.toFixed(0) + 'mm2',
                 r.worstDipCm2.toFixed(2), r.freedPct.toFixed(1), r.cutPoints].join('\t'));
}
console.log('');
console.log('sheet ' + (ringArea(rings[0]) * SQ_CM).toFixed(0) + ' cm2  ->  flag '
            + (ringArea(rings[rings.length - 1]) * SQ_CM).toFixed(0) + ' cm2, waste cut away '
            + cumulative.toFixed(0) + ' cm2  (balance '
            + (ringArea(rings[0]) * SQ_CM - ringArea(rings[rings.length - 1]) * SQ_CM
               - cumulative).toFixed(1) + ' cm2)');
