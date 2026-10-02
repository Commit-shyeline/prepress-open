# sheet-notify

Status e-mails from a Google Sheet, without a server: an Apps Script bound to
the sheet. One mail per order and per project manager when a status is
approved, and one shipping digest per day for the client's warehouse.

## Install (5 steps)

1. Open the sheet (or import `demo.csv` into a new Google Sheet). Row 1 must
   hold the headers listed in `COL` at the top of `Code.js`; their order does
   not matter.
2. Extensions → Apps Script. Delete the stub, paste `Code.js`, save.
3. Run `setup()` once from the editor toolbar and accept the authorization
   prompt. It renames the first tab to `Projekty`, creates `Log`, turns the
   `Zatwierdź maila` column into checkboxes, and installs two triggers: on edit, and
   daily at `DIGEST_HOUR` for the digest.
4. Set a row's `Stan` to `Produkcja` or `transport`, then tick `Zatwierdź maila`.
5. The PM's mail arrives within seconds, `Wysłano` fills in on every row of
   that PM's order, the checkbox unticks itself, `Log` gets a line.

## How it decides what to send

- **Nothing goes out on a status change alone.** The `Zatwierdź maila` checkbox is
  the acceptance step. Ticking it sends the mail for the row's current status
  and then clears itself. Ticking a row whose status is not one of the two
  mailing ones does nothing except leave a Polish note on the checkbox.
- **Three statuses mail the PM.** `Produkcja` (production started; carries a
  calendar entry spanning the realisation window), `transport` (shipped) and
  `Delivered` (arrived). Everything else is silent. Edit `NOTIFY` to change the
  wording or the set.
- **One tick at a time.** The whole handler runs under a script lock, so two
  people ticking in the same second cannot both pass the dedupe check. A waiting
  execution re-reads the sheet and finds the boxes already cleared.
- **Grouping is by order code + PM address + status.** Rows of one order that
  belong to the same PM *and sit in the same status* go into one mail; a
  second PM on the same order gets their own mail with only their rows; a row
  already in transport is never listed in a Produkcja mail. Rows without a
  code fall back to client + project + PM + delivery date.
- **Dedupe is per item.** The `Log`'s Items column remembers which products
  went out under which order, PM and status. Ticking again sends only items
  that have not gone yet — a row added or moved to a PM later gets its own
  mail with just that item, and nothing is ever announced twice. A row with no
  PM address gets a note on its `Stan` cell and sends nothing.
- **Bulk edits are fine.** Select several checkboxes and press Space, or
  paste TRUE into a range: one mail goes out per (order, PM, status). Sheets
  reports only the active cell when Space toggles a selection, so on any tick
  the script scans every data row for ticked boxes rather than trusting the
  event's range. A failing row leaves its tick in place with a Polish note
  explaining the error, writes an `(error: …)` line to `Log`, and the rest of
  the rows still go out.

## The `Wysłano` column: record and resend

After a send, every row of that PM's order reads `<STAN> | wysłano <date time>
→ <addresses>`, with the status exactly as it is written in the sheet, so the
cell tells you which mail went last. To send a status again — a correction, a
fixed typo — clear that cell. The script writes an invalidation marker to `Log`, forgets those
recipients, and empties `Wysłano` on the whole order. Tick `Zatwierdź maila` again
and the mail goes out afresh. Deleting an already-empty cell does nothing.

## The warehouse digest

The address in `E-mail magazyn` never receives per-order mails. Once a day at
`DIGEST_HOUR`, `sendDailyDigest` collects every row whose **`Data maila do
magazynu`** is today — regardless of `Stan` or of any mail — and sends each
warehouse address one table: item, size, PM, PM e-mail, producer, delivery
date. A row without that date falls back to `Data wysyłki z Polski`.

The warehouse has its own date because it is told days before the goods
leave, so it can prepare: ship Friday, tell the warehouse Wednesday, deliver
Monday. The digest is idempotent per day and address.
To test without waiting, run `sendDigestNow()` from the editor; it ignores
the "already sent today" check.

## Keeping the code out of the sheet people edit

Set `TARGET_SPREADSHEET_ID` to the ID of another spreadsheet the script owner
can edit and run `setup()` again. The edit trigger and the digest then watch
that sheet: its editors tick checkboxes there, `Wysłano` and `Log` are written
there, and the code stays in the spreadsheet it is bound to, which they need
not be able to open. Mails still leave from the script owner's account. Leave
`''` to run on the bound sheet itself. `setup()` removes the project's old
triggers, so the previously watched sheet stops reacting.

Note: File → Make a copy in Sheets copies a bound script along with the
sheet. A "no-code" sheet made that way has a copy of the code in its own
Apps Script project until you delete it there.

## Trial week and sender

- `TEST_RECIPIENT`: set it to one mailbox and every mail — per-order and
  digest — goes there instead, with the real addressee in the subject as
  `[TEST → a@x, b@x]`. `Log` still records the real addressee, so clearing
  the constant later does not resend anything. Set it back to `''` to go live.
- `SENDER_ALIAS`: a "send as" address verified in Gmail settings, so mails
  leave from the company domain rather than the account's own address. Leave
  `''` to use the account's address.
- Mails are sent through Gmail (`GmailApp`), so copies land in Sent.

## The `Log` tab

One line per event, including the ones that send nothing: sent; no address on
the row; everyone already had this status (tick consumed, no mail); tick on a
status that does not mail; a box unticked by hand; resend requested; digest
sent; error. Columns: when, group (order code + PM), status, recipients, row
count, a link to the order's rows, and a plain-Polish comment saying what
happened and, on a send, which mail went to how many addresses and items.
No-action lines carry a marker instead of an address in Recipients, so they
never count towards dedupe. **Newest line on top**:
each event is inserted at row 2 and pushes history down. Dedupe reads the Log
sorted by time, so the order of rows never matters. Colours: a resend request
is amber, the earlier sends it voids turn grey and struck through (they no
longer count towards dedupe, and their comment gets the moment it happened),
errors are light red, no-action lines are pale blue-grey, sends are plain. `setup()` rewrites the
header line, so an older Log picks up new columns on the next run.

The row link is a snapshot of row numbers at send time; inserting rows above
shifts it. The order code in the Group column is the durable reference.

## Dates and time zones

Every date in a mail, and the calendar entry, is formatted in the
**spreadsheet's** time zone (File → Settings). The script never uses its own
project zone for dates, so a mismatch cannot shift a delivery to the day
before. Set the spreadsheet's zone to the team's and `Log` timestamps read
naturally too.

## Adjusting

- Column names: edit the strings in `COL`. Matching is by header text,
  case-insensitive.
- Which statuses send, and their wording: `NOTIFY`.
- Digest hour: `DIGEST_HOUR`. Sender display name: `COMPANY_NAME`.

## Check

```bash
node sheet-notify/test_render.js
```

Runs grouping, recipients, dedupe, redirect, both templates, the calendar
range and the digest bucketing against `demo.csv` without Google. Everything
in `demo.csv` is invented.
