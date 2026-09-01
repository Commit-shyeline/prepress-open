/* The fabric itself: the silhouette mask that gives a flat plane the shape of a real cut piece,
   the ring test that says which side of a cut is waste, and the shears.
 *
 * It held the weave and the cloth material too, for a second page that ran its own copy of the
 * cutting animation. That page is gone (2026-09-01) and the flag scene builds its own material —
 * it needs an aoMap for the reinforcing hem, which the shared one never had — so the two are
 * deleted rather than left here unreachable. Both are in git if that scene ever adopts them.
 */
import * as THREE from 'three';

/* A silhouette canvas: white where there is cloth, black where there is not. Used as an alphaMap
   with alphaTest, which is what lets one rectangular plane wear any cut shape — and, because it is
   a canvas, what lets that shape CHANGE while the scene is running. */
export function silhouette(width = 2048, height = 2048) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const texture = new THREE.CanvasTexture(canvas);
    return { canvas, ctx: canvas.getContext('2d'), texture };
}

/* ONE path across every ring, filled even-odd. A ring set from a boolean operation can hold a
   hole inside an outline, and filling each ring on its own path paints that hole back in. */
export function fillSilhouette(ctx, rings, box, canvas) {
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    for (const ring of rings) {
        ring.forEach(([x, y], i) => {
            // Canvas y grows DOWN, the scene's y grows UP: the one flip, into texture space.
            const u = ((x - box.x0) / (box.x1 - box.x0)) * canvas.width;
            const v = ((box.y1 - y) / (box.y1 - box.y0)) * canvas.height;
            i ? ctx.lineTo(u, v) : ctx.moveTo(u, v);
        });
        ctx.closePath();
    }
    ctx.fill('evenodd');
}

// Ray casting. Shared because both cut surfaces need it to decide which side of a cut is waste.
export function pointInRing(ring, [x, y]) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
}

/* A tapered rod between two points in the XY plane. Endpoints, not a centre and an angle: the
   first pair of shears here was placed the other way and no two parts met — the arms floated a
   quarter of a unit clear of both the screw and the finger rings, and the blades were mounted
   point-first into the pivot (Shyeline spotted the pieces drifting apart, 2026-08-31). */
function rod(from, to, radiusFrom, radiusTo, material) {
    const [ax, ay] = from, [bx, by] = to;
    const length = Math.hypot(bx - ax, by - ay);
    // A cylinder runs along its own +y, which is the `to` end, hence the swapped radii.
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radiusTo, radiusFrom, length, 10),
                                material);
    mesh.rotation.z = Math.atan2(by - ay, bx - ax) - Math.PI / 2;
    mesh.position.set((ax + bx) / 2, (ay + by) / 2, 0);
    return mesh;
}

/* Tailor's shears, in the brass-and-steel a fabric bench actually has, at their real 260 mm at
   scale 1 (one scene unit is 100 mm). The origin IS the cutting point and the tool trails along
   -x, so placing it is a matter of moving that point along the cut and turning +x to the
   direction of travel. */
export function shears(scale = 1) {
    const group = new THREE.Group();
    const steel = new THREE.MeshStandardMaterial({ color: 0xd8dce2, roughness: 0.22,
                                                   metalness: 0.35 });
    const brass = new THREE.MeshStandardMaterial({ color: 0xb08d3f, roughness: 0.34,
                                                   metalness: 0.45 });
    const SCREW = -1.32;
    for (const side of [-1, 1]) {
        // Blade: sharp at the cutting point, thickening back to the screw, the two parted by the
        // few degrees a pair mid-stroke is actually open.
        const tip = [0, side * 0.014];
        const heel = [SCREW, side * 0.036];
        group.add(rod(tip, heel, 0.005, 0.072, steel));

        // Arm: screw to the root of the handle, splaying outwards.
        const root = [-2.08, side * 0.30];
        group.add(rod(heel, root, 0.062, 0.048, brass));

        /* The finger ring is centred one ring-radius further along the arm's own line, so the arm
           ends exactly ON the ring instead of near it. */
        const RING_RADIUS = 0.23;
        const runX = root[0] - heel[0], runY = root[1] - heel[1];
        const run = Math.hypot(runX, runY) || 1;
        const ring = new THREE.Mesh(new THREE.TorusGeometry(RING_RADIUS, 0.05, 10, 24), brass);
        ring.position.set(root[0] + (runX / run) * RING_RADIUS,
                          root[1] + (runY / run) * RING_RADIUS, 0);
        group.add(ring);
    }
    const screw = new THREE.Mesh(new THREE.CylinderGeometry(0.085, 0.085, 0.19, 14), steel);
    screw.rotation.x = Math.PI / 2;
    screw.position.set(SCREW, 0, 0);
    group.add(screw);

    group.scale.setScalar(scale);
    return group;
}
