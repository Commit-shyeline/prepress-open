"""The web app: a generator page for customers, an admin panel for the print shop.

Two surfaces, deliberately different in what they trust:

* `/` needs no login. A customer picks a material, types sizes, queues them and downloads a template.
  Everything it can do is read materials and draw PDFs.
* `/admin` edits the RULES, so it is gated on a token from the environment. If `PREPRESS_ADMIN_TOKEN`
  is not set the panel refuses to work at all rather than falling back to something guessable — an
  unauthenticated panel that edits print rules is the kind of thing that ships and then bites.

No database: materials live in one JSON file that reloads on change (see materials.py).
"""
import base64
import re
import functools
import io
import logging
import os
import secrets
import threading
import time

from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_file)
from urllib.parse import quote
from werkzeug.utils import secure_filename

from . import (demo_artwork, from_template, generate, identify, item, lines, materials, measure,
               messages, named_size, offset, outline, raster, rules, shape, structure)

logger = logging.getLogger(__name__)

app = Flask(__name__)
# A template PDF request carries only JSON; the upload path is the only one that takes bytes.
# 512 MB: flattened flag PDFs routinely pass 64 MB, and the first customer to hit the old cap
# got an HTML 413 the frontend could not parse. The matching errorhandler keeps the answer JSON.
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024


@app.errorhandler(413)
def upload_too_large(_error):
    return jsonify({"error": "Plik jest za duży — limit to 512 MB. Zmniejsz PDF "
                             "(spłaszczone bitmapy, kompresja) i spróbuj ponownie."}), 413

# ── Mounting under a path, and the optional session gate ─────────────────────
# Both exist for the same situation: this app published behind somebody else's front door rather
# than at the root of its own host. Neither changes anything when unset, which is the standalone
# default the tests and a fresh clone run under.

BASE_PATH_ENV = "PREPRESS_BASE_PATH"
# The shop's session secret. Set it and every endpoint that DOES work (uploads a file, builds a
# template, reads geometry) demands a signed bearer token; leave it unset and the app is open, as a
# public tool should be. Pages themselves stay reachable either way — the page tells the visitor to
# log in, which is friendlier than a bare 401 from a URL they were given.
SESSION_SECRET_ENV = "PREPRESS_SESSION_SECRET"
# Where an unauthenticated visitor is sent. `{next}` is replaced with the page they wanted.
LOGIN_URL_ENV = "PREPRESS_LOGIN_URL"
# Where the issuing app keeps the token in the browser. Its name, not ours — a shop bolting this
# behind an existing login already has a key, and the page has to read the same one.
SESSION_KEY_ENV = "PREPRESS_SESSION_STORAGE_KEY"
# Where a customer sends the checked files. Shop data, like the brand: no address, no button.
ORDER_EMAIL_ENV = "PREPRESS_ORDER_EMAIL"
# The size a PUBLIC upload can be, in MB, when something in front of this app (a CDN, a tunnel) caps
# requests below MAX_CONTENT_LENGTH. Told to the page so a customer hears the limit BEFORE the upload,
# not as a bare 413 after it. Unset: the app's own cap. Requests through an open door (see
# PREPRESS_OPEN_HEADER) are not behind that front, so they get the app's cap too.
PUBLIC_UPLOAD_LIMIT_ENV = "PREPRESS_PUBLIC_UPLOAD_LIMIT_MB"
# Files the customer has ALREADY put on the shop's own file server (an FTP the shop publishes,
# a share) are checked by PATH instead of being uploaded again — the way round a CDN's upload cap.
# `;`-separated roots; the pasted path is resolved FIRST and then tested against a root, so `..`
# cannot walk out, and nothing outside the roots is even tested for existence. Unset: no path check.
PATH_ROOTS_ENV = "PREPRESS_PATH_ROOTS"
# What the page tells a customer about where to put the file. Either a whole sentence in
# PREPRESS_PATH_HINT, or just the FTP host and login in ASCII — the sentence is then written here,
# because a Windows .bat hands non-ASCII env values over in the console code page and „hasło"
# arrived as „has┼éo" (2026-09-02).
PATH_HINT_ENV = "PREPRESS_PATH_HINT"
FTP_HOST_ENV = "PREPRESS_FTP_HOST"
FTP_LOGIN_ENV = "PREPRESS_FTP_LOGIN"
# Only artwork is read by path. Anything else on the share — an installer somebody uploaded, a
# spreadsheet — is refused by NAME before a byte of it is read into memory.
PATH_CHECK_EXTENSIONS = {".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png"}
# A request header whose PRESENCE means "this came through a door that needs no login" — a LAN
# entry with no login page of its own, say. Name the header here; the proxy must SET it on that
# entry and STRIP it on every public one, because a browser can send any header it likes.
OPEN_HEADER_ENV = "PREPRESS_OPEN_HEADER"
DEFAULT_SESSION_KEY = "session_token"
# The bar at the top of every page. A shop's mark belongs to the shop, so the logo is a URL the
# deployment supplies rather than a file committed here; with none set the bar wears the name.
BRAND_NAME_ENV = "PREPRESS_BRAND_NAME"
BRAND_LOGO_ENV = "PREPRESS_BRAND_LOGO"
BRAND_ICON_ENV = "PREPRESS_BRAND_ICON"
# Somewhere to say "this person is using the checker", for a shop that wants to know. A URL, so
# the app stays free of anyone's alerting: whatever is listening decides what an alert means.
VISIT_WEBHOOK_ENV = "PREPRESS_VISIT_WEBHOOK"
# One ping per identity per this many minutes. A customer checking eight files is one visit.
VISIT_QUIET_MINUTES = 30
_visit_last = {}
# Subjects the issuing app reserves for itself; a token carrying one is not a customer.
RESERVED_SUBJECTS = {"__status_admin__"}


def base_path():
    """The prefix this app is mounted under, without a trailing slash ('' when at the root).

    Read from the proxy's `X-Forwarded-Prefix` first so one process can serve both the LAN root and
    a mounted copy, then from the environment for a fixed deployment.
    """
    forwarded = (request.headers.get("X-Forwarded-Prefix") or "").strip() if request else ""
    return (forwarded or os.environ.get(BASE_PATH_ENV) or "").rstrip("/")


def asset(name):
    """A /static URL stamped with the file's own mtime.

    Flask serves static files with `max-age=14400`, so a browser that opened the page in the last
    four hours keeps its old copy. That is merely stale for CSS; for an ES module it is fatal —
    adding one export to `fabric.js` made every cached visitor's hero throw "does not provide an
    export named 'pointInRing'" and render nothing at all. A changed file gets a new URL here, so
    no cache between here and the customer can serve a version the page was not written for.
    """
    path = os.path.join(os.path.dirname(__file__), "static", name)
    try:
        stamp = int(os.path.getmtime(path))
    except OSError:
        stamp = 0                       # a name we do not ship: let the 404 say so plainly
    return "%s/static/%s?v=%d" % (base_path(), name, stamp)


@app.context_processor
def _inject_base_path():
    """`base` in every template, so a page's own links survive being mounted under a prefix."""
    return {"base": base_path(), "asset": asset,
            "login_url": _login_url(), "gated": _gated(),
            "session_key": _session_key(),
            "order_email": (os.environ.get(ORDER_EMAIL_ENV) or "").strip(),
            "upload_limit_mb": _upload_limit_mb(),
            "path_hint": _path_hint(),
            "brand_name": os.environ.get(BRAND_NAME_ENV) or "prepress-open",
            "brand_logo": os.environ.get(BRAND_LOGO_ENV) or "",
            "brand_icon": os.environ.get(BRAND_ICON_ENV) or ""}


def _session_secret():
    return (os.environ.get(SESSION_SECRET_ENV) or "").strip()


def _session_key():
    return os.environ.get(SESSION_KEY_ENV) or DEFAULT_SESSION_KEY


def _login_url():
    return (os.environ.get(LOGIN_URL_ENV) or "").strip()


def _opened_by_proxy():
    header = (os.environ.get(OPEN_HEADER_ENV) or "").strip()
    return bool(header) and bool(request.headers.get(header))


