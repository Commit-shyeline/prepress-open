# WordPress connector

A shop on WordPress cannot run this Python application inside PHP hosting. What it can do is link a
running instance and take the result back — that is `prepress-open-connector/`, a thin plugin (PHP +
one script) under its own licence, GPLv2 or later (its `LICENSE` says why). It ships none of the
engine.

**The loop**

1. The shop page shows the editor's button: `[prepress_design template="TOKEN"]`, or
   `prepress_open_design_url( $token )` in a theme. The link is
   `<instance>/projekt/<token>?return=<this page>`.
2. The customer designs on the issued sheet and saves; the editor offers „Wróć do sklepu z projektem"
   — only when the instance lists the shop's origin in `PREPRESS_RETURN_ORIGINS` (an allowlist: a
   `return` anyone can type would otherwise be an open redirect wearing the shop's name).
3. Back on the shop page the URL carries `?design=<held>&template=<token>&png=<picture>`;
   `connector.js` reads it, cleans the URL, keeps the design in `sessionStorage['prepress-open:design']`
   and fires `prepress-open:design` on `document` with `{ held, template, png, pdf }`. The shop's own
   script puts it in the basket or the inquiry.
4. The held design lives on the instance for 30 minutes after its last use (`/api/designs/<held>/pdf`
   is the filled template page, `/png` its picture); an order placed within that time fetches the PDF
   server side, or asks the customer to save again.

**Install:** copy `prepress-open-connector/` into `wp-content/plugins/`, activate, set the instance
address under Settings → General.
