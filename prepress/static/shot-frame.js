/* `?shot=1` catalogue stills: what a caller needs to stand a person beside the product at true
   scale — the ground line under the product, its left edge and how many pixels one metre is, all in
   page pixels — written to `data-shot` on the body. One copy for the flag scene (model3d.html), the
   GLB viewer (model3d_glb.html) and the banner scene (scene.html); the GLB viewer's own had measured
   its bounds before the −12° turn. */

/* `foot`: the product's lowest left point in scene units (a THREE.Vector3); `metreUnits`: one metre in
   those units; `width` × `height`: the picture's pixels; `heightM`: the product's height in metres. */
export function shotData(camera, foot, metreUnits, width, height, heightM) {
    camera.updateMatrixWorld();             // project() reads the matrix, which only a render would refresh
    const toPx = (v) => {
        const p = v.clone().project(camera);
        return [(p.x + 1) / 2 * width, (1 - p.y) / 2 * height];
    };
    const [left, ground] = toPx(foot);
    const [, metre] = toPx(foot.clone().setY(foot.y + metreUnits));
    return JSON.stringify({
        left: Math.round(left), ground: Math.round(ground),
        pxPerMetre: +(ground - metre).toFixed(2), heightM: +heightM.toFixed(3),
    });
}
