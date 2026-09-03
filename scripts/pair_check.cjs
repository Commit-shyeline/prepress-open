/* Offline mirror of installCut() in prepress/templates/model3d.html.
   Answers, for an ordered pair (source -> destination), the three questions that must be settled
   BEFORE any animation code: does the destination nest in the source, is the offcut one piece,
   and does the per-piece freeing run monotone to 100%.
   Everything runs in SCENE units (mm * 0.01) — polygon-clipping's tolerances are absolute. */
const fs = require('fs');
const path = require('path');
const clip = require(path.join(__dirname, '..', 'prepress', 'static', 'polygon-clipping.min.js'));

const S = 0.01;
const HERE = __dirname;
const SQ_CM = 100;                 // one scene unit squared = 100 cm²

function ringArea(points) {
    let doubled = 0;
    for (let i = 0; i < points.length; i++) {
        const [x1, y1] = points[i], [x2, y2] = points[(i + 1) % points.length];
        doubled += x1 * y2 - x2 * y1;
    }
    return Math.abs(doubled / 2);
}
function closedRing(ring) {
    const c = ring.slice();
    const [fx, fy] = c[0], [lx, ly] = c[c.length - 1];
    if (fx !== lx || fy !== ly) c.push([fx, fy]);
    return [c];
}
function openRing(ring) {
    const o = ring.slice();
    const [fx, fy] = o[0], [lx, ly] = o[o.length - 1];
    if (fx === lx && fy === ly) o.pop();
    return o;
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
function resamplePath(pathPts, count) {
    const lengths = [0];
    for (let i = 1; i < pathPts.length; i++) {
        lengths.push(lengths[i - 1] + Math.hypot(pathPts[i][0] - pathPts[i - 1][0],
                                                 pathPts[i][1] - pathPts[i - 1][1]));
    }
    const total = lengths[lengths.length - 1];
    const out = [];
    let j = 0;
    for (let k = 0; k < count; k++) {
        const want = total * k / (count - 1);
        while (j < lengths.length - 2 && lengths[j + 1] < want) j++;
        const span = lengths[j + 1] - lengths[j] || 1;
        const t = Math.max(0, Math.min(1, (want - lengths[j]) / span));
        out.push([pathPts[j][0] + (pathPts[j + 1][0] - pathPts[j][0]) * t,
                  pathPts[j][1] + (pathPts[j + 1][1] - pathPts[j][1]) * t]);
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
function sortedRings(shape) {
    return shape.netto.map(points => ({ points, area: ringArea(points) }))
                      .sort((a, b) => b.area - a.area);
}
function outlineOf(shape, scale) {
    const ring = (shape.corpus && shape.corpus.length)
        ? shape.corpus[0] : sortedRings(shape)[0].points;
    return ring.map(([x, y]) => [x * scale, y * scale]);
}
function fitInside(destination, source, preferred) {
    const outside = (ring) => clip.difference(closedRing(ring), closedRing(source))
                                  .reduce((sum, polygon) => sum + ringArea(polygon[0]), 0);
    const NOISE = ringArea(destination) * 2e-4;
    const raw = outside(destination);
    if (!preferred && raw <= NOISE) return { ring: destination, morph: false, raw, NOISE };
    const shape = preferred && preferred.length > 2 ? preferred : destination;
    const centre = [shape.reduce((s, p) => s + p[0] / shape.length, 0),
                    shape.reduce((s, p) => s + p[1] / shape.length, 0)];
    const scaledTo = (k) => shape.map(([x, y]) => [centre[0] + (x - centre[0]) * k,
                                                   centre[1] + (y - centre[1]) * k]);
    let scale = 1;
    if (outside(shape) > NOISE) {
        let fits = 0.05, tooBig = 1;
        for (let i = 0; i < 24; i++) {
            const middle = (fits + tooBig) / 2;
            if (outside(scaledTo(middle)) <= NOISE) fits = middle; else tooBig = middle;
        }
        scale = Math.floor(fits * 1000) / 1000;
    }
    return { ring: scaledTo(scale), morph: true, raw, NOISE, scale };
}

/* The whole of installCut's geometry, up to and including the per-step freed area. */
function analyse(sourceShape, destShape) {
    const fabricTrue = outlineOf(destShape, S);
    const scaled = outlineOf(sourceShape, S);
    const toRight = Math.max(...fabricTrue.map(p => p[0])) - Math.max(...scaled.map(p => p[0]));
    const toBottom = Math.min(...fabricTrue.map(p => p[1])) - Math.min(...scaled.map(p => p[1]));
    const nested = scaled.map(([x, y]) => [x + toRight, y + toBottom]);
    const preferred = (destShape.transform || []).map(([x, y]) => [x * S, y * S]);
    const fit = fitInside(fabricTrue, nested, preferred.length > 2 ? preferred : null);
    const fabric = fit.ring;
    const cutSource = openRing(clip.union(closedRing(nested), closedRing(fabric))
                                   .flat().sort((a, b) => ringArea(b) - ringArea(a))[0]);

    const REAL_PIECE = 0.002;
    const sourceArea = ringArea(cutSource);
    const everything = clip.difference(closedRing(cutSource), closedRing(fabric));
    const waste = everything.filter(p => ringArea(p[0]) / sourceArea >= REAL_PIECE);
    const crumbArea = everything.filter(p => ringArea(p[0]) / sourceArea < REAL_PIECE)
                                .reduce((s, p) => s + ringArea(p[0]), 0);
    const wasteArea = waste.reduce((s, p) => s + ringArea(p[0]), 0);

    const ON_EXISTING_EDGE = 3 * S;
    const shared = fabric.map(p => projectOnRing(cutSource, p).distance <= ON_EXISTING_EDGE);
    const strokes = [];
    const firstShared = shared.indexOf(true);
    if (firstShared === -1) {
        strokes.push(fabric.concat([fabric[0]]));
    } else {
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
    const result = {
        outsideCm2: fit.raw * SQ_CM, morph: fit.morph, morphScale: fit.scale,
        pieces: waste.length, crumbs: everything.length - waste.length,
        crumbMm2: crumbArea * 1e4, offcutCm2: wasteArea * SQ_CM,
        sourceCm2: sourceArea * SQ_CM, destCm2: ringArea(fabric) * SQ_CM,
        strokes: strokes.length, monotone: null, freedPct: null, monotoneDropCm2: 0,
    };
    if (!strokes.length || !waste.length) return result;

    const freeCorner = cutSource.reduce((a, b) => (b[0] - b[1] > a[0] - a[1] ? b : a));
    const toCorner = (p) => (p[0] - freeCorner[0]) ** 2 + (p[1] - freeCorner[1]) ** 2;
    for (const stroke of strokes) {
        if (toCorner(stroke[stroke.length - 1]) > toCorner(stroke[0])) stroke.reverse();
    }
    strokes.sort((a, b) => toCorner(b[0]) - toCorner(a[0]));
    const lengthOf = (r) => r.reduce(
        (sum, p, i) => (i ? sum + Math.hypot(p[0] - r[i - 1][0], p[1] - r[i - 1][1]) : 0), 0);
    const totalLength = strokes.reduce((sum, r) => sum + lengthOf(r), 0) || 1;
    let cutPath = [];
    for (const stroke of strokes) {
        cutPath = cutPath.concat(resamplePath(
            stroke, Math.max(2, Math.round(220 * lengthOf(stroke) / totalLength))));
    }
    if (cutPath.length <= 2) return result;

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
    const stepAt = (headLength) => {
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
    const CUT_FRAMES = 120;
    let previous = -1, monotone = true, worst = 0, last = 0;
    for (let f = 1; f <= CUT_FRAMES; f++) {
        const area = stepAt(Math.round(cutPath.length * f / CUT_FRAMES));
        if (area < previous - 1e-6) { monotone = false; worst = Math.max(worst, previous - area); }
        previous = area;
        last = area;
    }
    result.monotone = monotone;
    result.monotoneDropCm2 = worst * SQ_CM;
    result.freedPct = wasteArea ? (last / wasteArea) * 100 : 0;
    result.cutPoints = cutPath.length;
    return result;
}

const NAMES = {
    '5gIywkF3': 'Regular S', 'Wn5ikpfs': 'Play A', 'Xed1h5yd': 'Play C',
    'GqQ5Lo36': 'Play B', 'FlT8YW5Y': 'Drop',
};
const shapes = {};
for (const token of Object.keys(NAMES)) {
    shapes[token] = JSON.parse(fs.readFileSync(path.join(HERE, 'shape_' + token + '.json'), 'utf8'));
}

const only = process.argv.slice(2);
const tokens = Object.keys(NAMES);
const pairs = [];
if (only.length === 2) pairs.push(only);
else for (const a of tokens) for (const b of tokens) if (a !== b) pairs.push([a, b]);

const HEAD = ['source', 'dest', 'outside cm2', 'morph', 'pieces', 'strokes',
              'offcut cm2', 'crumbs', 'monotone', 'freed %', 'worst dip cm2'];
console.log(HEAD.join('\t'));
for (const [a, b] of pairs) {
    const r = analyse(shapes[a], shapes[b]);
    console.log([NAMES[a], NAMES[b], r.outsideCm2.toFixed(0),
                 r.morph ? (r.morphScale === undefined ? 'yes' : r.morphScale) : '-',
                 r.pieces, r.strokes, r.offcutCm2.toFixed(0),
                 r.crumbs + '/' + r.crumbMm2.toFixed(0) + 'mm2',
                 r.monotone === null ? '-' : (r.monotone ? 'yes' : 'NO'),
                 r.freedPct === null ? '-' : r.freedPct.toFixed(1), r.monotoneDropCm2.toFixed(2)].join('\t'));
}
