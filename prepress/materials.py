"""Materials — the rules, as data.

A material carries everything the shop decides about a substrate: how much bleed it needs, how far
from the trim its safe area sits, what resolution is acceptable, and how wide the roll is. Templates
and checks then read those numbers instead of hardcoding them, which is the whole reason an admin can
change a rule without a release.

The store is a single JSON file, re-read whenever it changes on disk, so the admin panel writes it and
every other process picks it up without a restart. No database until there is a reason for one.
"""
import json
import os
import re
import threading

DEFAULT_STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "materials.json")

# A material's own id is used in URLs, filenames and the template stamp, so it is a slug rather than
# free text — otherwise a rename breaks every template already issued under the old name.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")

# Every field an admin can set, with the type it must be and whether it may be omitted.
NUMERIC_FIELDS = ("bleed_mm", "safe_mm", "min_dpi", "max_width_mm",
                  "panel_max_long_mm", "panel_max_short_mm")
# Fields a material may leave unset. bleed and safe are the only ones a shop MUST decide.
OPTIONAL_NUMERIC_FIELDS = ("max_width_mm", "min_dpi", "panel_max_long_mm", "panel_max_short_mm")
COLOUR_MODES = ("cmyk", "any")
# Where the human-readable spec block sits on a template page.
#   panel   a centred info panel INSIDE the artwork: scale, graphic area, safe area, each in its own
#           cell, in a pale tint. Modelled on a display manufacturer's own trade template, which prints the
#           REAL dimensions even when the page is scaled — the default, and the most visible.
#   below   a strip UNDER the artwork, added to the page. For materials whose artwork must stay
#           completely clear. Works at any bleed, including zero.
#   margin  a bordered box in the bleed margin with rotated text, measured off a real flag
#           production file. Needs real bleed; falls back to `below` without it.
#   none    boxes and the invisible stamp only.
SPEC_POSITIONS = ("panel", "below", "margin", "none")

# Flags an admin sets per material, stored as real booleans so a rule can just read them.
#   cut_path  this material is CUT work: the die line must be present as a `Cut` separation, and a
#             second page carrying it is correct rather than a mistake.
BOOLEAN_FIELDS = ("cut_path",)

# What an admin may do to a rule's severity. "off" silences it; the others replace the level the rule
# chose for itself. A rule absent from the map keeps its own judgement.
RULE_LEVELS = ("off", "info", "amber", "red")

_cache = {"mtime": None, "path": None, "materials": [], "messages": {}, "rules": {},
          "templates": []}
_lock = threading.Lock()


class MaterialError(ValueError):
    """A material definition an admin should be told about, in words they can act on."""


def _validate(raw, existing_ids=()):
    """Return a clean material dict, or raise MaterialError with a message fit for a form."""
    if not isinstance(raw, dict):
        raise MaterialError("A material must be an object.")

    material_id = str(raw.get("id", "")).strip().lower()
    if not ID_PATTERN.match(material_id):
        raise MaterialError("Id must be 3–50 characters, lower-case letters, digits and hyphens, "
                            "starting and ending with a letter or digit (e.g. banner-frontlit-510).")
    if material_id in existing_ids:
        raise MaterialError(f"A material with id {material_id!r} already exists.")

    name = str(raw.get("name", "")).strip()
    if not name:
        raise MaterialError("Name cannot be empty — it is what the customer picks from.")

    clean = {"id": material_id, "name": name}
    for field in NUMERIC_FIELDS:
        value = raw.get(field)
        if value in (None, ""):
            # max_width_mm is genuinely optional: sheet materials have no roll width, and a missing
            # min_dpi means "do not judge resolution for this material".
            if field in OPTIONAL_NUMERIC_FIELDS:
                clean[field] = None
                continue
            raise MaterialError(f"{field} is required.")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise MaterialError(f"{field} must be a number, got {value!r}.")
        if number < 0:
            raise MaterialError(f"{field} cannot be negative.")
        if field in ("bleed_mm", "safe_mm") and number > 500:
            raise MaterialError(f"{field} of {number:g} mm looks like a typo — the limit is 500 mm.")
        clean[field] = number

    colour = str(raw.get("colour", "any")).strip().lower()
    if colour not in COLOUR_MODES:
        raise MaterialError(f"colour must be one of {', '.join(COLOUR_MODES)}.")
    clean["colour"] = colour

    spec = str(raw.get("spec_position", "panel")).strip().lower() or "panel"
    if spec not in SPEC_POSITIONS:
        raise MaterialError(f"spec_position must be one of {', '.join(SPEC_POSITIONS)}.")
    clean["spec_position"] = spec
    for field in BOOLEAN_FIELDS:
        clean[field] = _as_boolean(raw.get(field))
    clean["notes"] = str(raw.get("notes", "")).strip()
    return clean