def _gated():
    """Does THIS request have to show a session token?"""
    return bool(_session_secret()) and not _opened_by_proxy()


def _path_hint():
    if not _path_roots():
        return ""
    custom = (os.environ.get(PATH_HINT_ENV) or "").strip()
    if custom:
        return custom
    host = (os.environ.get(FTP_HOST_ENV) or "").strip()
    login = (os.environ.get(FTP_LOGIN_ENV) or "").strip()
    if not host:
        return ""
    who = f", login {login}" if login else ""
    return f"Serwer FTP: {host}{who}, hasło jak w instrukcji „Jak przygotować pliki”"


def _path_roots():
    return [root.strip() for root in (os.environ.get(PATH_ROOTS_ENV) or "").split(";")
            if root.strip()]


class PathRefused(ValueError):
    """The pasted path is not a file we may read; the message is for the customer."""


def resolve_shared_path(raw_path):
    """An existing FILE under one of the roots, or raise PathRefused.

    Accepts what a customer is likely to paste: `MERA/baner.pdf`, `MERA\\baner.pdf`, the full
    `ftp://host/MERA/baner.pdf` their client shows, or a full UNC/drive path that already lies under
    a root. The allowlist is applied to the RESOLVED path (symlinks, `..` and drive letters gone).
    """
    text = (raw_path or "").strip().strip('"').strip()
    if not text:
        raise PathRefused("Podaj ścieżkę do pliku.")
    if "://" in text:                                # ftp://host/dir/file → dir/file
        text = text.split("://", 1)[1].split("/", 1)[1] if "/" in text.split("://", 1)[1] else ""
    text = text.replace("/", "\\")
    roots = [os.path.realpath(root) for root in _path_roots()]
    candidates = [text] + [os.path.join(root, text.lstrip("\\")) for root in roots]
    for candidate in candidates:
        try:
            real = os.path.realpath(candidate)
        except (OSError, ValueError):
            continue
        inside = any(os.path.normcase(real).startswith(os.path.normcase(root) + os.sep)
                     for root in roots)
        if inside and os.path.isfile(real):
            return real
    raise PathRefused("Nie znajdujemy takiego pliku na naszym serwerze. Sprawdź ścieżkę — "
                      "podaj ją tak, jak widzisz ją w programie FTP, np. MojaFirma/baner.pdf.")


def _upload_limit_mb():
    """The largest upload THIS request's door accepts, in whole megabytes."""
    own = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    public = (os.environ.get(PUBLIC_UPLOAD_LIMIT_ENV) or "").strip()
    if not public or _opened_by_proxy():
        return own
    try:
        return min(own, int(public))
    except ValueError:
        return own


def session_identity():
    """Who the bearer token says this is, or None. Always None when no secret is configured."""
    secret = _session_secret()
    if not secret:
        return None
    header = request.headers.get("Authorization", "")
    token = header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""
    # A browser NAVIGATION (a template download, the 3D page, the generator) carries no
    # Authorization header and never can; the bar mirrors the session token into a same-origin
    # cookie of the same name so those requests are somebody too — the access log read every
    # download as "anonymous" (Shyeline, 2026-09-03). Same token, same verification.
    if not token:
        token = (request.cookies.get(_session_key()) or "").strip()
    if not token:
        return None
    try:
        import jwt
    except ImportError:                             # pragma: no cover — gate misconfigured
        logger.error("%s is set but PyJWT is not installed; refusing every request",
                     SESSION_SECRET_ENV)
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception as error:                      # noqa: BLE001 — any bad token is just a refusal
        logger.info("session token rejected: %s", error)
        return None
    subject = payload.get("nip") or payload.get("sub")
    if not subject or subject in RESERVED_SUBJECTS:
        return None
    return subject


def require_session(view):
    """Refuse the endpoint unless a valid session token is presented — when a secret is configured.

    Wraps the endpoints that DO something. Deliberately not applied to the pages: a visitor who
    followed a link should meet the page and be told to log in, not a bare JSON 401.
    """
    @functools.wraps(view)
    def guarded(*args, **kwargs):
        if _gated() and not session_identity():
            # A browser NAVIGATION (a pasted or e-mailed link) carries no Authorization header and
            # never can, so handing it raw JSON is a dead end — send it to the login instead.
            # "text/html" spelled out, not `accept_html` — that is also true for the `*/*` this
            # page's own fetch() sends, and a fetch silently following a redirect to the
            # login page reads as success instead of a refusal.
            if _login_url() and "text/html" in request.headers.get("Accept", ""):
                back = quote(base_path() + request.full_path.rstrip("?"), safe="")
                return redirect(_login_url().replace("{next}", back), code=302)
            return jsonify({"error": "Zaloguj się, żeby sprawdzić plik.",
                            "login": _login_url()}), 401
        return view(*args, **kwargs)
    return guarded


def announce_visit(who, page):
    """Tell the configured webhook that someone is using this, at most once per quiet window.

    Fire and forget, on its own thread, wrapped in every guard there is: a shop's alerting being
    down is not a reason a customer cannot check a file.
    """
    url = (os.environ.get(VISIT_WEBHOOK_ENV) or "").strip()
    if not url or not who:
        return
    import time
    now = time.time()
    if now - _visit_last.get(who, 0) < VISIT_QUIET_MINUTES * 60:
        return
    _visit_last[who] = now

    def post():
        import json as _json
        import urllib.request
        try:
            body = _json.dumps({"event": "visit", "who": who, "page": page}).encode()
            request_ = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request_, timeout=5).close()
        except Exception as error:                  # noqa: BLE001 — never the customer's problem
            logger.warning("visit webhook failed: %s", error)

    import threading
    threading.Thread(target=post, daemon=True).start()


@app.route("/api/session")
def api_session():
    """Whether this caller is logged in — what the pages ask before offering to do anything."""
    who = session_identity()
    if who:
        announce_visit(who, request.headers.get("Referer") or "/")
    return jsonify({"gated": _gated(),
                    "authenticated": bool(who) or not _gated(),
                    "login": _login_url()})


# ── Access log ──────────────────────────────────────────────────────────────
# Waitress logs nothing per request, so the console of a public checker stayed blank while
# customers downloaded templates and ran checks. One line per request: who (the session's
# NIP, or the door), from where, what, how it went, how long. Static assets are left out.

def _client_ip():
    """The real client behind the proxies: first hop of X-Forwarded-For, else the socket."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "-"


@app.before_request
def _start_clock():
    request._started = time.perf_counter()


@app.after_request
def _access_log(response):
    if not request.path.startswith(f"{base_path()}/static/"):
        elapsed_ms = (time.perf_counter() - getattr(request, "_started", time.perf_counter())) * 1000
        logger.info("%s %s %s %s %d %.0fms", _client_ip(), _caller(), request.method,
                    request.full_path.rstrip("?"), response.status_code, elapsed_ms)
    return response


ADMIN_TOKEN_ENV = "PREPRESS_ADMIN_TOKEN"
MAX_QUEUE_ITEMS = 50


def admin_token():
    return (os.environ.get(ADMIN_TOKEN_ENV) or "").strip()


def require_admin(view):
    """Fail closed: no configured token means the panel is unavailable, not open."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        expected = admin_token()
        if not expected:
            return jsonify({"error": f"Admin panel is disabled: set {ADMIN_TOKEN_ENV} and restart."}), 503
        supplied = (request.headers.get("X-Admin-Token")
                    or (request.get_json(silent=True) or {}).get("token", ""))
        # Constant-time compare so a wrong token cannot be found a character at a time.
        import hmac
        if not hmac.compare_digest(str(supplied), expected):
            return jsonify({"error": "Wrong admin token."}), 403
        return view(*args, **kwargs)
    return wrapped


# ── Customer surface ────────────────────────────────────────────────────────

@app.route("/")
def generator_page():
    return render_template("generator.html", materials=materials.load())


@app.route("/api/materials")
def api_materials():
    """Public on purpose: a customer choosing a material should see the rules it imposes."""
    return jsonify({"materials": materials.load()})


