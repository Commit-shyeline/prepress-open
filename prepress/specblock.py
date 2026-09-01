"""The spec panel on a template page.

Modelled on a real trade template a display manufacturer issues to its resellers, because that
file solves the problem better than my first attempt did. What it does, and what is copied here:

* the page is drawn at 1:4, but every dimension PRINTED on it is the real, full-size one. That is the
  fix for the expensive failure mode: a designer who does not notice the scale still reads the true
  size off the page.
* the scale gets its own cell, large. Not a footnote.
* two dimensions are stated, not one — the graphic area (brutto) and the safe area — so there is no
  ambiguity about which number the customer is designing to.
* it all sits INSIDE the artwork, centred, in a pale tint. Visible enough that nobody misses it,
  obviously not artwork, and the customer deletes the layer before sending.
* labels are bilingual, because the same template goes to customers who do not share a language.

Positions other than `panel` are kept for materials where the artwork must stay untouched: `below`
puts it in a strip added under the page, `margin` mimics the rotated box a real flag file uses, `none`
leaves only the invisible stamp.
"""
from reportlab.pdfbase.pdfmetrics import stringWidth

PT_PER_MM = 72.0 / 25.4

# The reference template's palette: red text on a pale red wash reads as instruction, never as artwork.
PANEL_TEXT = (0.85, 0.11, 0.13)
PANEL_FILL = (0.99, 0.93, 0.93)
PANEL_RULE = (0.85, 0.45, 0.45)
MUTED_TEXT = (0.97, 0.78, 0.78)
RULE_RGB = (0.55, 0.55, 0.58)
TEXT_RGB = (0.20, 0.21, 0.25)

FONT = "Helvetica"
BOLD = "Helvetica-Bold"

# The panel is sized as a SHARE of the page, not a fixed number of millimetres. Measured off the
# reference, whose panel is about 180 mm on a 784 mm page, i.e. roughly 23 %. A fixed 190 mm looked
# right on a poster and became an unreadable speck on a 3 m banner.
#
# Measured from the LONGER side, not the width, and that correction came from a real sheet: on a
# 801 x 2401 mm flag, 23 % of the WIDTH is 184 mm — which is 23 % across but only 2.1 % down. The
#长 side is what sets the zoom when someone looks at the whole page, so the panel came out at 3 px
# and could not be read without magnifying. From the longer side it is 552 mm, and the digits are
# about 24 mm. On a page near square nothing changes: the 784 mm reference still gives 180 mm.
PANEL_WIDTH_FRACTION = 0.23
MIN_PANEL_WIDTH_MM = 120.0
MAX_PANEL_WIDTH_MM = 900.0
# ...but never so wide that it dominates a narrow sheet.
MAX_PANEL_SHARE_OF_WIDTH = 0.70
# And never more than this share of the area it must sit INSIDE. A real VENTO S has a 553 mm safe area
# and the panel had grown to 552 mm — it fitted to the millimetre, so any offset pushed it out
# through the guides.
MAX_PANEL_SHARE_OF_SAFE = 0.80
PANEL_ASPECT = 0.28          # height as a share of the panel's width, also from the reference
FILENAME_LABEL = "nazwa pliku / file name:"


def draw(pdf, entry, page_width_mm, page_height_mm, describe):
    """Draw the spec block for `entry` on the current page."""
    position = entry.get("spec_position", "panel")
    if position == "none":
        return
    if position == "below" and entry.get("spec_strip_mm"):
        _draw_strip(pdf, entry, page_width_mm, entry["spec_strip_mm"], describe)
    elif position == "margin":
        _draw_margin_box(pdf, entry, page_width_mm, page_height_mm, describe)
    else:
        _draw_panel(pdf, entry, page_width_mm, page_height_mm)


def scale_up_for(panel_h_mm):
    """How much bigger than the reference panel this one is. Every size inside it follows this."""
    return max(1.0, panel_h_mm / 46.0)


