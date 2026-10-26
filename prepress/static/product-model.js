/* A 3D product model in a three.js scene: loaded from its .glb, its faces found (one per UV island, model-map.js),
   dressed in a page texture by a stored or freshly fitted map. Shared by the admin's pairing view (admin.html) and
   the customer's viewer (/model/<token>, model3d_glb.html), so the two cannot drift.

   The page position of every vertex is written into its OWN attribute (`uv3`, read by the page texture through
   `texture.channel`), and the model's original `uv` stays untouched — that is what a pick on the model reads, so
   a pair clicked after the live preview still names the model's own coordinates. */
import * as THREE from 'three';
import { GLTFLoader } from './GLTFLoader.js';
import { DRACOLoader } from './DRACOLoader.js';
import { mergeVertices } from './BufferGeometryUtils.js';

const maths = window.PrepressModelMap;          // model-map.js, a classic script the page loads first
export const PAGE_CHANNEL = 3;                  // the attribute `uv3`

/** Load the model: { root, meshes, faces, pageMaterial }. `faces` is one entry per UV island (or one per mesh
 * without UVs): { key, mesh, island, name, space, triangles, box } — `box` its UV box, for the automatic fit. */
export async function loadProductModel(url, dracoPath) {
    const loader = new GLTFLoader().setDRACOLoader(new DRACOLoader().setDecoderPath(dracoPath));
    const gltf = await loader.loadAsync(url);
    const meshes = [];
    const seen = new Set();
    gltf.scene.traverse((node) => {
        if (!node.isMesh) return;
        let geometry = node.geometry;
        // Two nodes sharing one geometry each wear their own map; a non-indexed export is one island per
        // triangle until its vertices are merged back together.
        if (seen.has(geometry)) geometry = geometry.clone();
        if (!geometry.index) geometry = mergeVertices(geometry);
        seen.add(geometry);
        node.geometry = geometry;
        const count = geometry.attributes.position.count;
        const index = Array.from(geometry.index.array);
        const space = geometry.attributes.uv ? 'uv' : 'xyz';
        const found = space === 'uv' ? maths.islands(index, count)
            : { ofTriangle: new Int32Array(index.length / 3), count: 1 };
        const source = Float32Array.from(space === 'uv' ? geometry.attributes.uv.array
                                                        : geometry.attributes.position.array);
        meshes.push({ index: meshes.length, name: node.name || `siatka ${meshes.length + 1}`, node, space,
                      ofTriangle: found.ofTriangle, islands: found.count, source, order: null,
                      original: { material: node.material, index } });
    });
    const faces = meshes.flatMap((mesh) => Array.from({ length: mesh.islands }, (_, island) => ({
        key: `${mesh.index}:${island}`, mesh: mesh.index, island, name: mesh.name, space: mesh.space,
        triangles: mesh.ofTriangle.reduce((n, which) => n + (which === island ? 1 : 0), 0),
        box: mesh.space === 'uv' ? maths.islandBox({ source: mesh.source, size: 2, index: mesh.original.index,
                                                     ofTriangle: mesh.ofTriangle, island }) : null,
    })));
    const pageMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.7, metalness: 0 });
    return { root: gltf.scene, meshes, faces, pageMaterial };
}

/** Sit the model on the ground, centred on the origin, whatever origin the export used. Returns its size. */
export function groundModel(root) {
    const bounds = new THREE.Box3().setFromObject(root);
    const size = bounds.getSize(new THREE.Vector3());
    const centre = bounds.getCenter(new THREE.Vector3());
    root.position.set(-centre.x, -bounds.min.y, -centre.z);
    return size;
}

/** Dress the model by `map` ({ faces: [{ mesh, island, affine }] }): every mapped island wears the page material,
 * every other triangle keeps the modeller's own. An empty map undresses it. */