def _resolve_from_request(payload):
    material = materials.get(str(payload.get("material", "")))
    if not material:
        raise item.ItemError(messages.notice("unknown_material"))
    unit = payload.get("unit", "mm")
    width = item.parse_dimension(payload.get("width"), unit)
    height = item.parse_dimension(payload.get("height"), unit)
    return item.resolve(material, width, height, label=payload.get("label", ""),
                        scale=payload.get("scale") or None)


def _refusal(error):
    """An ItemError as JSON: the CODE for the caller to branch on, plus the shop's own wording."""
    overrides = materials.load_messages()
    code = error.notice["code"]
    return jsonify({"code": code,
                    "error": messages.render(code, error.notice.get("values"), overrides)}), 400


@app.route("/api/resolve", methods=["POST"])
def api_resolve():
    """What the boxes WILL be, so the page can show them before anything is downloaded."""
    try:
        resolved = _resolve_from_request(request.get_json(silent=True) or {})
    except item.ItemError as error:
        return _refusal(error)
    overrides = materials.load_messages()
    return jsonify({"item": resolved, "page_mm": item.page_size_mm(resolved),
                    "describe": item.describe(resolved),
                    # Rendered here rather than in the browser: the wording is the shop's, and the
                    # page should not need a copy of it.
                    "notices": messages.render_all(resolved["notices"], overrides)})


@app.route("/api/template", methods=["POST"])
def api_template():
    """The queue → one multipage PDF."""
    payload = request.get_json(silent=True) or {}
    queue = payload.get("items") or []
    if not queue:
        return jsonify({"error": "Queue is empty."}), 400
    if len(queue) > MAX_QUEUE_ITEMS:
        return jsonify({"error": f"At most {MAX_QUEUE_ITEMS} items per download."}), 400
    try:
        resolved = [_resolve_from_request(entry) for entry in queue]
        pdf_bytes = generate.build_pdf(resolved)
    except item.ItemError as error:
        return _refusal(error)
    name = "szablon.pdf" if len(resolved) == 1 else f"szablony-{len(resolved)}.pdf"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=name)


@app.route("/plik")
def check_page():
    """The customer check: drop a file, read the verdict, watch it on the flag.

    At /plik rather than /sprawdz because a shop publishing this mounts the whole app under a word
    of its own, and a mount called /sprawdz would then serve this page at /sprawdz/sprawdz, which
    is not an address to hand to a customer. The landing page is `/`, and its CTA points here."""
    return render_template("sprawdz.html")


@app.route("/model/<token>")
def model_page(token):
    """The 3D preview of one template. Public, like the sheet download beside it."""
    template = materials.get_template(token)
    if not template:
        return jsonify({"error": "Nie znamy takiego szablonu — odśwież stronę."}), 404
    return render_template("model3d.html", token=token,
                           name=template.get("name") or token,
                           # `bare` strips ALL chrome for the hero; `embed` keeps the legend
                           # and controls but drops the header — the check page's inspectable frame.
                           bare=request.args.get("bare") == "1",
                           embed=request.args.get("embed") == "1")


@app.route("/podglad-hero")
def hero_preview():
    """A LAYOUT preview of the two-layer hero, for settling how it should look before it is one.

    Scaffolding only: the animation on it is the 3D template page in an iframe — exactly the one a
    real hero would embed — so there is no second copy of the animation here to drift out of step
    with the original. What the page exists to answer is the question that cannot be settled in the
    abstract: how far the cut cloth may hang past the hero band and over the copy below it. Hence
    the sliders, and hence the fact that nothing here is wired into the landing page.
    """
    return render_template("hero_preview.html")


# A directory of finished 3D product models (.glb), for products whose shape no flat template can
# describe — a pneumatic tent is a sewn object, not an outline. Configured, never committed: the
# models are a shop's own renders, for the same reason its die library is data this code loads
# rather than content shipped with it.
MODELS_DIR_ENV = "PREPRESS_MODELS_DIR"
_MODEL_SUFFIX = ".glb"


def _models_dir():
    return (os.environ.get(MODELS_DIR_ENV) or "").strip()


def _model_file(name):
    """The .glb this name refers to, or None.

    Refuses anything that is not a plain filename, so a request cannot walk out of the configured
    directory and read the disk.
    """
    directory = _models_dir()
    if not directory or not name.endswith(_MODEL_SUFFIX):
        return None
    if os.path.basename(name) != name:
        return None
    path = os.path.join(directory, name)
    return path if os.path.isfile(path) else None


@app.route("/produkt3d")
def product_models_page():
    """Every configured product model as a list of links; empty when none are configured."""
    directory = _models_dir()
    try:
        names = sorted(entry for entry in os.listdir(directory)
                       if entry.endswith(_MODEL_SUFFIX)) if directory else []
    except OSError as error:                        # noqa: BLE001 — a bad path is not a 500
        logger.warning("models directory %r unreadable: %s", directory, error)
        names = []
    return render_template("product3d.html", models=names, model=None, bare=False)


@app.route("/produkt3d/<name>")
def product_model_page(name):
    if not _model_file(name):
        return jsonify({"error": "Nie znamy takiego modelu."}), 404
    return render_template("product3d.html", models=[], model=name,
                           bare=request.args.get("bare") == "1")


@app.route("/api/models/<name>")
def api_model_file(name):
    """The .glb itself. Cached for a day: a model runs to tens of megabytes, and a designer's new
    export arrives under a new filename rather than replacing one in place."""
    path = _model_file(name)
    if not path:
        return jsonify({"error": "Nie znamy takiego modelu."}), 404
    return send_file(path, mimetype="model/gltf-binary", max_age=86400)


@app.route("/api/templates/<token>/shape")
def api_template_shape(token):
    """The template's geometry as flat polylines, for the 3D scene.

    Disclosure note, because the customer list deliberately does NOT publish outlines: this is not
    a new leak. The PDF one click away literally DRAWS these same lines — brutto, netto and safe —
    so the shape endpoint hands out exactly what the sheet already does, in a form a mesh can eat.
    What stays unpublished is the list-wide dump: geometry is per-token, like the PDF.
    """
    template = materials.get_template(token)
    if not template:
        return jsonify({"error": "Nie znamy takiego szablonu — odśwież stronę."}), 404
    try:
        drawing, _notes = from_template.derive(template)
    except from_template.TemplateError as error:
        return jsonify({"error": str(error)}), 409

    def polylines(entries):
        return [[[round(x, 2), round(y, 2)] for x, y in offset.flatten(entry)]
                for entry in entries or []]

    # The mast, from the shop's 2026 price list (Flagi i akcesoria, pages 3-7) rather than eyeballed: the
    # Standard mast is the finished height + 400 mm for every Vento size (220->260, 270->310,
    # 370->410, 480->520, and the Drops 175->215 ... 430->470) and + 500 mm for the Regulars
    # (220->270, 280->330, 380->430). Finished = page - 200 mm on both axes, the same key the
    # sticker sheets already use.
    page_mm = template.get("page_mm") or [0, 0]
    finished_height_mm = max(float(page_mm[1]) - 200.0, 0.0)
    is_regular = "regular" in (template.get("name") or "").lower()
    mast_mm = finished_height_mm + (500.0 if is_regular else 400.0)

    # The ground base is a SQUARE, sized per flag size (the shop's price list, 2026-08-27): the number
    # on the sheet is the DIAGONAL, so the side is d/sqrt(2). Keyed on the size letter in the
    # template name; S is the safe floor when no letter is found.
    base_by_size = {"S": (415.0, 4), "M": (485.0, 6), "L": (515.0, 8), "XL": (670.0, 12)}
    size_letter = next((letter for letter in ("XL", "S", "M", "L")
                        if re.search(rf"\b{letter}\b", template.get("name") or "")), "S")
    base_diagonal_mm, base_weight_kg = base_by_size[size_letter]

    # HOW the flag is folded, sewn and hung — read from the template's own sewn sides, not
    # guessed from its name. A side sewn >= 60 mm is a TUNNEL (a 30 mm hem is finishing, not a
    # sleeve): top-only means a hung flag (mast + crossarm), a left tunnel means the Vento winder
    # family, and Regular stays keyed on its name because its hardware differs, not its sewing.
    sewn_sides = template.get("sewn_sides_mm") or {}
    tunnel_sides = {side for side, value in sewn_sides.items() if float(value or 0) >= 60.0}
    if is_regular:
        construction = "rigid"
    elif tunnel_sides == {"top"}:
        construction = "top"
    else:
        construction = "winder"

    return jsonify({
        "name": template.get("name") or token,
        "page_mm": template.get("page_mm"),
        "mast_mm": mast_mm,
        # 1 = one-sided with mirror show-through: the BACK renders the front mirrored (and dimmed).
        # 2 = a true double-sided flag whose back is its own graphic, never a mirror. The 3D scene
        # keys on THIS field — the name says the same thing, but names are for people.
        "sides": template.get("sides") or 1,
        "base": {"side_mm": round(base_diagonal_mm / 2 ** 0.5, 1),
                 "weight_kg": base_weight_kg},
        "construction": construction,
        "sewn_sides_mm": sewn_sides,
        # Regular is different HARDWARE, not just a different shape: rigid aluminium pole and a
        # rigid arm, tunnel sewn as a full sleeve (not folded in half). The 3D scene keys wind
        # behaviour on this — a Regular flag moves only near its free corner.
        "rigid": is_regular,
        "netto": polylines(drawing.get("netto")),
        # The VISIBLE fabric. The tunnel element between the hem cut and the body cut is folded in
        # half and sewn onto the corpus (shop rule, 2026-08-26) — it wraps the mast as the sleeve —
        # so a model that renders the hem ring as flat cloth draws a flap that does not exist.
        # The corpus is the outline flagged as the safe base; absent on single-cut templates,
        # where the one cut line IS the fabric.
        "corpus": polylines([shape.deserialise(o)
                             for o in (template.get("outlines") or [])
                             if o.get("safe_base")][:1]),
        "safe": polylines(drawing.get("safe")),
        "brutto": polylines(drawing.get("brutto")),
        # Optional, and deployment data rather than anything this repo ships: the shape to CUT
        # when this template cannot be cut from another one at its true size. A Drop is not a
        # trimmed feather flag — part of it falls outside — so the animation cuts a shape that
        # does fit and then transforms that into the real outline. Absent for every template that
        # nests as it is, which is nearly all of them.
        "transform": [[round(float(x), 2), round(float(y), 2)]
                      for x, y in (template.get("transform_ring_mm") or [])],
    })