def _cells(entry):
    """The three cells, in the order the reference template uses: scale, graphic area, safe area.

    Every number is FULL SIZE, whatever scale the page is drawn at — that is the whole point.
    """
    brutto_w, brutto_h = entry["brutto_mm"]
    safe_w, safe_h = entry["safe_mm_box"]
    return [
        ("SKALA / SCALE", f"1:{entry['scale']}", True),
        ("OBSZAR GRAFICZNY / GRAPHIC AREA", f"{brutto_w:.0f} (w) x {brutto_h:.0f} (h) mm", False),
        ("OBSZAR BEZPIECZNY / SAFE AREA", f"{safe_w:.0f} (w) x {safe_h:.0f} (h) mm", False),
    ]


def panel_size_mm(page_width_mm, art_height_mm, safe_width_mm=None):
    """How big the spec panel is on this page. Separate so it can be measured, not only looked at.

    From the LONGER side, because that is what sets the zoom when someone opens the whole sheet: on a
    801 x 2401 mm flag, 23 % of the WIDTH is 184 mm, which is 2.1 % of the height and rendered about
    three pixels tall.

    Then capped twice: against the page, so it never dominates a narrow sheet, and against the SAFE
    area when the caller knows one, so it cannot spill over the guides it is describing.
    """
    longest_side_mm = max(page_width_mm, art_height_mm)
    width = min(MAX_PANEL_WIDTH_MM,
                max(MIN_PANEL_WIDTH_MM, longest_side_mm * PANEL_WIDTH_FRACTION))
    width = min(width, page_width_mm * MAX_PANEL_SHARE_OF_WIDTH)
    if safe_width_mm:
        width = min(width, safe_width_mm * MAX_PANEL_SHARE_OF_SAFE)
    return width, min(width * PANEL_ASPECT, art_height_mm * 0.32)


