"""Flat rasters — TIFF, JPEG, PNG — as the thing being checked.

A raster has no page, no fonts, no separations and no declared boxes: it is pixels and a colour
mode. What it CAN be judged on is what the ink does (bleed coverage, safe area — the same render
maths as a PDF page, `measure.py`), its resolution at the size the customer declared (pixels ÷
inches, the file's own DPI tag being only a hint), its colour mode, whether it was flattened, and
its frame count. Everything else returns no finding rather than a pass.

Pillow (MIT-compatible HPND) does the decoding. Its decompression-bomb guard is RAISED, not
removed: a 3 × 1.5 m banner at 150 DPI is 157 million pixels, twice the default ceiling, and this
tool exists for exactly such files. 400 million pixels (a 5 × 3 m banner at 150 DPI, ~1.2 GB of
RGB) is the ceiling; a file claiming more is refused as unreadable BEFORE any pixel is decoded, so
a 200-byte PNG header that promises 100 000 × 100 000 pixels cannot take the server down.
"""
import io

from PIL import Image

from . import structure

Image.MAX_IMAGE_PIXELS = 400_000_000
# Pillow warns between MAX_IMAGE_PIXELS and twice that, and raises above; we want the raise for
# anything over the ceiling, so the warning is promoted to the error the callers already handle.
import warnings                                       # noqa: E402
warnings.simplefilter("error", Image.DecompressionBombWarning)

# A DPI tag under or over these is a default some export wrote, not a statement about the print.
PLAUSIBLE_DPI = (30, 2400)
MODE_TO_COLOUR_SPACE = {"CMYK": "DeviceCMYK", "RGB": "DeviceRGB", "RGBA": "DeviceRGB",
                        "L": "DeviceGray", "LA": "DeviceGray", "1": "DeviceGray", "P": "Indexed",
                        "PA": "Indexed", "I;16": "DeviceGray", "I": "DeviceGray", "F": "DeviceGray",
                        "YCbCr": "DeviceRGB", "LAB": "Lab", "HSV": "DeviceRGB"}


def is_raster(data):
    """True when Pillow recognises the bytes as an image (and they are not a PDF).

    The header alone decides — the pixel ceiling is enforced by every function that decodes, and a
    bomb therefore comes back as an unreadable raster rather than as "not a raster, try PDF".
    """
    if data[:5] == b"%PDF-":
        return False
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Image.DecompressionBombError:
        return True
    except Exception:                                # noqa: BLE001 — not an image is the answer
        return False


def page_count(data):
    with Image.open(io.BytesIO(data)) as image:
        return int(getattr(image, "n_frames", 1) or 1)


def _frame(data, page_index):
    image = Image.open(io.BytesIO(data))
    if page_index and getattr(image, "n_frames", 1) > page_index:
        image.seek(page_index)
    return image


def page_mm(data, page_index=0):
    """The physical size the file's DPI tag implies, or None when the tag is missing or absurd."""
    with _frame(data, page_index) as image:
        dpi = image.info.get("dpi")
        if not dpi:
            return None
        x_dpi, y_dpi = (float(dpi[0]), float(dpi[1])) if isinstance(dpi, tuple) else (float(dpi),) * 2
        if not all(PLAUSIBLE_DPI[0] <= v <= PLAUSIBLE_DPI[1] for v in (x_dpi, y_dpi)):
            return None
        return (image.width / x_dpi * 25.4, image.height / y_dpi * 25.4)


def facts(data, filename=""):
    """What a raster declares about itself, in the shape `structure.facts` gives for a PDF."""
    result = {"readable": False, "reason": "", "pages": 0, "producer": "", "creator": "",
              # None, not []: a rule that reads these must say "not applicable", not "clean".
              "fonts": None, "colour_spaces": [], "spot_names": None, "overprint": False,
              "shows_text": False, "raster_alpha": None, "raster_mode": None}
    result.update(structure.filename_facts(filename))
    try:
        with Image.open(io.BytesIO(data)) as image:
            result["pages"] = int(getattr(image, "n_frames", 1) or 1)
            result["raster_mode"] = image.mode
            result["colour_spaces"] = [MODE_TO_COLOUR_SPACE.get(image.mode, image.mode)]
            # Not flattened: an alpha channel, a transparency key, or TIFF extra samples. Layers
            # as such do not survive into a TIFF's pixel data, but their alpha does.
            extra = image.info.get("transparency") is not None
            try:
                extra = extra or bool(image.tag_v2.get(338))    # ExtraSamples
            except AttributeError:
                pass
            result["raster_alpha"] = ("A" in image.mode) or extra
            result["readable"] = True
    except Exception as error:                       # noqa: BLE001 — a bad upload is a verdict
        result["reason"] = type(error).__name__
    return result


def render_array(data, page_index, artwork_mm, target_px_per_mm, max_px):
    """(RGB numpy array, pixels per mm, effective DPI): the frame laid over the declared artwork.

    The raster IS the artwork, so its pixel grid maps onto `artwork_mm` exactly; the effective DPI
    falls out of that. Downsampled for measuring when finer than the target — the measurement
    layer wants 2 px/mm, not 150 DPI.
    """
    import numpy

    with _frame(data, page_index) as image:
        width_px, height_px = image.size
        dpi = min(width_px / (artwork_mm[0] / 25.4), height_px / (artwork_mm[1] / 25.4))
        px_per_mm = min(target_px_per_mm, max_px / max(artwork_mm))
        target = (max(1, int(round(artwork_mm[0] * px_per_mm))),
                  max(1, int(round(artwork_mm[1] * px_per_mm))))
        rgb = image.convert("RGB")
        if target != rgb.size:
            rgb = rgb.resize(target, Image.Resampling.BOX)
        px_per_mm = rgb.width / artwork_mm[0]
        return numpy.asarray(rgb), px_per_mm, round(dpi, 1)


def preview_png(data, page_index=0, max_pixels=1400):
    """A PNG of the frame, longest side capped — the picture the overlay is drawn on."""
    with _frame(data, page_index) as image:
        rgb = image.convert("RGB")
        longest = max(rgb.size) or 1
        if longest > max_pixels:
            factor = max_pixels / longest
            rgb = rgb.resize((max(1, int(rgb.width * factor)), max(1, int(rgb.height * factor))),
                             Image.Resampling.BOX)
        holder = io.BytesIO()
        rgb.save(holder, format="PNG", optimize=True)
        return holder.getvalue()