# A demo artwork raster, for the 3D scene to wear on the marketing hero and for testing the
# texture path before an upload page exists. Configured, never hardcoded to one shop's disk: unset
# means the scene simply shows bare cloth.
DEMO_ARTWORK_ENV = "PREPRESS_DEMO_ARTWORK"
_demo_artwork_cache = {}


@app.route("/api/demo-artwork.jpg")
def api_demo_artwork():
    """One rasterised page of the configured demo artwork, as a texture.

    Cached in memory after the first render: it is the same bytes for every visitor, and a 3 m flag
    at texture resolution costs about a second of pypdfium2 that nobody should pay twice.
    """
    # Asked for a TEMPLATE's demo: drawn inside that template's own safe outline, so the hero never
    # wears detail in the keep-out ring of the page that offers to catch exactly that. Cached per
    # template and per store version; falls through to the bundled file when the template is gone.
    token = (request.args.get("token") or "").strip()
    template = materials.get_template(token) if token else None
    if template:
        stamp = f"{token}:{os.path.getmtime(materials.DEFAULT_STORE) if os.path.exists(materials.DEFAULT_STORE) else 0}"
        etag = f'"demo-{abs(hash(stamp))}"'
        if request.headers.get("If-None-Match") == etag:
            return Response(status=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        if _demo_artwork_cache.get("stamp") != stamp or _demo_artwork_cache.get("token") != token:
            try:
                png = shape.render_preview(demo_artwork.build_pdf(template), 0, max_pixels=2800)
            except (demo_artwork.DemoError, from_template.TemplateError) as error:
                logger.info("demo artwork for %s not drawable: %s", token, error)
                template = None                     # bare cloth beats a wrong picture
            except Exception as error:              # noqa: BLE001 — a demo is decoration
                logger.warning("demo artwork render failed for %s: %s", token, error)
                template = None
            else:
                _demo_artwork_cache.clear()
                _demo_artwork_cache.update({"stamp": stamp, "token": token, "png": png})
        if template:
            return Response(_demo_artwork_cache["png"], mimetype="image/png",
                            headers={"Cache-Control": "no-cache", "ETag": etag})
    bundled = os.path.join(app.static_folder, "demo-artwork.pdf")
    source = os.environ.get(DEMO_ARTWORK_ENV, "").strip() or bundled
    if not os.path.exists(source):
        return jsonify({"error": "Nie skonfigurowano przykładowej grafiki."}), 404
    try:
        stamp = os.path.getmtime(source)
    except OSError:
        stamp = 0
    # no-cache + ETag, not max-age: the browser re-asks on every load and gets a 304 while the
    # file is unchanged — a swapped demo shows up on a PLAIN reload instead of hiding behind an
    # hour of max-age (the owner had to hard-reload to see the new artwork).
    etag = f'"demo-{int(stamp)}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    if _demo_artwork_cache.get("stamp") != stamp or _demo_artwork_cache.get("token"):
        try:
            with open(source, "rb") as handle:
                png = shape.render_preview(handle.read(), 0, max_pixels=2800)
        except Exception as error:                  # noqa: BLE001 — a bad demo file is not a 500
            logger.warning("demo artwork render failed for %s: %s", source, error)
            return jsonify({"error": "Nie udało się wyrenderować przykładowej grafiki."}), 500
        _demo_artwork_cache.clear()
        _demo_artwork_cache.update({"stamp": stamp, "token": None, "png": png})
    return Response(_demo_artwork_cache["png"], mimetype="image/png",
                    headers={"Cache-Control": "no-cache", "ETag": etag})


@app.route("/api/templates/<token>/pdf")
def api_template_pdf(token):
    """The customer's sheet for one imported template.

    Public, like the generator: a template is the thing we WANT every customer to have, and the login
    guards the check, not the download (customers were bounced to /status for a sheet, 2026-09-02).
    """
    template = materials.get_template(token)
    if not template:
        return jsonify({"error": "Nie znamy takiego szablonu — odśwież stronę."}), 404
    try:
        pdf_bytes = from_template.build_pdf(template)
    except from_template.TemplateError as error:
        # A template the shop saved but that cannot be drawn is the SHOP's problem to fix, and the
        # customer should be told plainly rather than handed a broken sheet.
        return jsonify({"error": str(error)}), 409
    # "Play A.pdf" told a customer nothing (2026-09-02): the sheet is named in full, with the
    # finished size in centimetres, the unit flags are ordered in.
    name = (template.get("name") or token).replace("/", "-")
    trim = template.get("trim_mm") or []
    if len(trim) == 2:
        name += f" {round(float(trim[0]) / 10)}x{round(float(trim[1]) / 10)} cm"
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=f"{name}.pdf")


# ── Uploads held for a re-check ─────────────────────────────────────────────
# A customer who picks a different template or material should not send a 600 MB file again. The
# bytes are held in MEMORY for a while, keyed by a token the check response carries — memory, not
# disk, because the check page promises the file never touches the disk here, and the check already
# holds the whole file in memory anyway. One global cap keeps a busy hour from eating the machine;
# the oldest upload goes first.
UPLOAD_HOLD_SECONDS = 30 * 60
UPLOAD_HOLD_MAX_BYTES = 1536 * 1024 * 1024    # ponytail: one global cap; per-caller caps if abused
_held_uploads = {}                              # token -> {"data", "filename", "expires"}
_held_lock = threading.Lock()


def _caller():
    """Who is asking: the session subject, or the door they came through. A held upload may only be
    re-checked by the same caller — a leaked token must not hand one customer another's file."""
    return session_identity() or ("open-door" if _opened_by_proxy() else "anonymous")