def _draw_panel(pdf, entry, page_width_mm, page_height_mm):
    """Centred info panel inside the artwork — the reference template's layout."""
    strip_mm = entry.get("spec_strip_mm", 0.0)
    art_height_mm = page_height_mm - strip_mm
    # The box the panel must live inside. A rectangular template's safe area IS the page, so the
    # fallback keeps that path unchanged; a shaped one hands us the real thing.
    home = entry.get("safe_box_mm") or (0.0, strip_mm, page_width_mm, strip_mm + art_height_mm)
    home_w = home[2] - home[0]
    panel_w, panel_h = panel_size_mm(page_width_mm, art_height_mm, home_w)
    # Centred on the AREA, not on the page: on a flag the body sits 137 mm from one edge and 30 from
    # the other, so a page-centred panel walks out through the guides.
    left = home[0] + (home_w - panel_w) / 2
    bottom = home[1] + ((home[3] - home[1]) - panel_h) / 2

    # Heading above the panel: what this template IS. The label is only appended when it says
    # something the name does not — a heading reading "Flaga VENTO S — PRZÓD · Flaga VENTO S PRZÓD"
    # is the same fact twice.
    title = entry["material_name"]
    label = (entry.get("label") or "").strip()
    if label and label.lower() not in title.lower():
        title = f"{title}  ·  {label}"
    title_size = _pick_size(title, BOLD, home_w * 0.98, 16.0 * scale_up_for(panel_h), 7.0)
    pdf.setFillColorRGB(*PANEL_TEXT)
    pdf.setFont(BOLD, title_size)
    pdf.drawCentredString((home[0] + home_w / 2) * PT_PER_MM,
                          (bottom + panel_h + panel_h * 0.20) * PT_PER_MM, title)

    scale_up = scale_up_for(panel_h)
    cells = _cells(entry)
    cell_w = panel_w / len(cells)
    # ONE size for every caption, not one per cell. `_pick_size` shrinks each string to fit its own
    # cell, so three captions of different lengths came out three different sizes; the longest one
    # binds them all. Measured on a 442 mm panel: 5.1 mm before, 7.3 mm after.
    caption_size = min(_pick_size(caption, FONT, cell_w * 0.92, 9.0 * scale_up, 3.4)
                       for caption, _value, _big in cells)
    for index, (caption, value, big) in enumerate(cells):
        x = left + index * cell_w
        pdf.setFillColorRGB(*PANEL_FILL)
        pdf.setStrokeColorRGB(*PANEL_RULE)
        pdf.setLineWidth(0.5)
        pdf.setDash((), 0)
        pdf.rect(x * PT_PER_MM, bottom * PT_PER_MM, cell_w * PT_PER_MM, panel_h * PT_PER_MM,
                 stroke=1, fill=1)

        centre_pt = (x + cell_w / 2) * PT_PER_MM
        pdf.setFillColorRGB(*PANEL_TEXT)
        value_size = _pick_size(value, BOLD, cell_w * 0.9,
                                (20.0 if big else 9.0) * scale_up, 5.5)
        pdf.setFont(BOLD, value_size)
        pdf.drawCentredString(centre_pt, (bottom + panel_h * (0.42 if big else 0.34)) * PT_PER_MM,
                              value)
        pdf.setFont(FONT, caption_size)
        # The inset has to follow the caption: at a fixed 5.5 mm a 7.3 mm caption climbs out through
        # the top edge of its own cell.
        caption_inset_mm = caption_size / PT_PER_MM * 1.5
        pdf.drawCentredString(centre_pt, (bottom + panel_h - caption_inset_mm) * PT_PER_MM,
                              caption)

    # The line the customer writes their own file name on, under the panel. Scales WITH the panel:
    # it used to be a fixed 7 pt, which is 2.5 mm of text sitting under 24 mm digits on a 2.4 m sheet.
    # Raised from 7.0: on a 442 mm panel that was 6.6 mm of text under 19 mm digits, and the line
    # has room to spare — label plus rule came to 136 mm inside a 553 mm safe area.
    filename_size = 10.0 * scale_up
    rule_length_pt = 70.0 * scale_up
    pdf.setFont(FONT, filename_size)
    pdf.setFillColorRGB(*PANEL_TEXT)
    label_pt = stringWidth(FILENAME_LABEL, FONT, filename_size)
    baseline = (bottom - 8 * scale_up) * PT_PER_MM
    start_pt = (home[0] + home_w / 2) * PT_PER_MM - (label_pt + rule_length_pt) / 2
    pdf.drawString(start_pt, baseline, FILENAME_LABEL)
    pdf.setStrokeColorRGB(*PANEL_RULE)
    pdf.setLineWidth(0.4 * scale_up)
    pdf.line(start_pt + label_pt + 4 * scale_up, baseline - 1.5 * scale_up,
             start_pt + label_pt + (rule_length_pt + 4) * scale_up, baseline - 1.5 * scale_up)

    _draw_zone_watermark(pdf, entry, page_width_mm, strip_mm, art_height_mm, bottom, home)


def _draw_zone_watermark(pdf, entry, page_width_mm, strip_mm, art_height_mm, panel_bottom_mm,
                         home=None):
    """A pale label naming the zone, the way the reference writes SAFE AREA across the lower half.

    Placed low enough not to collide with the panel, and skipped when the page is too short to hold
    it without overlapping — a watermark on top of the spec would defeat both.
    """
    text = "OBSZAR BEZPIECZNY / SAFE AREA"
    home = home or (0.0, strip_mm, page_width_mm, strip_mm + art_height_mm)
    home_w = home[2] - home[0]
    # From the LONGER side, for the same reason the panel is: on a 1:3 flag, a size taken from the
    # width alone is invisible once the whole sheet is on screen. Fitted to the SAFE area, since a
    # label that runs over the cut line is the thing it is warning against.
    longest_side_mm = max(page_width_mm, art_height_mm)
    size = _pick_size(text, BOLD, home_w * 0.82, max(15.0, longest_side_mm * 0.055), 6.0)
    y_mm = home[1] + (home[3] - home[1]) * 0.24
    if y_mm > panel_bottom_mm - 16:
        return
    pdf.setFillColorRGB(*MUTED_TEXT)
    pdf.setFont(BOLD, size)
    pdf.drawCentredString((home[0] + home_w / 2) * PT_PER_MM, y_mm * PT_PER_MM, text)


