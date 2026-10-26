/* The admin panel's „Modele 3D" section: import a .glb with its page, then fit the model's faces to the page —
   automatically from its UV islands (model-map.js autoFit), corrected by clicked point pairs where a face came out
   wrong — watching the model wear a lettered checker or the template itself. Saved as the template's `model_map`.

   Classic-script helpers of admin.html (el, esc, messageAt) are globals; three.js and the scene module load only
   when a model is opened, so the panel stays light for everyone who never comes here. */
const maths = window.PrepressModelMap;
const ADMIN_HEADERS = () => ({ 'X-Admin-Token': el('token').value.trim() });
const CLICK_TRAVEL_PX = 5;                     // less than this between down and up is a click, not an orbit
const SLIVER_SHARE = 0.01;                     // an island under 1 % of the largest's UV box is not a printed face
const DOT_COLOURS = ['#d81b60', '#1e88e5', '#43a047', '#fb8c00', '#8e24aa', '#00acc1', '#6d4c41', '#c0ca33'];

let three = null, orbit = null, scene3d = null;   // the lazily imported modules
let view = null;                               // the open pairing view

function minPairs(space) { return space === 'uv' ? 3 : 4; }

async function loadTemplateList() {
    const data = await (await fetch('api/templates')).json();
    return data.templates || [];
}

function renderPickers(templates) {
    const picker = el('mdlTemplate');
    picker.innerHTML = '<option value="">— nowy szablon, ze strony poniżej —</option>'
        + templates.map((t) => `<option value="${esc(t.token)}">${esc(t.name)}${t.has_model ? ' · ma model' : ''}</option>`).join('');
    const withModel = templates.filter((t) => t.has_model);
    el('mdlList').innerHTML = withModel.length ? withModel.map((t) => `
        <div class="cand" data-mdl="${esc(t.token)}">
            <div><b>${esc(t.name)}</b><br><span class="note"><code>${esc(t.token)}</code></span></div>
            <div style="display:flex;gap:6px;justify-content:flex-end">
                <a class="ghost" style="padding:5px 9px;border:1px solid var(--line);border-radius:8px;text-decoration:none;font-size:12.5px"
                   href="model/${encodeURIComponent(t.token)}" target="_blank">Podgląd</a>
                <button class="ghost" data-mdl-fit="${esc(t.token)}">Dopasuj</button>
            </div>
        </div>`).join('') : '<p class="note">Jeszcze żaden szablon nie ma modelu.</p>';
    el('mdlList').querySelectorAll('[data-mdl-fit]').forEach((button) =>
        button.addEventListener('click', () => openPairing(button.dataset.mdlFit)));
}

async function loadMaterials() {
    const data = await (await fetch('api/materials')).json();
    el('mdlMaterial').innerHTML = '<option value="">— bez materiału —</option>'
        + (data.materials || []).map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
}