def _remember_upload(data, filename):
    """Hold these bytes for a re-check; returns the token the page sends back."""
    now = time.time()
    owner = _caller()
    with _held_lock:
        for stale in [t for t, held in _held_uploads.items() if held["expires"] < now]:
            del _held_uploads[stale]
        while _held_uploads and (sum(len(h["data"]) for h in _held_uploads.values())
                                 + len(data) > UPLOAD_HOLD_MAX_BYTES):
            oldest = min(_held_uploads, key=lambda t: _held_uploads[t]["expires"])
            del _held_uploads[oldest]
        token = secrets.token_urlsafe(16)
        _held_uploads[token] = {"data": data, "filename": filename, "owner": owner,
                                "expires": now + UPLOAD_HOLD_SECONDS}
    return token


def _recall_upload(token):
    """The held upload for this token, its clock restarted — or None when it is gone."""
    now = time.time()
    with _held_lock:
        held = _held_uploads.get(token)
        if not held:
            return None
        if held["expires"] < now:
            del _held_uploads[token]
            return None
        if held["owner"] != _caller():
            return None                              # somebody else's upload: as if it never existed
        held["expires"] = now + UPLOAD_HOLD_SECONDS
        return held


@app.route("/api/check", methods=["POST"])
@require_session
def api_check():
    """Identify a returned file, measure it, and judge it against the material's rules.

    The stamp says which template it is, the render says what the ink does, and the objects say what
    the file declares about itself. Anything that genuinely cannot be measured comes back as `info`
    rather than a pass — saying "not measured" beats implying a file is fine.

    The severities and every sentence come from the shop's own settings, so two shops running this
    build can reach different verdicts on the same file, which is the point.
    """
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file uploaded."}), 400
    data = upload.read()
    filename = upload.filename or ""
    return _judge(data, filename, request.form, _remember_upload(data, filename))


@app.route("/api/check-path", methods=["POST"])
@require_session
def api_check_path():
    """Judge a file the customer already put on the shop's file server, by its path."""
    if not _path_roots():
        return jsonify({"error": "Sprawdzanie po ścieżce jest wyłączone."}), 404
    try:
        real = resolve_shared_path(request.form.get("path"))
    except PathRefused as refusal:
        return jsonify({"error": str(refusal)}), 404
    if os.path.splitext(real)[1].lower() not in PATH_CHECK_EXTENSIONS:
        return jsonify({"error": "Sprawdzamy pliki PDF, TIFF, JPG i PNG."}), 415
    if os.path.getsize(real) > app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({"error": "Ten plik jest za duży nawet dla sprawdzania po ścieżce."}), 413
    with open(real, "rb") as handle:
        data = handle.read()
    filename = os.path.basename(real)
    return _judge(data, filename, request.form, _remember_upload(data, filename))


@app.route("/api/recheck/<token>", methods=["POST"])
@require_session
def api_recheck(token):
    """The same judgement over a held upload, with a different template, material, side or page."""
    held = _recall_upload(token)
    if not held:
        return jsonify({"error": "Ten plik nie jest już w pamięci — wgraj go ponownie."}), 410
    return _judge(held["data"], held["filename"], request.form, token)


def _judge(data, filename, form, token=None):
    """The check itself, over bytes already in hand. `form` carries the customer's choices."""
    # Which page of the customer's file is being judged (0-based). The whole pipeline always took
    # a page_index; the endpoint simply never passed one, so a 4-page file was silently judged by
    # page 1 alone.
    try:
        page_index = max(int(form.get("page") or 0), 0)
    except ValueError:
        page_index = 0
    # A flat raster (TIFF, JPEG, PNG) or a PDF. A raster has no stamp and no page to match, so it
    # goes straight to the fourth rung: the customer names material and size, and the pixels are
    # judged against them (`raster.py`). Its "page" is what its DPI tag implies, when plausible.
    kind = "raster" if raster.is_raster(data) else "pdf"
    try:
        if kind == "raster":
            page_count = raster.page_count(data)
        else:
            import pikepdf as _pikepdf
            with _pikepdf.open(io.BytesIO(data)) as _pdf:
                page_count = len(_pdf.pages)
    except Exception:                               # noqa: BLE001 — an unreadable file is a verdict
        return jsonify({"recognised": False, "reason": "The file could not be read."}), 200
    if page_index >= page_count:
        return jsonify({"error": f"Ten plik ma {page_count} stron — strony "
                                 f"{page_index + 1} w nim nie ma."}), 400
    try:
        page_mm = (raster.page_mm(data, page_index) if kind == "raster"
                   else identify.page_size_mm(data, page_index))
    except Exception:                               # noqa: BLE001 — an unreadable page is a verdict
        return jsonify({"recognised": False, "reason": "The page size could not be read."}), 200

    # The identification ladder, in the ROADMAP's order: the stamp, then — for the years of files
    # made on the OLD Illustrator templates — geometry as a HINT the human confirms. `template` is
    # that confirmation coming back: the customer picked one of the candidates we offered, so the
    # stored template supplies the same geometry its stamp would have carried. Never resolved
    # silently: this morning's real files match THREE templates by page size alone, and the wrong
    # pick is the 12 mm-off cut this project's history already paid for once.
    confirmed = (form.get("template") or "").strip()
    material_token = (form.get("material") or "").strip()
    free_size = False
    if confirmed:
        template = materials.get_template(confirmed)
        if not template:
            return jsonify({"error": "Nie znamy takiego szablonu — odśwież stronę."}), 404
        stamp = from_template.stamp_payload(template)
        assumed = True
    elif material_token:
        # The fourth rung: no template at all — a banner, a board, a sticker. The customer names the
        # material and the finished size, and the file is judged against the SAME geometry the
        # generator would have stamped into a sheet for that pair (item.resolve → generate.stamp_payload),
        # so the two roads cannot drift. At 1:1 and without the spec strip: what is being judged is
        # the artwork itself, not one of our pages. Rectangles only — a size alone has no outline.
        try:
            resolved = _resolve_from_request({"material": material_token, "scale": 1,
                                              "width": form.get("width"),
                                              "height": form.get("height")})
        except item.ItemError as error:
            return _refusal(error)
        resolved["spec_strip_mm"] = 0.0
        stamp = generate.stamp_payload(resolved)
        assumed = True
        free_size = True
    elif kind == "raster":
        named, stated = named_size.parse_mm(filename)
        return jsonify({"recognised": False, "reason": "raster", "kind": kind,
                        "candidates": [], "page_mm": page_mm,
                        "named_size_mm": list(named_size.reconcile(named, stated, page_mm) or [])
                        or None, "token": token}), 200
    else:
        found = identify.read_stamp(data, page_index)
        if not found:
            # Second rung before the candidates: the PRINTED identity in the bleed corner. A
            # design app's export rebuilds the PDF and kills the stamp, but keeps drawn content —
            # so a kept template layer identifies the file exactly, no human pick needed.
            printed = materials.get_template(identify.printed_token(data, page_index) or "")
            if printed:
                stamp = from_template.stamp_payload(printed)
                assumed = False
            else:
                tolerance = 3.0
                # The NAME is the second witness: a file saved off our template at a drifted page
                # size still says `80x240` in its name, and that names the template family.
                named, stated = named_size.parse_mm(filename)
                named = named_size.reconcile(named, stated, page_mm)
                candidates, seen = [], set()
                for t in materials.load_templates():
                    by_page = (abs(float(t["page_mm"][0]) - page_mm[0]) <= tolerance
                               and abs(float(t["page_mm"][1]) - page_mm[1]) <= tolerance)
                    trim = t.get("trim_mm") or []
                    by_name = bool(named and len(trim) == 2
                                   and named_size.same_size(named, (float(trim[0]), float(trim[1]))))
                    if (by_page or by_name) and t.get("token") not in seen:
                        seen.add(t.get("token"))
                        candidates.append({"token": t.get("token"), "name": t.get("name")})
                # page_mm and the name's size ride along so the page can pre-fill the picker and
                # offer a choice when the two disagree.
                return jsonify({"recognised": False, "reason": found["reason"],
                                "candidates": candidates, "page_mm": page_mm,
                                "named_size_mm": list(named) if named else None,
                                "token": token}), 200
        else:
            stamp = found["stamp"]
            assumed = False
    expected = identify.stamped_geometry(stamp)
    # Which separation is the knife, when the customer said (the page offers the file's own list).
    cut_spot = (form.get("cut") or "").strip() or None
    facts = dict(measure.measure(data, expected, page_index, cut_spot=cut_spot))
    facts["cut_spot"] = cut_spot
    # A raster without a plausible DPI tag has no size of its own: the declared size IS its page.
    if page_mm is None:
        page_mm = [expected["brutto_mm"][0] / expected["scale"],
                   expected["brutto_mm"][1] / expected["scale"]]
    facts["page_mm"] = page_mm
    # Guides, detected by VECTORS when the template is in hand. The raster detector only ever saw
    # straight guides — a winder's curved cut line never fills a row, so a file consisting of
    # nothing but guides measured as guide-free. The template knows its own outlines; an uploaded
    # outline within a millimetre of one of them IS that guide, whatever its shape.
    template_record = materials.get_template((stamp or {}).get("template") or "")
    if template_record:
        own = [(float(o.get("width_mm") or 0), float(o.get("height_mm") or 0))
               for o in (template_record.get("outlines") or [])]
        try:
            uploaded = outline.candidates(data)
        except Exception:                           # noqa: BLE001 — unreadable vectors = no guides
            uploaded = []
        facts["guides_present"] = any(
            abs(candidate["width_mm"] - w) <= 1.0 and abs(candidate["height_mm"] - h) <= 1.0
            for candidate in uploaded for (w, h) in own)

    # The bare template, said in its own words: our guides still drawn AND no artwork anywhere
    # near the edges (the blank scan found nothing but page furniture). Judging it like a design
    # meant scolding our own spec panel for sitting in the safe area.
    blank = facts.get("blank_edges_mm") or ()
    bare_template = bool(
        facts.get("guides_present")
        and blank
        and max(blank[0] / max(expected["brutto_mm"][0], 1),
                blank[1] / max(expected["brutto_mm"][1], 1)) > 0.3)
    facts["declared_boxes_mm"] = {} if kind == "raster" else identify.declared_boxes_mm(data)
    # What the objects declare: fonts, colour spaces, colorants, overprint, page count, producer.
    # `readable` and `reason` are dropped rather than merged — the render already reported why it
    # could not measure, and two facts under one name is how a verdict quietly loses its reason.
    declared = raster.facts(data, filename) if kind == "raster" else structure.facts(data, filename)
    facts.update({key: value for key, value in declared.items()
                  if key not in ("readable", "reason")})
    material = materials.get(expected["material_id"] or "")
    wording = materials.load_messages()
    findings = rules.run(facts, expected, material,
                         levels=materials.load_rule_levels(), wording=wording)
    if bare_template:
        findings = []
    # The die line travels with the FRONT file of a two-file pair: the tył of a dwustronna flag
    # never carries its own wykrojnik, so asking it for one repeats a warning the przód already
    # made (shop rule, 2026-08-27). The client says which side this file is; sides only exist for
    # pairs, so a single-file check never sends the field.
    if form.get("side") == "back":
        findings = [finding for finding in findings if finding["id"] != "cut_path"]
    # The uploaded page as a texture, straight back in the response. Base64 on purpose: the
    # customer's bytes still never touch the disk, so there is nothing to retain or leak — the
    # raster lives exactly as long as the response does.
    try:
        # 2800 px on the longest side: a 3 m flag keeps ~750 px across its width, enough for the
        # small print to survive an oblique camera. The admin preview stays at its own size — this
        # raster is a TEXTURE, that one is a picture to recognise a shape in.
        preview_png = base64.b64encode(
            raster.preview_png(data, page_index, max_pixels=2800) if kind == "raster"
            else shape.render_preview(data, page_index, max_pixels=2800)).decode()
    except Exception:                               # noqa: BLE001 — the verdict outranks the picture
        preview_png = None
    return jsonify({"recognised": True, "expected": expected, "page_mm": page_mm,
                    "kind": kind,
                    # Send this back to /api/recheck/<token> instead of the file again.
                    "token": token,
                    # The clean template itself — nothing to judge, and the page says so instead
                    # of scolding our own spec panel.
                    "bare_template": bare_template,
                    "page": page_index,
                    "page_count": page_count,
                    # Said out loud when the template was assumed on the customer's word rather
                    # than read from a stamp — the report is only as right as their pick.
                    "assumed_template": assumed,
                    "free_size": free_size,
                    "template_token": (stamp or {}).get("template"),
                    # Every named separation the file draws, and which one was read as the knife,
                    # so the page can offer the pick. Sent whatever the material is.
                    "separations": facts.get("spot_names") or [],
                    "cut_spot": cut_spot or ((facts.get("die") or {}).get("colorant") or None),
                    # The knife as drawn (outline_mm, page mm, y down) for the overlay.
                    "die": facts.get("die"),
                    "preview_png": preview_png,
                    "measured": {k: facts.get(k) for k in
                                 ("blank_edges_mm", "safe_intrusion_mm", "min_dpi",
                                  "guides_present", "reason")},
                    "declared": declared,
                    "declared_boxes_mm": facts["declared_boxes_mm"],
                    "material": material, "checks": findings,
                    "summary": (messages.render("summary.bare_template", {}, wording)
                                if bare_template
                                else rules.summarise(findings, wording))})


