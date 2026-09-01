# Roadmap

STATUS: phases 1–4 DONE (see below). 121 tests.

> admins can import templates of production files, the app generates end-user (customer) templates
> with brutto, netto and safe area which they design on. than they import it back to a page like
> [the checker], it makes the preflight, report back what's wrong against rules set by admin - and if they
> want there (user ui panel) - they can send those file to use, where graphic team can download it:
> source file, cut and production file

## Why this is worth doing (researched 2026-08-22)

Open source has PDF/X and PDF/A **conformance** validators — `pdf-preflight` (Ruby),
`pdfbox-preflight` (Java), veraPDF, which states outright that it is not a print-production tool.
Commercially the category is Enfocus PitStop, Markzware FlightCheck, Callas pdfToolbox, plus preflight
buried inside Agfa Apogee and Kodak Prinergy. The top GitHub repos for print prepress preflight have
**3, 0 and 0 stars**.

Nothing open source answers "does this artwork fit the job that was ordered" — because nothing owns
the template the job was designed on. This loop closes that: the printer issues the template,
so the check has an authoritative thing to check against. That is the differentiator, not any
individual rule.

## The five stages, and how far we already are

| stage | exists today | new work |
|---|---|---|
| 1. admin imports production templates | `scripts/build_flag_templates.py` → `state/flag_templates.json`, 75 templates measured. Entries already carry `finished_mm` (NETTO), `cut`, `safe`, `finishing`, `regmarks`. | admin UI, per-tenant library, import-time validation |
| 2. app generates the customer template (brutto/netto/safe) | geometry is extracted; `flag_output._draw_polyline` / `_stroke_publish` already stroke and label outlines | **the generator** — plus deriving `safe` where the template has none (it is `null` on some) |
| 3. customer designs on it | — | nothing |
| 4. upload back, preflight | `preflight_app.run_check` + 19 rules, already job-relative (ordered size, product, template outline) | nothing structural |
| 5a. report against **admin-set** rules | 19 `_check_*` functions, `PRODUCT_SAFE_AREA_MM`, `REQUIRED_BLEED_MM` — hardcoded constants | rules as DATA (see below) |
| 5b. "send it to us" | a `mailto:` link | a submission store |
| 5c. grafik downloads source + cut + production | `api_cut` / `api_print` / `api_print_flat` exist and work | they are ephemeral — artifacts sweep after an hour |

## The three decisions that are cheap now and expensive later

**1. Stamp the template on the way out, read the stamp on the way back.** Today `match_template`
GUESSES which template a file belongs to, from size + filename + inner frames, and that guessing is
the single biggest source of error in this tool's history — it once picked the wrong one of three
nested outlines and produced a cut 12 mm off, and the word-overlap matcher exists because filename
matching kept missing. In this loop we ISSUE the template, so write an id into it (XMP or a private
dict key) and read it back: the check then knows exactly which template and which rule profile apply.
pikepdf writes that losslessly — measured 2026-08-22, a TrimBox edit left the content stream
byte-identical and the re-render at a pixel delta of 0.0000. Keep the geometric matcher as the
fallback for customers who flatten the metadata away.

### How durable is the stamp, really? (2026-08-23)

Worth being precise, because "the stamp identifies the file" is doing a lot of work. What ships today
is **metadata, not a drawn object**: a private `/PrepressTemplate` key on the page dictionary, written
by `generate._stamp_pages` and read by `identify.read_stamp`. A designer deleting layers or objects
cannot remove it, and it carries per PAGE, which document-level XMP cannot do — one downloaded PDF
holds several different templates.

But any carrier inside the file can be lost, and this one is lost the moment a designer builds a NEW
document and re-exports. Measured: it survives a pikepdf/qpdf save (page dict AND XMP both intact).
**Not measured, and the cases that actually matter:** Illustrator, InDesign, CorelDRAW and Acrobat
re-exports — none of those tools is on this machine, and LibreOffice's PDF→PDF path crashes on this
build (`rc 0xC0000409`), so it could not stand in for them. Assume the stamp is lost on a rebuild
until somebody measures it on the real tools.

**Geometry cannot be the answer either, and that IS measured.** Against the shipped example
materials, one returned page of 540 × 740 mm can legitimately come from four different jobs:

