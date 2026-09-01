# prepress-open

Open-source prepress for print shops: **issue the template, then check the artwork against it.**

> ⚠️ **Young, but in use.** The whole loop runs: define materials, generate a template, hand it to a
> customer, take the file back on a page they can drop it onto, and get a report from fourteen rules
> measured off a render and read off the PDF's own objects. It has been serving one print shop's
> customers rather than only its own test suite. Expect rough edges at the seams, not in the engine.

## The gap this exists to fill

Open source has PDF **conformance** validators — PDF/X and PDF/A checkers such as veraPDF, which says
outright that it is not a print-production tool. Commercially, print preflight means Enfocus PitStop,
Markzware FlightCheck, Callas pdfToolbox, or whatever is embedded in a RIP.

None of them answers the question a print shop actually asks:

> *Does this artwork fit the job that was ordered?*

Is 5 m at this resolution acceptable? Is the logo inside the safe area **of this die**? Does the
artwork reach the cut line? Is the page the size the order says? Those questions need the **job** —
the ordered size, the product, the template — and not just the file.

So this project inverts the usual arrangement: **the print shop issues the template the customer
designs on**, which means the check has an authoritative thing to check against, and the customer
gets told what is wrong before the file reaches a plotter.

## The intended loop

1. **Admin imports a production template** — the shop's real die or layout.
2. **The app generates a customer template** with BRUTTO (bleed), NETTO (finished size) and SAFE AREA
   drawn and labelled, stamped with an identifier.
3. **The customer designs on it.**
4. **The customer uploads it back** and gets an automated prepress report — measured, not guessed,
   against the rules that shop's admin set.
5. **If they choose, they submit it**, and the graphic team downloads three files: the source, the cut
   file, and the print-ready production file.

Step 2 is what makes step 4 honest. Because the template is issued and stamped, the check reads the
identifier instead of guessing which template a file belongs to — a guess that, in the in-house
version, once produced a cut outline 12 mm off.

## What already works in the in-house tool (being extracted here)

- 19 measured preflight rules — size against the ordered size, resolution, colour space, bleed and
  trim boxes, fonts converted to curves, artwork reaching the cut line, safe-area intrusion
- an annotated preview and a PDF report a customer can read
- cut-file generation with real `Cut` / `Regmark` spot colorants, registration marks measured in the
  cutter's frame, and the artwork matted outside the cut line
- print-ready output as vector PDF or flattened CMYK TIFF
- template matching against a measured library of 75 dies

## Running it

```bash
pip install -r requirements.txt
cp materials.example.json materials.json
set PREPRESS_ADMIN_TOKEN=choose-something-long     # export on Linux/macOS
python -m prepress.app                            # http://127.0.0.1:5099
```

`/` is the customer generator, `/admin` edits the materials. Without `PREPRESS_ADMIN_TOKEN` the admin
panel refuses to work at all rather than falling back to something guessable.

Materials carry the rules — bleed, safe margin, minimum resolution, roll width, colour mode, whether
the material is cut work, where the spec block sits — so a shop changes what it demands without
touching code. The panel also sets **how serious each rule is**: any of the fourteen can be silenced
or moved between info, amber and red, because that is a business judgement rather than a technical
one. And every sentence a customer reads is editable there too; the engine only ever emits a code
plus numbers.

## What is missing

No page for a customer to upload on — `/api/check` works, the UI for it does not exist yet, and
that is where the returning file should be identified by the link it arrives on rather than only by
the stamp inside it (see ROADMAP). No multi-tenancy and no submission store, though the direction for
the store is decided: short retention plus a one-time link, no backup. Custom die outlines still have
to be described by hand; only rectangles are derived automatically. Rule text is Polish for now.

Panelling geometry is deliberately absent, not missing: the tool says a job will be panelled, because
the welds show, but the strip layout belongs to the graphic team and the roll actually loaded.

## Licence

**AGPL-3.0**, and by choice rather than by inheritance — worth stating precisely, because the
in-house tool this comes from does link an AGPL renderer and this code deliberately does not. Nothing
here needs it: reportlab draws (BSD), pikepdf edits PDFs losslessly (MPL-2.0), pypdfium2 rasterises
(Apache-2.0 / BSD-3). Copyleft is here to keep the work available to the print shops it is for rather
than absorbed into a closed product.

Because the choice is free, it can be revisited in the open if a different licence would serve those
shops better.

## Credits

Built by [Commit](https://www.shyeline.work) out of a working in-house tool at a Polish large-format
print shop.

## Optional environment

| variable | what it does |
|---|---|
| `PREPRESS_ADMIN_TOKEN` | enables the admin panel; unset means the panel is disabled, not open |
| `PREPRESS_DEMO_ARTWORK` | path to a PDF whose page matches one of your templates. Its first page is rasterised and worn by the 3D flag — the marketing hero and the texture path both use it. Unset means bare cloth, which is a correct fallback. |