# The report is built from what the page already holds, so a huge body is abuse, not a big job.
MAX_REPORT_BODY_BYTES = 12 * 1024 * 1024
MAX_REPORT_ROWS = 200


@app.route("/api/report", methods=["POST"])
@require_session
def api_report():
    """The verdict the customer is reading, as a PDF to attach to their order.

    Built from the check the page already received — the file itself is not sent again, and the
    picture is the overlay the page drew (so the report shows exactly what the customer saw).
    """
    if (request.content_length or 0) > MAX_REPORT_BODY_BYTES:
        return jsonify({"error": "Raport jest za duży."}), 413
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("checks"), list):
        return jsonify({"error": "Brak wyniku kontroli do zapisania."}), 400
    # The report is laid out from whatever the client sent: only strings of a sane length, only
    # dict rows, a bounded number of them — a payload is not a document.
    text = lambda value, cap: str(value if isinstance(value, (str, int, float)) else "")[:cap]  # noqa: E731
    payload = {
        "filename": text(payload.get("filename"), 200),
        "subject": text(payload.get("subject"), 300),
        "checked_at": text(payload.get("checked_at"), 60),
        "summary": text(payload.get("summary"), 2000),
        "legend": text(payload.get("legend"), 600),
        "overlay_png": payload.get("overlay_png") if isinstance(payload.get("overlay_png"), str) else None,
        "checks": [{"level": text(row.get("level"), 8), "title": text(row.get("title"), 500),
                    "detail": text(row.get("detail"), 2000)}
                   for row in payload["checks"][:MAX_REPORT_ROWS] if isinstance(row, dict)],
    }
    from . import report
    pdf_bytes = report.build_pdf(payload, brand=os.environ.get(BRAND_NAME_ENV) or "prepress-open")
    stem = re.sub(r"\.[^.]+$", "",
                  secure_filename(report.fold_ascii(payload.get("filename") or "plik")))
    return send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
                     as_attachment=True, download_name=f"raport-{stem or 'plik'}.pdf")


# ── Admin surface ───────────────────────────────────────────────────────────

@app.route("/admin")
def admin_page():
    """The page loads without a token; every ACTION on it needs one.

    `proxied`: a proxy in front already holds the token and stamps it on every request (the ERP
    dev console mounts this panel under its own login), so the page hides its token field and
    sends a placeholder the proxy overwrites.
    """
    return render_template("admin.html", configured=bool(admin_token()),
                           token_env=ADMIN_TOKEN_ENV,
                           proxied=bool(request.headers.get("X-Admin-Token")))


@app.route("/api/admin/materials", methods=["POST"])
@require_admin
def api_admin_upsert():
    payload = request.get_json(silent=True) or {}
    try:
        stored = materials.upsert(payload.get("material") or {})
    except materials.MaterialError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"material": stored, "materials": materials.load()})


@app.route("/api/admin/materials/<material_id>", methods=["DELETE"])
@require_admin
def api_admin_remove(material_id):
    if not materials.remove(material_id):
        return jsonify({"error": f"No material with id {material_id!r}."}), 404
    return jsonify({"materials": materials.load()})