| material | netto that produces a 540 × 740 page | safe |
|---|---|---|
| banner-frontlit-510 | 500 × 700 mm | 30 mm |
| mesh | 500 × 700 mm | 40 mm |
| flag-polyester-117 | 510 × 710 mm | 60 mm |
| poster-paper-200 | 534 × 734 mm | 5 mm |

A 34 mm spread in the finished size and a 55 mm spread in the safe margin, from one page size. Picking
wrong is a worse version of the 12 mm cut this whole approach exists to prevent, so a geometric match
may only ever be offered as a HINT for a human to confirm — never resolved silently.

**Therefore the identity does not belong in the file at all.** We ISSUE the template, so the strongest
carrier is the transaction: a job id minted at download, carried in the upload URL, so the returning
file is identified by WHERE it came back rather than by what survived inside it. That costs nothing
extra — it is the same piece of work as building the upload page, which does not exist yet. Layered,
strongest first:

1. **the job id in the link** — immune to anything a design tool does to the file;
2. **the metadata stamp** — free, and a genuine cross-check: stamp disagreeing with job id means the
   customer uploaded the wrong file, which is worth catching on its own;
3. **geometry** — a hint, shown for confirmation;
4. **ask** — a short list of that customer's open jobs beats any guess.

**2. Rules as data from day one.** "Rules set by admin" and 19 hardcoded Python checks cannot
coexist — every new rule would be a code change and a release. Model a **profile**: template geometry
+ a declarative rule set, and ship our current 19 as the default profile so behaviour is unchanged on
day one. Retrofitting this means rewriting every check.

**3. Stage 5b inverts a rule the current code treats as a virtue.** `preflight_app`'s docstring says
uploaded bytes are NEVER written to disk — that is why there is nothing to retain or leak. "Send those
files to us so the graphic team can download them" requires the opposite: durable storage of another
company's artwork, with retention, deletion and access control. Decide it deliberately.

## The PyMuPDF question, which decides the licence

The preflight engine uses ~9 fitz capabilities. pikepdf (MPL-2.0, on qpdf) covers the structural ones
better than fitz — proven on real production files: page boxes (a sticker sheet reporting MediaBox
958.7×979.9 mm against TrimBox 954.7×975.9 mm, i.e. 2 mm of bleed per side), separations (`Cut` and `Regmark` found in a generated cut file),
content-stream operators (text-showing count = the real "converted to curves" test), and lossless
writes. pypdfium2 (Apache-2.0/BSD-3) covers rendering. **pikepdf cannot rasterise at all**, so the
pair is needed.

Remaining gaps, hardest first: `get_drawings()` (find the template's strokes — manual operator walk
with CTM tracking, which we already wrote once for guide-path removal); CMYK pixmaps for the flat-TIFF
ink measurement; image DPI without `get_bboxlog()`; and authoring (`new_shape` / `show_pdf_page` /
`insert_image`) via form XObjects plus reportlab, already a dependency.

If that lands, PyMuPDF leaves the codebase, the service needs no AGPL publication, and the licence
becomes a free choice.

## Immediate small win, independent of all of the above

Add a preflight check that **verifies the `Cut` and `Regmark` separations exist** in a cut file. Our
own lessons file records a night lost to a geometrically perfect cut file the machine could not read
because those colorants were missing, and nothing verifies them today. pikepdf does it in ~10 lines.


## Phase 1 — DONE

A print shop defines materials; a customer picks one, types sizes, queues them and downloads a
template PDF that can identify itself when it comes back.

- `prepress/materials.py` — materials ARE the rules (bleed, safe margin, min DPI, roll width,
  colour), stored in one JSON file that reloads on change, edited from the admin panel. No rule is
  hardcoded, which is the whole point.
- `prepress/item.py` — material + typed dimensions → the three boxes, plus two constraints caught
  before anyone designs: the roll width (with rotation offered when it would fit turned), and the
  200-inch PDF page ceiling, past which the template is emitted at a declared scale.
- `prepress/generate.py` — a queue becomes one multipage PDF, one page per item, each **stamped**.
- `prepress/identify.py` — reads the stamp back. A file without one says so instead of being guessed
  at.
- `prepress/rules.py` — three rules driven by the material's own numbers, and the shape the other
  sixteen get ported onto.
- `prepress/app.py` — an open customer surface and a token-gated admin panel. No token configured
  means the panel is disabled, not open.