export function dressModel(model, map) {
    const byMesh = new Map();
    for (const face of (map && map.faces) || []) {
        if (!face.affine) continue;
        if (!byMesh.has(face.mesh)) byMesh.set(face.mesh, []);
        byMesh.get(face.mesh).push(face);
    }
    for (const mesh of model.meshes) {
        const geometry = mesh.node.geometry;
        const faces = byMesh.get(mesh.index) || [];
        if (!faces.length) {
            geometry.setIndex(mesh.original.index);
            geometry.clearGroups();
            mesh.node.material = mesh.original.material;
            mesh.order = null;
            continue;
        }
        const size = mesh.space === 'uv' ? 2 : 3;
        const target = new Float32Array(geometry.attributes.position.count * 2);
        const mapped = new Set();
        for (const face of faces) {
            maths.remap({ source: mesh.source, size, index: mesh.original.index, ofTriangle: mesh.ofTriangle,
                          island: face.island, affine: face.affine, target });
            mapped.add(face.island);
        }
        geometry.setAttribute(`uv${PAGE_CHANNEL}`, new THREE.BufferAttribute(target, 2));
        const grouped = maths.regroup(mesh.original.index, mesh.ofTriangle, mapped);
        geometry.setIndex(grouped.index);
        geometry.clearGroups();
        geometry.addGroup(0, grouped.mappedTriangles * 3, 0);
        geometry.addGroup(grouped.mappedTriangles * 3, grouped.index.length - grouped.mappedTriangles * 3, 1);
        mesh.node.material = [model.pageMaterial, mesh.original.material];
        mesh.order = grouped.order;
    }
}

/** Put `texture` (the page, top row first) on every dressed face. */
export function wearPage(model, texture) {
    texture.flipY = false;                      // the glTF frame: v = 0 at the page's top, as model-map.js says
    texture.channel = PAGE_CHANNEL;
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
    // A texture still downloading uploads itself when its image lands; marking it now only warns.
    if (texture.image) texture.needsUpdate = true;
    model.pageMaterial.map = texture;
    model.pageMaterial.needsUpdate = true;
}

/** A numbered checker, 8 × 8 cells with a letter row and a digit column: a mirrored or turned face shows at once,
 * which the print itself hides. Also shows which way is up: row A is the page's top. */
export function checkerTexture(cells = 8, cellPx = 128) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = cells * cellPx;
    const ctx = canvas.getContext('2d');
    ctx.font = `bold ${Math.round(cellPx * 0.42)}px system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (let row = 0; row < cells; row++) {
        for (let col = 0; col < cells; col++) {
            ctx.fillStyle = (row + col) % 2 ? '#d9dde6' : '#f6f7fa';
            ctx.fillRect(col * cellPx, row * cellPx, cellPx, cellPx);
            ctx.fillStyle = '#23262f';
            ctx.fillText(`${String.fromCharCode(65 + row)}${col + 1}`,
                         (col + 0.5) * cellPx, (row + 0.5) * cellPx);
        }
    }
    // An arrow up along the left edge: "góra" with no letters to read.
    ctx.fillStyle = '#d81b60';
    ctx.beginPath();
    ctx.moveTo(cellPx * 0.5, cellPx * 0.15);
    ctx.lineTo(cellPx * 0.2, cellPx * 0.6);
    ctx.lineTo(cellPx * 0.8, cellPx * 0.6);
    ctx.closePath();
    ctx.fill();
    return new THREE.CanvasTexture(canvas);
}

/** What the pointer is on: { key, mesh, island, name, from, point } or null. `from` is in the model's own frame
 * (its UV, or the mesh's local XYZ) — what a pair is fitted on. */
export function pickFace(model, raycaster) {
    const hit = raycaster.intersectObjects(model.meshes.map((mesh) => mesh.node), false)[0];
    if (!hit) return null;
    const mesh = model.meshes.find((one) => one.node === hit.object);
    const triangle = mesh.order ? mesh.order[hit.faceIndex] : hit.faceIndex;
    const island = mesh.space === 'uv' ? mesh.ofTriangle[triangle] : 0;
    let from;
    if (mesh.space === 'uv') {
        from = [hit.uv.x, hit.uv.y];
    } else {
        const local = hit.object.worldToLocal(hit.point.clone());
        from = [local.x, local.y, local.z];
    }
    return { key: `${mesh.index}:${island}`, mesh: mesh.index, island, name: mesh.name, from,
             point: hit.point.clone() };
}

/** The scene's lights, the same three the flag scene uses: flat ambient makes a fabric read as a sticker. */
export function lightScene(scene) {
    scene.add(new THREE.HemisphereLight(0xf4f6ff, 0x3a3c40, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.9);
    sun.position.set(3, 5, 4);
    scene.add(sun);
    const rim = new THREE.DirectionalLight(0xdfe8ff, 0.55);
    rim.position.set(-4, 2, -3);
    scene.add(rim);
}