@app.route("/api/admin/messages", methods=["GET"])
@require_admin
def api_admin_messages():
    """Defaults and overrides together, so the editor can show what it is replacing."""
    return jsonify({"defaults": messages.DEFAULT_MESSAGES,
                    "overrides": materials.load_messages(),
                    "info_codes": sorted(messages.INFO_CODES)})


@app.route("/api/admin/messages", methods=["POST"])
@require_admin
def api_admin_save_messages():
    payload = request.get_json(silent=True) or {}
    saved = materials.save_messages(payload.get("messages") or {})
    return jsonify({"overrides": saved})


@app.route("/api/admin/inspect", methods=["POST"])
@require_admin
def api_admin_inspect():
    """A production template goes in; every outline it contains comes back, with a picture.

    Nothing is decided here and nothing is stored. The panel shows the shape and the sizes, and a
    human says which outline is the cut — because measured on this shop's own templates there is no
    signal to decide it from: one unnamed layer, one stroke colour, no separations, and a page frame
    that reads as the outermost outline. Detail in `prepress/outline.py`.
    """
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Nie wybrano pliku."}), 400
    data = upload.read()
    page_index = _page_index()
    try:
        page_mm = outline.page_size_mm(data, page_index)
    except Exception:                               # noqa: BLE001 — a bad upload is an answer
        return jsonify({"error": "Nie udało się otworzyć tego pliku jako PDF."}), 400
    found = outline.mark_page_sized(outline.candidates(data, page_index), page_mm)
    if not found:
        return jsonify({"error": "Nie znaleźliśmy w tym pliku żadnego obrysu tej wielkości."}), 400
    try:
        preview = base64.b64encode(shape.render_preview(data, page_index)).decode("ascii")
    except Exception:                               # noqa: BLE001 — the numbers matter, the picture helps
        preview = ""
    return jsonify({
        "line_types": lines.for_browser(),
        "page_mm": page_mm,
        "pages": _page_count(data),
        "page_index": page_index,
        "preview_png": preview,
        "source_name": upload.filename or "",
        "candidates": [{
            "index": order,
            "width_mm": entry["width_mm"],
            "height_mm": entry["height_mm"],
            "area_mm2": entry["area_mm2"],
            "painted": entry["painted"],
            "page_sized": entry["page_sized"],
            "group": entry["group"],
            "svg_path": shape.to_svg_path(entry, page_mm[1]),
            "outline": shape.serialise(entry),
        } for order, entry in enumerate(found)],
    })


def _page_index():
    try:
        return max(0, int(request.form.get("page", request.args.get("page", 0))))
    except (TypeError, ValueError):
        return 0


def _page_count(data):
    import pikepdf

    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception:                               # noqa: BLE001
        return 1


@app.route("/api/admin/templates", methods=["POST"])
@require_admin
def api_admin_save_template():
    """Store the outline the admin pointed at, with the numbers they typed, under a fresh token."""
    payload = request.get_json(silent=True) or {}
    chosen, problem = _typed_outlines(payload.get("outlines"))
    if problem:
        return jsonify({"error": problem}), 400
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Podaj nazwę szablonu — klient wybiera po niej."}), 400
    try:
        bleed_mm = float(payload.get("bleed_mm"))
        safe_mm = float(payload.get("safe_mm"))
    except (TypeError, ValueError):
        return jsonify({"error": "Spad i obszar bezpieczny muszą być liczbami."}), 400
    if bleed_mm < 0 or safe_mm < 0:
        return jsonify({"error": "Spad i obszar bezpieczny nie mogą być ujemne."}), 400
    page = payload.get("page_mm")
    if not (isinstance(page, list) and len(page) == 2):
        return jsonify({"error": "Brak rozmiaru strony szablonu."}), 400

    trim = lines.trim_box_mm(chosen)
    try:
        sides = 2 if int(payload.get("sides") or 1) == 2 else 1
    except (TypeError, ValueError):
        sides = 1
    sewn_mm, problem = _sewn_sides(payload.get("sewn_sides_mm"))
    if problem:
        return jsonify({"error": problem}), 400
    template = {
        # Short and URL-safe: the customer picks a template by it, and it goes into the stamp. It is
        # an identifier, not a secret — the customer surface is open by design.
        "token": payload.get("token") or secrets.token_urlsafe(6),
        "name": name,
        "page_mm": [float(page[0]), float(page[1])],
        "outlines": chosen,
        # The finished size, as the UNION of everything marked as a cut — on a flag the hem reaches
        # further than the body, and the flag that leaves the shop is as wide as the hem.
        "trim_mm": [round(trim[2] - trim[0], 2), round(trim[3] - trim[1], 2)] if trim else None,
        "bleed_mm": bleed_mm,
        "safe_mm": safe_mm,
        "sewn_sides_mm": sewn_mm,
        "sides": sides,
        "material": str(payload.get("material") or "").strip() or None,
        "source_name": str(payload.get("source_name") or "").strip(),
        "note": str(payload.get("note") or "").strip(),
    }
    try:
        stored = materials.upsert_template(template)
    except materials.MaterialError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"template": stored, "templates": materials.load_templates()})


def _sewn_sides(raw):
    """How much material finishing eats on each side, or None when it eats none.

    Returns (value-or-None, problem-or-None). This is NOT the safe margin — it is the sleeve, hem or
    weld that gets folded and sewn, after which that material is gone. The margin is added on top of
    it, so all zeroes mean "nothing is sewn" and are stored as absent rather than as four noisy
    zeroes every later read has to interpret.
    """
    if raw in (None, "", {}):
        return None, None
    if not isinstance(raw, dict):
        return None, "Zakładki muszą być podane dla czterech boków."
    sides = {}
    for side in from_template.SAFE_SIDES:
        try:
            value = float(raw.get(side) or 0.0)
        except (TypeError, ValueError):
            return None, f"Zakładka „{side}” musi być liczbą."
        if value < 0:
            return None, "Zakładki nie mogą być ujemne."
        sides[side] = value
    if not any(sides.values()):
        return None, None
    return sides, None


def _typed_outlines(raw):
    """Validate the outlines the admin marked. Returns (outlines, problem-or-None).

    Refusals are specific on purpose: "nothing selected" and "selected but nothing is a cut" are
    different mistakes, and an admin who marked three folds and no cut needs to be told which.
    """
    if not isinstance(raw, list) or not raw:
        return None, "Nie wskazano żadnej linii — kliknij obrysy i nadaj im typ."
    typed = []
    for entry in raw:
        if not isinstance(entry, dict) or not (entry.get("segments") or {}):
            return None, "Jedna z wskazanych linii nie ma geometrii."
        kind = str(entry.get("type") or "").strip().lower()
        if not lines.is_known(kind):
            return None, f"Nieznany typ linii: {kind or '(brak)'}."
        # `safe_base` is a FLAG, not a type: a 633 mm outline is a cut line that ALSO happens to be
        # the edge the margin is measured from, and an earlier model made the operator choose between the two.
        typed.append({**entry, "type": kind, "safe_base": bool(entry.get("safe_base"))})
    if not any(lines.defines_trim(entry["type"]) for entry in typed):
        names = ", ".join(f"„{t['label']}”" for t in lines.LINE_TYPES if t["defines_trim"])
        return None, f"Żadna linia nie wyznacza rozmiaru gotowego — oznacz co najmniej jedną jako {names}."
    outside = _safe_outside_trim(typed)
    if outside:
        return None, (f"Linia oznaczona jako obszar bezpieczny ({outside['width_mm']:.1f} × "
                      f"{outside['height_mm']:.1f} mm) leży POZA linią cięcia, a obszar bezpieczny "
                      "z definicji jest w środku. To prawie na pewno linia spadu.")
    return typed, None


# Half a millimetre: template outlines are drawn to a tenth, so anything bigger is a real mistake
# rather than rounding.
SAFE_CONTAINMENT_TOLERANCE_MM = 0.5