- `prepress/messages.py` — **every customer-facing sentence is data.** The engine emits a notice CODE
  plus numbers; the shop owns the wording and edits it in the panel. This exists because of a real
  mistake: the first build told customers "wider than the 3100 mm roll — it will be printed rotated",
  which is a production detail, not a problem, and it made a perfectly good job look broken. The code
  cannot know which of a shop's constraints a customer should worry about, so it stopped guessing.

**The roll rule, as a print shop actually works:** exceeding the roll in ONE direction is silent — the
job is turned, and how we fit it is production's business. Exceeding it in BOTH means the graphic is
panelled: printed in strips and welded into one piece, which IS worth saying because the welds are
visible. Past the welding ceiling (25 × 15 m by default, per material) the job is refused.

58 tests. Verified in a browser end to end: mixed-material queue, live preview of the boxes, and a
downloaded PDF whose pages identify themselves.

**Deliberately not in phase 1:** automatic extraction of custom die outlines, the other sixteen rules,
the ink measurements (bleed coverage and safe-area intrusion report `info`, honestly, rather than
implying a pass), tenancy, and submission storage.

## Phase 2 — the measurement layer — DONE

Render with pypdfium2 and measure what the ink rules need: blank edges, safe-area intrusion, and
resolution at final size. That turned three `info` results into real verdicts.

Two conceptual errors were caught only by testing against a blank template, which the first version
scored identically to full-bleed artwork, and both are recorded in `prepress/measure.py`: a guide
RECTANGLE inks every row, so "is there ink in this row" cannot tell a template from a design
(coverage can); and a full-bleed background is SUPPOSED to fill the safe-area ring, so any-ink
flagged every correct file (local contrast — detail — separates type from flat colour).

The resolution gap this section used to end on is now closed — see phase 4.

## Phase 3 — the rules become the shop's — DONE

Fourteen rules now run, and not one of them holds a threshold, a sentence or a severity.

