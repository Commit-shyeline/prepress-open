=== prepress-open Connector ===
Contributors: prepress-open contributors
Tags: print, prepress, design, templates
Requires at least: 6.5
Tested up to: 6.8
Requires PHP: 8.0
Stable tag: 0.1.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Links a running prepress-open instance from your shop: a "Design it online" button per product, and the saved design handed back into your page.

== Description ==

prepress-open (https://github.com/Commit-shyeline/prepress-open) is a print-file checker and template
generator with a browser editor on the issued template. It is a Python application and runs on its own
server; this plugin does not run it inside WordPress. It knows where your instance answers, builds the
editor's link for a template token with your page as the return address, and reads the saved design's
token back when the customer returns.

1. Settings → General → "prepress-open instance": the instance's address, with the path it is mounted
   under (for example `https://check.example.com/sprawdz`).
2. On the instance, list your site in `PREPRESS_RETURN_ORIGINS` (scheme and host, comma-separated),
   or the editor will show no way back.
3. Put `[prepress_design template="TOKEN" label="Design it online"]` where the button belongs. The
   token is shown under each template in the instance's admin panel. In PHP:
   `prepress_open_design_url( $token )`.
4. When the customer returns, the page receives a `prepress-open:design` event on `document` with
   `{ held, template, png, pdf }` in `event.detail`, and the same object sits in
   `sessionStorage['prepress-open:design']`. `held` names the design on the instance for 30 minutes
   after its last use; `pdf` is where to fetch the filled template page in that time.

== Changelog ==

= 0.1.0 =
* First release: the setting, the shortcode, the hand-off reader.