def _draw_strip(pdf, entry, page_width_mm, strip_mm, describe):
    """A ruled strip along the bottom of the page, below the artwork — for materials whose artwork
    must stay completely clear."""
    strip_pt = strip_mm * PT_PER_MM
    width_pt = page_width_mm * PT_PER_MM
    margin_pt = 4 * PT_PER_MM

    pdf.setStrokeColorRGB(*RULE_RGB)
    pdf.setLineWidth(0.75)
    pdf.setDash((), 0)
    pdf.line(0, strip_pt, width_pt, strip_pt)

    brutto_w, brutto_h = entry["brutto_mm"]
    safe_w, safe_h = entry["safe_mm_box"]
    spec = (f"{describe(entry)}   ·   obszar graficzny {brutto_w:.0f}×{brutto_h:.0f} mm"
            f"   ·   obszar bezpieczny {safe_w:.0f}×{safe_h:.0f} mm")
    pdf.setFillColorRGB(*TEXT_RGB)
    pdf.setFont(BOLD, 8.5)
    pdf.drawString(margin_pt, strip_pt - (7.5 * PT_PER_MM),
                   _fit(spec, BOLD, 8.5, width_pt - 2 * margin_pt))
    pdf.setFont(FONT, 7.0)
    label_width = stringWidth(FILENAME_LABEL, FONT, 7.0)
    baseline = strip_pt - (15.5 * PT_PER_MM)
    pdf.drawString(margin_pt, baseline, FILENAME_LABEL)
    pdf.setLineWidth(0.4)
    pdf.line(margin_pt + label_width + 3, baseline - 1.5, width_pt - margin_pt, baseline - 1.5)


def _draw_margin_box(pdf, entry, page_width_mm, page_height_mm, describe):
    """The flag-file placement: a bordered box in the top-right margin, text rotated 90°.

    Sized from the real file — about 16 mm wide and 120 mm tall, sitting in the bleed that is
    trimmed away.
    """
    bleed_mm = entry["bleed_mm"] / entry["scale"]
    box_width_mm = min(max(bleed_mm - 2.0, 6.0), 16.0)
    box_height_mm = min(page_height_mm * 0.35, 130.0)
    right_pt = (page_width_mm - 1.0) * PT_PER_MM
    left_pt = right_pt - box_width_mm * PT_PER_MM
    top_pt = (page_height_mm - 8.0) * PT_PER_MM
    bottom_pt = top_pt - box_height_mm * PT_PER_MM

    pdf.setStrokeColorRGB(*RULE_RGB)
    pdf.setLineWidth(0.6)
    pdf.setDash((), 0)
    pdf.rect(left_pt, bottom_pt, right_pt - left_pt, top_pt - bottom_pt, stroke=1, fill=0)

    pdf.saveState()
    pdf.translate(left_pt + (box_width_mm * PT_PER_MM) / 2, bottom_pt + 3 * PT_PER_MM)
    pdf.rotate(90)
    pdf.setFillColorRGB(*TEXT_RGB)
    pdf.setFont(FONT, 7.0)
    available = (box_height_mm - 6) * PT_PER_MM
    pdf.drawString(0, -7.0 / 3, _fit(f"{FILENAME_LABEL}  {describe(entry)}", FONT, 7.0, available))
    pdf.restoreState()


def _pick_size(text, font, available_mm, largest_pt, smallest_pt):
    """The biggest size at which `text` fits the space — so a long material name shrinks instead of
    running off the panel."""
    available_pt = available_mm * PT_PER_MM
    size = largest_pt
    while size > smallest_pt and stringWidth(text, font, size) > available_pt:
        size -= 0.5
    return size


def _fit(text, font, size_pt, available_pt):
    if available_pt <= 0:
        return ""
    while text and stringWidth(text, font, size_pt) > available_pt:
        text = text[:-2] + "…" if len(text) > 3 else ""
    return text