* **Ported from the in-house engine** (`prepress/structure.py`, read with pikepdf — no rendering, so
  it works on files too big to rasterise): office-application origin, colour mode, extra inks, the
  die line, text not converted to curves, overprint, page count and the two filename habits.
  Every extractor was diffed against the PyMuPDF engine it replaces on real production files: fonts,
  producer, page count, overprint and spot names came out IDENTICAL, `['Cut', 'Regmark']` included.
  Two differences were found by that diff and fixed rather than papered over — `ICCBased` alone never
  contains the string "RGB" (the profile's `/N` does the work), and a `Separation` is a colorant, not
  a colour mode.
* **Severity is data.** A shop silences any rule or moves it between info, amber and red from the
  panel, because "how bad is this" is a business judgement — RGB on a backlit banner and RGB on a
  fine-art print are not the same problem. A PASS is never re-graded: asking for "fonts: red" flags a
  file with fonts, it does not call a clean file broken.
* **The rules stopped writing sentences.** They were the last place in the engine that did. A finding
  is now a code plus its numbers, and `messages.py` holds the words — so the shop owns the wording of
  a verdict exactly as it already owned the wording of a refusal.
* **Two more material fields**, both with a control in the panel: `spec_position` (where the spec
  block sits) and `cut_path` (this material is cut work, so the die line is required and a second
  page is correct rather than a mistake).

What deliberately did NOT get ported: the flag-template matcher and its die library (a client's
shapes are the client's IP — the code can be public, the dies cannot), the flat-TIFF ink
measurements, and minimum text height, which needs the same CTM tracking as the DPI gap.

There is still no customer-facing page for the check itself — `/api/check` is API-only, and the
generator page does not upload anything yet. That is the next visible piece of the loop.

## Phase 4 — resolution, for real — DONE

The hardest remaining gap, and the last rule that was reporting "not measured" on ordinary files.

Nothing in a PDF records how big a placed image is. An image XObject is always drawn into the unit
square, so its printed size is whatever the current transformation matrix makes of that square —
which means the number can only come from walking the content stream and tracking the CTM.
`structure.placed_images` does that, through `q`/`Q`/`cm`, into form XObjects with their own
`/Matrix` and `/Resources`, and over inline images too.

Diffed against the PyMuPDF reference on four cases, and identical on all of them: a plain placement
(144 DPI), the same image rotated 90° (144 — a bounding box gets this wrong, because it is
axis-aligned and its width then belongs to the image's pixel HEIGHT; that mistake once reported 112
DPI for a banner that was really 75), a placement nested in a form at half scale (288), and a real
production label file (160 DPI at 52.9 × 7.9 mm, agreeing to three decimals on area fraction).

The verdict is decided by the artwork, not by the bullet points: placements under 1 % of the page are
decorative, and a 36 × 27 px icon must not condemn a 200 DPI banner.

Same phase, one more honesty fix: `fonts` was triggered by a `/Font` RESOURCE, which only proves a
font is available. reportlab — which draws this project's own templates — registers Helvetica whether
anything is typed or not, so a customer who pasted artwork over the spec panel was told to convert
text that did not exist. The trigger is now a text-showing operator, which is what the research notes
above already identified as the real "converted to curves" test.

The calibration case now exists as a test, and it is the one that matters most: a real CMYK full-bleed
return at 150 DPI passes all fourteen rules green, and the same file at 40 DPI produces exactly one
finding. A rule set that cries wolf on good work is worse than no rule set.

## Published, after the loop closed — 2026-09-01

The rule was that the repository stays private until the tool has proved itself inside a print shop
rather than only inside its test suite. That was the right test, and it named three things the engine
could not supply:

1. **A page a customer can upload on.** `/api/check` worked; nothing sat over it, so a month of use
   would have exercised the GENERATOR only and left the preflight — the whole differentiator —
   tested by nobody.
2. **A server meant for people.** Flask's development server says on every boot that it is not for
   this.
3. **Surviving a reboot.** Nothing started it automatically.

All three exist now: a checker page, `serve.py` on Waitress, and a service that comes back on its
own. Customers have used it. The CLA question, which was resting on the repository staying closed,
is live from here: anything a contributor sends has to be something a later dual-licence build could
still include.

## Panelling geometry — OUT OF SCOPE, decided 2026-08-23

Earlier drafts of this file listed "split an oversized job into welded strips" as future work. It is
not going to be built here. The tool says a job **will** be panelled, because the welds are visible in
the finished product and the customer should not be surprised by them. It does not say into how many
strips and it does not draw the layout — that is the graphic team's call, made against the roll
actually loaded, and it can still change after the file is accepted.

So the customer-facing sentence names the technique and nothing else, and the panel count stays where
production can use it: `item.resolve(...)["panels"]`, returned by `/api/resolve`. `{panels}` remains a
placeholder a shop can put back into its own wording if it disagrees.

## The submission store — direction chosen 2026-08-23

Stage 5b ("send those files to us, the graphic team downloads them") inverts what the current build
treats as a virtue: uploaded bytes never touch the disk, so there is nothing to retain or leak. The
decision was made deliberately rather than drifted into — **short retention plus a one-time link**:

- the accepted file is stored for a fixed number of days, set by the shop, and deleted automatically;
- the graphic team gets a single-use link rather than a browsable directory;
- every download is logged — who, what, when;
- deletion on request has to work, and be findable without a database console;
- **no backup of the store.** A backup is a second retention period wearing a different name, and it
  is the copy nobody remembers to purge.

Explicitly NOT chosen: customer accounts with permanent history. It is the better product for repeat
customers and the closer fit to the paid embed, but it turns a print-shop tool into a data controller
with data-export and account-deletion duties, and that is not the next thing this needs.

## Later: real dies, not just rectangles

Stage 1 of the loop still only handles rectangles. A shop's real production templates are irregular —
a flag with a sleeve, a sticker with a contour — and importing those means extracting an outline from
a PDF rather than deriving one from two numbers. The dies themselves stay private data the code loads:
publish the tool, not somebody's shapes.

## The commercial layer, and the licence knot it creates

The intended business model is open core plus a paid embed — a widget a seller drops into
WooCommerce (or any shop platform) so their customers get template generation and preflight at the
point of purchase.

That collides with AGPL. A copyleft licence is exactly what a commercial seller embedding a widget
does not want, and its network clause is what makes it unattractive to them. The normal resolution is
**dual licensing**: the same code offered as AGPL to the community and under a commercial licence to
sellers. That is only possible if ONE party owns the copyright.

So the decision that is free today and expensive later: **require a CLA (or an equivalent grant) from
every outside contributor from the very first pull request.** Accept one AGPL contribution without it
and that code can never be included in a commercially licensed build. The repository is private with
no contributors right now, which is the cheapest this will ever be.

Architecturally the embed needs three things the current build does not have, none of them hard yet:
a JSON API as the real product surface (already the shape — the HTML pages are thin clients over it),
per-seller tenancy with API keys, and a CORS origin allowlist per tenant.
