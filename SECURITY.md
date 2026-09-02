# Security notes

What a hostile upload can and cannot do to this app, as of 2026-09-02. Read this before touching
anything that takes bytes from a customer: `/api/check`, `/api/check-path`, `/api/recheck/<token>`,
`/api/report`, and the admin's template import.

## Nothing from a file is ever executed

The file is parsed, measured and rendered — never opened by an application, never run. PDF
JavaScript, launch actions and embedded files are inert: pikepdf reads objects, pypdfium2
rasterises pages, neither executes anything. Rasters go through Pillow. Report PDFs are generated
by reportlab from text and one PNG; nothing from the customer's file is copied into them except
the preview picture.

## Ceilings, and where they are enforced

| What                              | Ceiling                              | Where                                   |
|-----------------------------------|--------------------------------------|-----------------------------------------|
| Upload size                       | `MAX_CONTENT_LENGTH` (1 GB)          | Flask, before the view runs             |
| Path-checked file size            | same                                 | `api_check_path`, before reading        |
| Path-checked file type            | pdf/tif/tiff/jpg/jpeg/png by name    | `api_check_path`, before reading        |
| Raster pixels                     | 400 million (`Image.MAX_IMAGE_PIXELS`) | `raster.py`, at `Image.open` — before decode |
| Render size                       | 4000 px longest side                 | `measure.MAX_RENDER_PX`, `raster.render_array` |
| PDF pages read for structure      | 12                                   | `structure.MAX_PAGES_READ`              |
| Text glyphs measured              | 20 000                               | `measure.MAX_CHARS_MEASURED`            |
| Content-stream operators walked   | 2 000 000 per page, forms included   | `outline.MAX_OPERATORS_WALKED`          |
| Form XObject nesting              | 4                                    | `outline.MAX_FORM_DEPTH`                |
| Held uploads (re-check)           | 1.5 GB total, 30 min, oldest evicted | `app.UPLOAD_HOLD_MAX_BYTES`             |
| Report body                       | 12 MB, 200 rows, strings capped      | `api_report`                            |

Every parser call that can fail is wrapped: an unreadable file is an ANSWER (`recognised: false`),
never a traceback.

## Paths

`/api/check-path` resolves the pasted path with `os.path.realpath` FIRST and only then tests it
against the configured roots (`PREPRESS_PATH_ROOTS`); `..`, symlinks, junctions and drive letters
are gone before the test, and nothing outside a root is even `stat`-ed. The refusal message does
not say whether the target exists. With no roots configured the endpoint is a 404.

Note that a shop's FTP with ONE shared login already lets every customer read every other
customer's upload; this endpoint does not widen that, but it does not narrow it either.

## Tokens

A held upload's token (`secrets.token_urlsafe(16)`) is bound to the caller who sent the file — the
session subject, or the door they came through. Another caller presenting the token gets a 410, as
if it never existed. Tokens expire after 30 minutes of silence.

## What the page renders

Every string that reaches `innerHTML` goes through `escapeHtml`, including file names (in tiles,
in the mail body via `encodeURIComponent`) and every sentence the server returns. The server's
own sentences come from `messages.py` or the shop's overrides — a shop admin CAN put HTML into a
message override; that is the admin's own page.

## Known, accepted

- No per-IP rate limit in the app. The public door sits behind the shop's login and Cloudflare;
  the LAN door is the LAN. A rate-limit rule at the CDN is the right place if it is ever needed.
- A 1 GB upload is held in memory in full while it is checked (and up to 1.5 GB for re-checks).
  This is the design — the customer's bytes never touch the disk — and it means concurrent large
  checks compete for RAM on the host.
- Parser bugs in Pillow, pikepdf/qpdf, pdfium or FreeType are inherited. Keep them current:
  Pillow 12.3, pikepdf 10.12, pypdfium2 5.13, reportlab 5.0 at the time of writing.

## Reporting

Open an issue on the repository, or write to the address in the footer of the shop's deployment.