def _as_boolean(value):
    """A flag as it arrives from a form: a real bool, or the strings a select posts.

    "0" and "false" are spelled out because they are truthy STRINGS in Python, and a material
    silently marked as cut work would demand a die line nobody ordered.
    """
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in ("", "0", "false", "no", "nie")


def load(path=None, force=False):
    """Every material, re-read when the file changed. Never raises for a missing store — a fresh
    install has no materials yet, and the admin panel is how you get the first one."""
    path = path or DEFAULT_STORE
    with _lock:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            _cache.update(mtime=None, path=path, materials=[], messages={}, rules={},
                          templates=[])
            return []
        if not force and _cache["path"] == path and _cache["mtime"] == mtime:
            return list(_cache["materials"])
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        materials = []
        for entry in document.get("materials", []):
            materials.append(_validate(entry, existing_ids=[m["id"] for m in materials]))
        _cache.update(mtime=mtime, path=path, materials=materials,
                      messages=_validate_messages(document.get("messages")),
                      rules=_validate_rule_levels(document.get("rules")),
                      templates=_validate_templates(document.get("templates")))
        return list(materials)


def _validate_messages(raw):
    """The shop's message overrides: a flat {code: text} map of strings, and nothing else.

    Unknown codes are KEPT rather than rejected — a shop upgrading to a build with new codes should
    not lose text it wrote, and a code this build does not know simply never renders.
    """
    if not isinstance(raw, dict):
        return {}
    return {str(code): str(text) for code, text in raw.items()
            if isinstance(text, str) and text.strip()}


def _validate_rule_levels(raw):
    """The shop's severity choices: a flat {rule_id: level} map, unknown levels dropped.

    Unknown rule IDS are kept, for the same reason unknown message codes are: a shop that silences a
    rule, then runs a build without it, should not lose the setting.
    """
    if not isinstance(raw, dict):
        return {}
    return {str(rule): level.strip().lower() for rule, level in raw.items()
            if isinstance(level, str) and level.strip().lower() in RULE_LEVELS}


def _validate_templates(raw):
    """Production templates the shop imported, each already reduced to one chosen outline.

    Kept forgiving on purpose: a template is DATA an admin produced by pointing at a shape, and the
    only fields this build refuses to store are the ones without which it could not redraw the
    outline at all. Anything else it does not recognise is passed through, so a store written by a
    later build is not silently stripped by an earlier one.
    """
    if not isinstance(raw, list):
        return []
    kept = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        outlines = entry.get("outlines")
        page = entry.get("page_mm")
        if not isinstance(outlines, list) or not outlines:
            continue
        if not all(isinstance(o, dict) and o.get("segments") for o in outlines):
            continue
        if not (isinstance(page, (list, tuple)) and len(page) == 2):
            continue
        if not str(entry.get("token") or "").strip():
            continue
        kept.append(dict(entry))
    return kept


def load_templates(path=None):
    """Every imported production template. The customer picker and the generator both read this."""
    load(path)
    return [dict(t) for t in _cache["templates"]]


def get_template(token, path=None):
    """One template by its token, or None. A stale link is a normal outcome, not an error."""
    return next((t for t in load_templates(path) if t.get("token") == token), None)


def save_templates(templates, path=None):
    """Replace the template list, keeping the materials, messages and rule levels untouched."""
    _write(path or DEFAULT_STORE, templates=_validate_templates(templates))
    return load_templates(path)


def upsert_template(template, path=None):
    """Add a template, or replace the one with the same token."""
    token = str(template.get("token") or "").strip()
    kept = [t for t in load_templates(path) if t.get("token") != token]
    stored = _validate_templates([template])
    if not stored:
        raise MaterialError("This template has no usable outline to store.")
    save_templates(kept + stored, path)
    return stored[0]


# What an edit may touch. Geometry is NOT on this list on purpose — an outline's ROLE can only be
# judged against the picture it came from, and an edit form has no picture.
EDITABLE_TEMPLATE_FIELDS = ("name", "bleed_mm", "safe_mm", "sewn_sides_mm",
                           "sides", "material", "note")


def update_template(token, changes, path=None):
    """Change a stored template's metadata in place. Returns the new record, or None if unknown.

    In place matters: `upsert_template` appends, so editing would send the row to the bottom of the
    list, and somebody correcting names across sixteen templates would watch them shuffle.
    """
    templates = load_templates(path)
    for index, template in enumerate(templates):
        if template.get("token") != token:
            continue
        merged = {**template, **{key: value for key, value in changes.items()
                                 if key in EDITABLE_TEMPLATE_FIELDS}}
        validated = _validate_templates([merged])
        if not validated:
            raise MaterialError("Ta zmiana zostawiłaby szablon bez użytecznego obrysu.")
        templates[index] = validated[0]
        save_templates(templates, path)
        return validated[0]
    return None


