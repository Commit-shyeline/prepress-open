/* The flag you last checked, remembered for the hero — and remembered NOWHERE ELSE.
 *
 * The check page already has the customer's artwork as a raster (the check response's
 * `preview_png`, handed to the 3D scene over postMessage). This keeps a small copy of it so the
 * landing page's hero can fly YOUR flag instead of the bundled demo when you come back to it.
 *
 * Three deliberate choices, because this is somebody's unreleased artwork:
 *
 * * sessionStorage, not localStorage and certainly not the server. Per origin AND per session: it
 *   dies with the tab, it is never sent anywhere, and no other visitor can reach it — which is the
 *   only version of this feature that is safe as an open-source default, where the same install
 *   may be a shop counter PC used by one customer after another. The cost is honest: a hero opened
 *   in an unrelated new tab shows the demo, because that tab is a different session and SHOULD be.
 * * downscaled before it is stored. The check's raster is rendered at up to 2800 px for
 *   measurement; the hero wears it on a flag a few hundred pixels tall, behind a quality ladder
 *   that softens it further. Storing the measurement-grade raster would be several megabytes into
 *   a five-megabyte quota, for pixels nobody can see.
 * * every read and write is guarded. Private-mode browsers throw on the storage accessor itself,
 *   the quota throws on write, and a stored value can be anything by the time it is read back. A
 *   hero with no remembered flag is a hero with the demo on it, which is a fine hero.
 */
(function () {
    'use strict';

    // Per session and per origin. The name is namespaced because a shop mounts this app inside its
    // own front door, where the origin is shared with whatever else that front door serves.
    var KEY = 'prepress:last-artwork';
    // Longest edge, in pixels. 1200 covers a full-height hero flag on a 2x display with room to
    // spare; the artwork is mapped across the cloth, not shown flat.
    var MAX_EDGE = 1200;
    // JPEG, not PNG: this is a photographic-scale raster of a print, and PNG of one is roughly ten
    // times the bytes for a difference invisible on moving cloth. 0.85 is where the ringing around
    // hard-edged lettering stops being visible at hero size.
    var QUALITY = 0.85;

    function store() {
        // The ACCESSOR throws in a browser with site data blocked — not just the get/set.
        try {
            return window.sessionStorage;
        } catch (error) {
            return null;
        }
    }

    /* Shrink to MAX_EDGE and re-encode. Async because decoding an image is, and the caller has
       nothing to wait for: this is a nicety on the next page load, never part of the check. */
    function shrink(dataUrl) {
        return new Promise(function (resolve, reject) {
            var image = new Image();
            image.onload = function () {
                var scale = Math.min(1, MAX_EDGE / Math.max(image.width, image.height));
                var canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round(image.width * scale));
                canvas.height = Math.max(1, Math.round(image.height * scale));
                var context = canvas.getContext('2d');
                // The artwork may be transparent where the page is bare, and JPEG has no alpha —
                // so it composites onto white rather than onto the black an empty canvas gives.
                context.fillStyle = '#ffffff';
                context.fillRect(0, 0, canvas.width, canvas.height);
                context.drawImage(image, 0, 0, canvas.width, canvas.height);
                resolve(canvas.toDataURL('image/jpeg', QUALITY));
            };
            image.onerror = function () { reject(new Error('nie udało się odczytać rastra')); };
            image.src = dataUrl;
        });
    }

    /* Remember this artwork for the hero. Returns a promise that never rejects: the caller is in
       the middle of showing a customer their verdict, and storage is not worth interrupting it. */
    function remember(dataUrl) {
        var storage = store();
        if (!storage || typeof dataUrl !== 'string' || dataUrl.indexOf('data:image/') !== 0) {
            return Promise.resolve(false);
        }
        return shrink(dataUrl).then(function (small) {
            storage.setItem(KEY, small);
            return true;
        }).catch(function () {
            // Over quota, or a raster that would not decode. Try to leave nothing half-written:
            // a truncated value would be read back as a broken texture on the next hero.
            try {
                storage.removeItem(KEY);
            } catch (error) { /* nothing more to try */ }
            return false;
        });
    }

    /* The remembered artwork as a data URI, or null. Synchronous — the hero asks for it while
       deciding what to put in its iframe. */
    function recall() {
        var storage = store();
        if (!storage) return null;
        var value;
        try {
            value = storage.getItem(KEY);
        } catch (error) {
            return null;
        }
        // Validated rather than trusted: what comes back is whatever is under that key now, and
        // the 3D scene only accepts a data URI anyway.
        return (typeof value === 'string' && value.indexOf('data:image/') === 0) ? value : null;
    }

    function forget() {
        var storage = store();
        if (!storage) return;
        try {
            storage.removeItem(KEY);
        } catch (error) { /* already gone, for our purposes */ }
    }

    window.PrepressLastArtwork = { remember: remember, recall: recall, forget: forget, KEY: KEY };
}());