async function importModel() {
    if (!el('token').value.trim()) return messageAt('msgMdl', 'Podaj token.', false);
    const model = el('mdlFile').files[0];
    if (!model) return messageAt('msgMdl', 'Wybierz plik .glb.', false);
    const body = new FormData();
    body.append('model', model);
    const existing = el('mdlTemplate').value;
    if (existing) {
        body.append('token', existing);
    } else {
        const page = el('mdlPage').files[0];
        if (!page) return messageAt('msgMdl', 'Wgraj stronę szablonu (PDF albo PNG) albo wybierz istniejący szablon.', false);
        body.append('page', page);
        body.append('name', el('mdlName').value.trim());
        body.append('page_number', el('mdlPageNo').value || '1');
        body.append('width_mm', el('mdlW').value);
        body.append('height_mm', el('mdlH').value);
        body.append('material', el('mdlMaterial').value);
    }
    messageAt('msgMdl', 'Wgrywam model…', true);
    const response = await fetch('api/admin/models', { method: 'POST', headers: ADMIN_HEADERS(), body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return messageAt('msgMdl', data.error || 'Nie udało się wgrać modelu.', false);
    messageAt('msgMdl', `Zapisano model dla „${data.template.name}”. Teraz dopasuj go do strony.`, true);
    renderPickers(await loadTemplateList());
    if (typeof loadTemplates === 'function') loadTemplates();
    openPairing(data.template.token);
}

// ── The pairing view ────────────────────────────────────────────────────────

async function openPairing(token) {
    if (!three) {
        [three, orbit, scene3d] = await Promise.all([
            import('three'), import('./OrbitControls.js'), import('./product-model.js')]);
    }
    if (view) view.dispose();
    el('mdlPair').hidden = false;
    el('mdlPair').scrollIntoView({ block: 'start', behavior: 'smooth' });
    const info = await (await fetch(`api/templates/${encodeURIComponent(token)}/model`)).json();
    if (info.error) return messageAt('msgPair', info.error, false);
    const model = await scene3d.loadProductModel(info.glb, new URL('draco/', import.meta.url).href);
    view = buildView(token, info, model);
    // What the file carries wins; a fresh model gets the automatic pass.
    const stored = (info.map && info.map.faces) || [];
    if (stored.length) view.applyStored(stored); else view.autoFit();
    view.render();
}

function buildView(token, info, model) {
    const THREE = three;
    const stage = el('mdlStage');
    stage.innerHTML = '';
    const scene = new THREE.Scene();
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    stage.appendChild(renderer.domElement);
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 500);
    const controls = new orbit.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene3d.lightScene(scene);
    const size = scene3d.groundModel(model.root);
    scene.add(model.root);
    const bounds = new THREE.Box3().setFromObject(model.root);
    const centre = bounds.getCenter(new THREE.Vector3());
    controls.target.copy(centre);
    const distance = Math.max(size.x, size.y, size.z) * 1.9;
    camera.position.set(centre.x + distance * 0.75, centre.y + size.y * 0.45, centre.z + distance * 0.75);
    const markers = new THREE.Group();
    scene.add(markers);
    const checker = scene3d.checkerTexture();
    const sheet = new THREE.TextureLoader().load(`api/templates/${encodeURIComponent(token)}/sheet.png`);
    const pageImg = el('mdlPageImg');
    pageImg.src = `api/templates/${encodeURIComponent(token)}/sheet.png`;
    const [pageW, pageH] = info.page_mm || [1, 1];

    // Per face: { face, printed, pairs: [{from, to, point}], affine, source, residual, turn, mirror }.
    // Biggest UV island first; the slivers — caps, stubs, feet, a column has 144 of them — start unticked,
    // or every one would be stretched over the whole page and the list would be unreadable.
    const area = (box) => (box ? (box[2] - box[0]) * (box[3] - box[1]) : 0);
    const largest = Math.max(...model.faces.map((face) => area(face.box)), 0);
    const faces = [...model.faces].sort((a, b) => area(b.box) - area(a.box) || b.triangles - a.triangles)
        .map((face) => ({ face, printed: area(face.box) >= largest * SLIVER_SHARE || (face.space === 'xyz'),
                          pairs: [], affine: null, source: null, residual: null, turn: 0, mirror: false }));
    const byKey = new Map(faces.map((entry) => [entry.face.key, entry]));
    let pending = null;                        // a model point waiting for its page point
    let texture = 'checker';

    function resize() {
        const width = stage.clientWidth || 1, height = stage.clientHeight || 1;
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }
    window.addEventListener('resize', resize);
    resize();
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });

    // Click = raycast, orbit = not a click: told apart by how far the pointer travelled.
    let down = null;
    renderer.domElement.addEventListener('pointerdown', (event) => { down = [event.clientX, event.clientY]; });
    renderer.domElement.addEventListener('pointerup', (event) => {
        if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > CLICK_TRAVEL_PX) return;
        const box = renderer.domElement.getBoundingClientRect();
        const pointer = new THREE.Vector2(((event.clientX - box.left) / box.width) * 2 - 1,
                                          -((event.clientY - box.top) / box.height) * 2 + 1);
        const raycaster = new THREE.Raycaster();
        raycaster.setFromCamera(pointer, camera);
        const hit = scene3d.pickFace(model, raycaster);
        if (!hit) return;
        pending = hit;
        drawMarkers();
        status(`Punkt na „${hit.name}” (wyspa ${hit.island + 1}). Teraz kliknij ten sam punkt na stronie.`);
    });
    el('mdlPageBox').addEventListener('click', (event) => {
        if (!pending) return status('Najpierw kliknij punkt na modelu.');
        const box = pageImg.getBoundingClientRect();
        const to = [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height];
        if (to[0] < 0 || to[0] > 1 || to[1] < 0 || to[1] > 1) return;
        const entry = byKey.get(pending.key);
        entry.pairs.push({ from: pending.from, to, point: pending.point.toArray() });
        pending = null;
        refit(entry);
        render();
    });

    function status(text) { el('mdlStatus').textContent = text; }

    function refit(entry) {
        const needed = minPairs(entry.face.space);
        if (entry.pairs.length < needed) {
            // Not enough pairs yet: the automatic fit, if any, stays on until the pairs can replace it.
            status(`${entry.pairs.length} z ${needed} par na „${entry.face.name}”.`);
            return;
        }
        const fit = maths.fitAffine(entry.pairs.map((pair) => ({ from: pair.from, to: pair.to })));
        if (!fit) {
            status('Te punkty leżą na jednej linii — kliknij punkt w innym miejscu tej ściany.');
            return;
        }
        Object.assign(entry, { affine: fit.affine, source: 'pairs', residual: fit.residual, turn: null, mirror: null });
        status(`„${entry.face.name}”: dopasowane z ${entry.pairs.length} par, błąd ${residualMm(entry)} mm.`);
    }

    function residualMm(entry) {
        if (!entry.residual) return '—';
        return Math.max(entry.residual[0] * pageW, entry.residual[1] * pageH).toFixed(1);
    }

    function autoFit() {
        const fits = maths.autoFit(faces.filter((entry) => entry.printed).map((entry) => ({ key: entry.face.key, box: entry.face.box })),
                                   info.boxes || []);
        for (const entry of faces) {
            entry.pairs = [];
            const fit = fits.find((one) => one.key === entry.face.key);
            Object.assign(entry, fit ? { affine: fit.affine, source: 'auto', residual: null, turn: fit.turn, mirror: fit.mirror }
                                     : { affine: null, source: null, residual: null });
        }
        status(fits.length ? `Dopasowano automatycznie ${fits.length} ścian po kształcie wysp UV — sprawdź szachownicę: litery mają czytać się wprost, A u góry.`
                           : 'Ten model nie ma współrzędnych UV — zaznacz po 4 punkty na każdej płaskiej ścianie.');
    }

    function applyStored(stored) {
        for (const entry of faces) {
            const kept = stored.find((one) => one.mesh === entry.face.mesh && one.island === entry.face.island);
            entry.printed = !!kept;
            if (kept) Object.assign(entry, { affine: kept.affine, source: kept.source || 'stored', residual: kept.residual || null,
                                            turn: kept.turn ?? null, mirror: kept.mirror ?? null, pairs: kept.pairs || [] });
        }
        status('Wczytano zapisane mapowanie.');
    }

    function currentMap() {
        return { faces: faces.filter((entry) => entry.printed && entry.affine).map((entry) => ({
            mesh: entry.face.mesh, island: entry.face.island, name: entry.face.name, space: entry.face.space,
            affine: entry.affine, source: entry.source, residual: entry.residual, turn: entry.turn, mirror: entry.mirror,
            pairs: entry.pairs,
        })) };
    }

    function drawMarkers() {
        markers.clear();
        const radius = Math.max(size.x, size.y, size.z) * 0.012;
        const dot = (point, colour) => {
            const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 12, 10),
                                        new THREE.MeshBasicMaterial({ color: colour }));
            mesh.position.fromArray(point);
            markers.add(mesh);
        };
        faces.forEach((entry, i) => entry.pairs.forEach((pair) => dot(pair.point, DOT_COLOURS[i % DOT_COLOURS.length])));
        if (pending) dot(pending.point.toArray(), '#ffffff');
        el('mdlPageDots').innerHTML = faces.flatMap((entry, i) => entry.pairs.map((pair) =>
            `<i style="left:${pair.to[0] * 100}%;top:${pair.to[1] * 100}%;background:${DOT_COLOURS[i % DOT_COLOURS.length]}"></i>`)).join('');
    }

    function render() {
        scene3d.dressModel(model, currentMap());
        scene3d.wearPage(model, texture === 'checker' ? checker : sheet);
        drawMarkers();
        el('mdlFaces').innerHTML = faces.map((entry, i) => `
            <div class="cand" data-face="${esc(entry.face.key)}">
                <div><span class="swatch" style="background:${DOT_COLOURS[i % DOT_COLOURS.length]}"></span>
                    <b>${esc(entry.face.name)}</b> · wyspa ${entry.face.island + 1}
                    <span class="note">· ${entry.face.triangles} trójkątów · ${entry.face.space === 'uv' ? 'UV' : 'bez UV: rzut płaski'}</span><br>
                    <span class="note">${entry.source === 'auto' ? `automatycznie${entry.turn ? `, obrót ${entry.turn * 90}°` : ''}${entry.mirror ? ', lustro' : ''}`
                        : entry.source === 'pairs' ? `z ${entry.pairs.length} par, błąd ${residualMm(entry)} mm`
                        : entry.source ? 'zapisane' : 'niedopasowana'}${entry.pairs.length && entry.source !== 'pairs' ? ` · ${entry.pairs.length} z ${minPairs(entry.face.space)} par` : ''}</span></div>
                <div style="display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap">
                    <label class="check" style="margin:0"><input type="checkbox" data-printed="${esc(entry.face.key)}"${entry.printed ? ' checked' : ''}> drukowana</label>
                    <button class="ghost" data-clear="${esc(entry.face.key)}" style="padding:4px 9px;font-size:12px">wyczyść pary</button>
                </div>
            </div>`).join('');
        el('mdlFaces').querySelectorAll('[data-printed]').forEach((box) => box.addEventListener('change', () => {
            byKey.get(box.dataset.printed).printed = box.checked;
            render();
        }));
        el('mdlFaces').querySelectorAll('[data-clear]').forEach((button) => button.addEventListener('click', () => {
            const entry = byKey.get(button.dataset.clear);
            entry.pairs = [];
            if (entry.source === 'pairs') Object.assign(entry, { affine: null, source: null, residual: null });
            render();
        }));
    }

    async function save() {
        if (!el('token').value.trim()) return messageAt('msgPair', 'Podaj token.', false);
        const response = await fetch(`api/admin/templates/${encodeURIComponent(token)}/model-map`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json', ...ADMIN_HEADERS() },
            body: JSON.stringify(currentMap()),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) return messageAt('msgPair', data.error || 'Nie udało się zapisać.', false);
        messageAt('msgPair', `Zapisano mapowanie: ${currentMap().faces.length} ścian. Podgląd: model/${token}`, true);
    }

    function dispose() {
        renderer.setAnimationLoop(null);
        renderer.dispose();
        window.removeEventListener('resize', resize);
        el('mdlPageDots').innerHTML = '';
    }

    return {
        render, autoFit, applyStored, save, dispose,
        setTexture(which) { texture = which; render(); },
    };
}

if (el('mdlImport')) {
    el('mdlImport').addEventListener('click', importModel);
    el('mdlTemplate').addEventListener('change', () => { el('mdlNewFields').hidden = !!el('mdlTemplate').value; });
    el('mdlAuto').addEventListener('click', () => { if (view) { view.autoFit(); view.render(); } });
    el('mdlSave').addEventListener('click', () => { if (view) view.save(); });
    document.querySelectorAll('[name="mdlTexture"]').forEach((radio) =>
        radio.addEventListener('change', () => { if (view && radio.checked) view.setTexture(radio.value); }));
    loadTemplateList().then(renderPickers);
    loadMaterials();
}