def _reordered(stored, wanted, key):
    """The stored records in the order `wanted` asks for, keeping anything it forgot to mention.

    Never a filter. A browser tab that has been open since before a template was added would send a
    list missing it, and silently dropping records because a stale tab did not know about them is a
    data loss disguised as a sort. Unknown ids are ignored; unmentioned records keep their relative
    order at the end.
    """
    by_id = {record.get(key): record for record in stored}
    ordered = [by_id.pop(one) for one in wanted if one in by_id]
    ordered.extend(record for record in stored if record.get(key) in by_id)
    return ordered


def reorder_templates(tokens, path=None):
    """Store the templates in this order. Returns the new list."""
    ordered = _reordered(load_templates(path), tokens, "token")
    save_templates(ordered, path)
    return ordered


def reorder_materials(ids, path=None):
    """Store the materials in this order. Returns the new list."""
    ordered = _reordered(load(path), ids, "id")
    save_all(ordered, path)
    return ordered


def remove_template(token, path=None):
    """Delete a template. Returns True if it existed."""
    before = load_templates(path)
    remaining = [t for t in before if t.get("token") != token]
    if len(remaining) == len(before):
        return False
    save_templates(remaining, path)
    return True


def load_messages(path=None):
    """The shop's overrides for customer-facing text. Empty means "use the defaults"."""
    load(path)
    return dict(_cache["messages"])


def load_rule_levels(path=None):
    """The shop's severity overrides per rule. Empty means every rule judges for itself."""
    load(path)
    return dict(_cache["rules"])


def save_messages(overrides, path=None):
    """Replace the message overrides, keeping every other block untouched."""
    _write(path or DEFAULT_STORE, messages=_validate_messages(overrides))
    return load_messages(path)


def save_rule_levels(overrides, path=None):
    """Replace the rule severities, keeping the materials and messages untouched."""
    _write(path or DEFAULT_STORE, rules=_validate_rule_levels(overrides))
    return load_rule_levels(path)


def _write(path, **changes):
    """Write the whole document atomically, changing only the blocks named.

    One writer for all three blocks, because there are three now: a saver that rebuilt the document
    from the two blocks IT knew about dropped the third, which is how a shop would lose its wording
    by editing a material.
    """
    document = {"version": 1,
                "materials": changes.get("materials", load(path)),
                "messages": changes.get("messages"),
                "rules": changes.get("rules"),
                "templates": changes.get("templates")}
    if document["messages"] is None:
        document["messages"] = (dict(_cache["messages"]) if _cache["path"] == path
                                else _read_block(path, "messages", _validate_messages))
    if document["rules"] is None:
        document["rules"] = (dict(_cache["rules"]) if _cache["path"] == path
                             else _read_block(path, "rules", _validate_rule_levels))
    if document["templates"] is None:
        document["templates"] = ([dict(t) for t in _cache["templates"]] if _cache["path"] == path
                                 else _read_block(path, "templates", _validate_templates))
    temporary = f"{path}.tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)
    load(path, force=True)


def get(material_id, path=None):
    """One material, or None. Callers that need a hard failure should say so themselves — a missing
    material is a normal outcome when a customer follows an old link."""
    for material in load(path):
        if material["id"] == material_id:
            return material
    return None


def save_all(materials, path=None):
    """Write the whole store atomically, so a crash mid-write cannot leave a shop with no rules."""
    path = path or DEFAULT_STORE
    validated = []
    for entry in materials:
        validated.append(_validate(entry, existing_ids=[m["id"] for m in validated]))
    _write(path, materials=validated)
    return validated


def _read_block(path, key, validate):
    """One block straight off disk, for a save that must not lose what it is not changing."""
    try:
        with open(path, encoding="utf-8") as handle:
            return validate(json.load(handle).get(key))
    except (OSError, json.JSONDecodeError):
        return {}


def upsert(material, path=None):
    """Add a material, or replace the one with the same id. Returns the stored version."""
    incoming = dict(material)
    material_id = str(incoming.get("id", "")).strip().lower()
    kept = [m for m in load(path) if m["id"] != material_id]
    # Validate against the OTHERS only, so editing a material is not rejected as a duplicate of
    # itself — the mistake that makes an admin panel infuriating.
    stored = _validate(incoming, existing_ids=[m["id"] for m in kept])
    save_all(kept + [stored], path)
    return stored


def remove(material_id, path=None):
    """Delete a material. Returns True if it existed."""
    remaining = [m for m in load(path) if m["id"] != material_id]
    if len(remaining) == len(load(path)):
        return False
    save_all(remaining, path)
    return True