def _safe_outside_trim(typed):
    """A drawn safe area that is bigger than the cut, which is impossible and used to be accepted.

    It happened on a real import: the bleed line was marked „obszar bezpieczny", so the generator
    took a 732 mm box as the safe area of a 720 mm flag, then had to INVENT a bleed because the real
    one was now spoken for. Nothing complained. The shapes say this cannot be true, so they say so.
    """
    trim = lines.trim_box_mm(typed)
    if not trim:
        return None
    slack = SAFE_CONTAINMENT_TOLERANCE_MM
    for entry in typed:
        if entry["type"] != "safe" or not entry.get("origin_mm"):
            continue
        x0, y0 = entry["origin_mm"]
        if (x0 < trim[0] - slack or y0 < trim[1] - slack
                or x0 + entry["width_mm"] > trim[2] + slack
                or y0 + entry["height_mm"] > trim[3] + slack):
            return entry
    return None


@app.route("/api/admin/line-types")
@require_admin
def api_admin_line_types():
    """The line-type table on its own, for a panel that has not inspected anything yet."""
    return jsonify({"line_types": lines.for_browser()})


@app.route("/api/admin/templates/order", methods=["PUT"])
@require_admin
def api_admin_reorder_templates():
    """Put the saved templates in the order the panel dragged them into.

    The order is not decoration: this list IS the customer's picker, so a shop that sells mostly
    Vento M should be able to put Vento M first. Registered before the `<token>` rule reads oddly but
    matters not at all — Werkzeug prefers a static segment over a converter — and no token can
    collide with it anyway, since they are eight random URL-safe characters.
    """
    payload = request.get_json(silent=True) or {}
    tokens = payload.get("order")
    if not isinstance(tokens, list):
        return jsonify({"error": "Brak kolejności do zapisania."}), 400
    return jsonify({"templates": materials.reorder_templates([str(t) for t in tokens])})


@app.route("/api/admin/materials/order", methods=["PUT"])
@require_admin
def api_admin_reorder_materials():
    """Same for the material list, which drives the picker on the customer's own page."""
    payload = request.get_json(silent=True) or {}
    ids = payload.get("order")
    if not isinstance(ids, list):
        return jsonify({"error": "Brak kolejności do zapisania."}), 400
    return jsonify({"materials": materials.reorder_materials([str(i) for i in ids])})


@app.route("/api/admin/templates/<token>", methods=["PATCH"])
@require_admin
def api_admin_edit_template(token):
    """Rename a template, or correct its numbers, without re-importing the file.

    The outlines are deliberately out of reach here. Their roles were decided against a rendered
    page, and this request has no page — so a form that let them be changed blind would be exactly
    the guessing the import flow exists to remove. The TOKEN never changes either: it is already in
    every template PDF handed out, and re-minting it would orphan them.
    """
    payload = request.get_json(silent=True) or {}
    changes = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Podaj nazwę szablonu — klient wybiera po niej."}), 400
        changes["name"] = name
    for key in ("bleed_mm", "safe_mm"):
        if key in payload:
            try:
                value = float(payload[key])
            except (TypeError, ValueError):
                return jsonify({"error": "Spad i obszar bezpieczny muszą być liczbami."}), 400
            if value < 0:
                return jsonify({"error": "Spad i obszar bezpieczny nie mogą być ujemne."}), 400
            changes[key] = value
    if "sewn_sides_mm" in payload:
        sewn_mm, problem = _sewn_sides(payload.get("sewn_sides_mm"))
        if problem:
            return jsonify({"error": problem}), 400
        changes["sewn_sides_mm"] = sewn_mm
    if "sides" in payload:
        changes["sides"] = 2 if str(payload.get("sides")) == "2" else 1
    if "material" in payload:
        changes["material"] = str(payload.get("material") or "").strip() or None
    if "note" in payload:
        changes["note"] = str(payload.get("note") or "").strip()
    if not changes:
        return jsonify({"error": "Nie ma czego zmienić."}), 400
    try:
        updated = materials.update_template(token, changes)
    except materials.MaterialError as error:
        return jsonify({"error": str(error)}), 400
    if not updated:
        return jsonify({"error": f"Nie ma szablonu o tokenie {token!r}."}), 404
    return jsonify({"template": updated, "templates": materials.load_templates()})


@app.route("/api/admin/templates/<token>", methods=["DELETE"])
@require_admin
def api_admin_remove_template(token):
    if not materials.remove_template(token):
        return jsonify({"error": f"Nie ma szablonu o tokenie {token!r}."}), 404
    return jsonify({"templates": materials.load_templates()})


@app.route("/api/templates")
def api_templates():
    """What a customer may see: the name, the finished size and the token to order against.

    The outline itself is not published — a customer picks a template, they do not need its geometry,
    and the shape of somebody's die is not a thing to hand out for free.
    """
    return jsonify({"templates": [{
        "token": t.get("token"),
        "name": t.get("name"),
        "page_mm": t.get("page_mm"),
        "trim_mm": t.get("trim_mm"),
        "sides": t.get("sides") or 1,
        # How many lines of each kind, so a customer sees "this one creases" without getting the
        # geometry of somebody's die.
        "lines": sorted({o.get("type") for o in t.get("outlines") or []}),
        "bleed_mm": t.get("bleed_mm"),
        "safe_mm": t.get("safe_mm"),
        # The safe area's own size — the advertising surface a customer actually gets.
        "safe_size_mm": _safe_size_mm(t),
        "sewn_sides_mm": t.get("sewn_sides_mm"),
        "material": t.get("material"),
        "note": t.get("note"),
    } for t in materials.load_templates()]})


# Deriving a template's safe outline costs about a second, and the landing page asks for the whole
# list twice — uncached that was 26 s of spinner (Shyeline, 2026-09-03). Keyed on the store's
# modification time, so an admin's edit invalidates every entry at once.
_safe_size_cache = {"stamp": None, "sizes": {}}


def _safe_size_mm(template):
    """(w, h) of the safe outline's bounding box, or None when the template cannot be drawn."""
    try:
        stamp = os.path.getmtime(materials.DEFAULT_STORE)
    except OSError:
        stamp = None
    if _safe_size_cache["stamp"] != stamp:
        _safe_size_cache.update(stamp=stamp, sizes={})
    token = template.get("token")
    if token in _safe_size_cache["sizes"]:
        return _safe_size_cache["sizes"][token]
    size = None
    try:
        drawing, _notes = from_template.derive(template)
        points = [p for entry in drawing.get("safe") or [] for p in offset.flatten(entry)]
        if len(points) >= 3:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            size = [round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1)]
    except Exception:                                # noqa: BLE001 — a list entry, not a verdict
        size = None
    _safe_size_cache["sizes"][token] = size
    return size


@app.route("/api/admin/rules", methods=["GET"])
@require_admin
def api_admin_rules():
    """Every rule a shop can re-grade, with what it is currently set to.

    The rule IDS are the contract, not the function names: a shop's saved severities are keyed on
    them, and they are also the prefix of the message codes that word each rule.
    """
    return jsonify({"rule_ids": list(rules.RULE_IDS),
                    "levels": list(materials.RULE_LEVELS),
                    "labels": {rule_id: _rule_label(rule_id) for rule_id in rules.RULE_IDS},
                    "overrides": materials.load_rule_levels()})


def _rule_label(rule_id):
    """What this rule SAYS when it finds something, as the label for its severity control.

    Reusing the rule's own wording beats maintaining a second list of human names: an admin deciding
    how serious a rule is wants to read the sentence a customer would get, and a shop that reworded
    that sentence sees its own words here.
    """
    return messages.render_label(rules.RULE_LABELS[rule_id], materials.load_messages())


@app.route("/api/admin/rules", methods=["POST"])
@require_admin
def api_admin_save_rules():
    payload = request.get_json(silent=True) or {}
    saved = materials.save_rule_levels(payload.get("rules") or {})
    return jsonify({"overrides": saved})


@app.route("/health")
def health():
    return jsonify({"ok": True, "materials": len(materials.load()),
                    "admin_configured": bool(admin_token())})


if __name__ == "__main__":       # pragma: no cover — development convenience only
    # threaded=True: Werkzeug serves single-threaded by default, so one slow request (the
    # demo artwork lives on a network share) queued EVERY navigation behind it — the shop
    # measured 30-60 s page changes.
    app.run(host="127.0.0.1", port=5099, debug=False, threaded=True)
