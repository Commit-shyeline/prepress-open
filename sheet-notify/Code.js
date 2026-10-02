/**
 * Sheet Notify — status e-mails from a Google Sheet, one per order and per
 * project manager, plus one shipping digest per day for the client's warehouse.
 *
 * Install: paste into Extensions → Apps Script, save, run setup() once. setup()
 * turns the "Zatwierdź" column into checkboxes and installs two triggers: an
 * edit trigger (installable — simple triggers cannot send mail) and a daily
 * time trigger for the digest.
 *
 * Rules, agreed with the customer on 2026-09-17:
 *  - A status change alone sends nothing. Ticking "Zatwierdź" on any row of an
 *    order sends the mail for that row's current status, then unticks itself.
 *  - Three statuses mail the PM: Produkcja (with a calendar entry spanning the
 *    realisation window, from "Dni przed" to "Dni po" around the delivery
 *    date), transport and Delivered. Every other status is silent.
 *  - Rows are grouped by order code + PM address, so each PM receives only
 *    their own items. The warehouse address gets nothing per order; it gets
 *    one digest per day listing the rows whose "Data maila do magazynu" is
 *    that day, usually a few days before they ship.
 *  - "Wysłano" records who received what. Clearing it forgets those
 *    recipients; tick "Zatwierdź" again to send again.
 *  - TEST_RECIPIENT, when set, redirects every mail there with the real
 *    addressee in the subject. The Log still records the real addressee, so
 *    switching the redirect off later does not resend anything.
 *
 * Dates are formatted in the SPREADSHEET's time zone, never the script
 * project's, so a mismatch between the two cannot shift a day.
 */

const SHEET_NAME = 'Projekty';
const LOG_SHEET_NAME = 'Log';
const COMPANY_NAME = 'Ambient Hub';
const DATE_FMT = 'dd.MM.yyyy';
const DIGEST_HOUR = 17;      // local hour at which the warehouse digest goes out
const SENDER_ALIAS = '';     // a verified "send as" address in Gmail, or '' for the account's own
const TEST_RECIPIENT = '';   // e.g. 'commit.shyeline@gmail.com' during the trial week; '' = live
const SEND_PAUSE_MS = 1200;  // Gmail throttles bursts from consumer accounts; breathe between sends
// '' = the spreadsheet this script is bound to. An ID = another spreadsheet the
// script owner can edit: the triggers watch THAT sheet, people click there, and
// they never see this code because it lives here, not in their file.
// Demo phase: the code sits in "PROJEKTY DEMO v2", the customer clicks in
// "PROJEKTY DEMO - dla klienta". Set back to '' when installing in his own sheet.
const TARGET_SPREADSHEET_ID = '1SwQfcNma-C1dd192Sz1-c2i642aaQtCbFQB_3b5tK0U';

// Row-1 header text → role. Edit the strings if the sheet's headers differ;
// nothing else in the file depends on column positions.
const COL = {
  deadline:       'MAX DEADLINE KLIENTA',
  delivery:       'Data dostawy do klienta',
  shipped:        'Data wysyłki z Polski',
  warehouseDate:  'Data maila do magazynu',
  produced:       'Data ukończenia produkcji',
  confirmed:      'Data potwierdzenia',
  status:         'Stan',
  approve:        'Zatwierdź maila',
  sentAt:         'Wysłano',
  client:         'Klient',
  pm:             'PM',
  pmEmail:        'E-mail PM',
  warehouseEmail: 'E-mail magazyn',
  project:        'Projekt',
  orderCode:      'Nr zlecenia',
  product:        'Produkt',
  producer:       'Producent',
  carrier:        'Przewoźnik dostawy',
  address:        'Adres dostawy',
  size:           'Wymiar i waga (szacunek)',
  tracking:       'Numer śledzenia',
  offer:          'Link do oferty',
  daysBefore:     'Dni przed',
  daysAfter:      'Dni po',
};

// The statuses that mail the PM. Anything else stays silent.
const NOTIFY = {
  'Produkcja': {
    subject: 'Production has started',
    intro: 'Your project has been confirmed and production is under way. The attached calendar entry covers the planned realisation window.',
  },
  'transport': {
    subject: 'Your order is on its way',
    intro: 'Your order has left production and is now in transport.',
  },
  'Delivered': {
    subject: 'Your order has been delivered',
    intro: 'Your order has been delivered. Thank you for working with us.',
  },
};

// Notes shown in the sheet are Polish — his team reads the sheet, clients read the mails.
const NO_EMAIL_NOTE = 'Brak adresu e-mail w tym wierszu — nic nie wysłano. '
  + 'Wpisz adres w „E-mail PM" i zaznacz „Zatwierdź" ponownie.';
const NO_EMAIL_LOG = '(no e-mail on row — nothing sent)';
const INVALIDATED_LOG = '(invalidated — resend requested)';
const DIGEST_KEY = 'DIGEST';
const LOG_HEADER = ['Sent at', 'Group', 'Stan', 'Recipients', 'Rows', 'Rows link', 'Comment', 'Items'];
const ITEMS_SEP = ' | ';
// Log row colours: the resend marker, the sends it voids, and errors.
const LOG_BG_INVALIDATED = '#fff3cd';
const LOG_BG_VOIDED = '#e5e7eb';
const LOG_BG_ERROR = '#fde2e1';
const LOG_BG_NOACTION = '#eef2f7';
const NOACTION_DEDUPE = '(no action — everyone already has this status)';
const NOACTION_SILENT = '(no action — status does not mail)';
const NOACTION_UNTICKED = '(unticked by hand)';
const LOCK_WAIT_MS = 60000;

// ---------------------------------------------------------------- Apps Script

// The spreadsheet the script operates on — see TARGET_SPREADSHEET_ID.
function target_() {
  return TARGET_SPREADSHEET_ID ? SpreadsheetApp.openById(TARGET_SPREADSHEET_ID) : SpreadsheetApp.getActive();
}

function setup() {
  const ss = target_();
  if (!ss.getSheetByName(SHEET_NAME)) ss.getSheets()[0].setName(SHEET_NAME);
  const sheet = ss.getSheetByName(SHEET_NAME);
  const idx = columnIndex_(sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0]);
  if (idx.approve >= 0 && sheet.getMaxRows() > 1) {
    sheet.getRange(2, idx.approve + 1, sheet.getMaxRows() - 1).insertCheckboxes();
  }
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('onSheetEdit').forSpreadsheet(ss).onEdit().create();
  ScriptApp.newTrigger('sendDailyDigest').timeBased().everyDays(1).atHour(DIGEST_HOUR).create();
  logSheet_(ss);
}

// Who made the edit being processed, when Sheets tells us (same Workspace domain
// or the trigger owner; consumer accounts often yield ''). Appended to Log comments.
let CURRENT_EDITOR = '';

// Fires on edits to "Zatwierdź maila" and "Wysłano". For ticks it scans EVERY
// data row, not just the event's range: pressing Space on a multi-cell
// selection toggles all the checkboxes but Sheets reports only the active cell.
// Ticks are consumed as they are processed, so no stale TRUE survives a scan.
function onSheetEdit(e) {
  const sheet = e.range.getSheet();
  if (sheet.getName() !== SHEET_NAME) return;
  try { CURRENT_EDITOR = (e.user && e.user.getEmail()) || ''; } catch (_) { CURRENT_EDITOR = ''; }

  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const idx = columnIndex_(headers);
  const c1 = e.range.getColumn(), c2 = e.range.getLastColumn();
  const touched = role => idx[role] >= 0 && idx[role] + 1 >= c1 && idx[role] + 1 <= c2;
  if (!touched('approve') && !touched('sentAt')) return;

  // Clearing "Wysłano" asks to forget the recipients — but deleting an already-empty cell does not.
  const singleCell = e.range.getNumRows() === 1 && e.range.getNumColumns() === 1;
  const clearedSent = touched('sentAt') && e.value === undefined && !(singleCell && !e.oldValue);

  // One tick at a time. Ticking three boxes in the same second used to start three
  // executions that each read the Log before any of them wrote to it, so dedupe let
  // all three send the same mail (seen live 2026-09-21, 21:06). Everything that
  // reads or writes the sheet and the Log now happens inside this lock; because the
  // holder scans every row, an execution that waited finds the boxes already
  // cleared and has nothing left to do.
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(LOCK_WAIT_MS)) return;
  try {
    handleTicks_(e, sheet, idx, touched, singleCell, clearedSent);
  } finally {
    lock.releaseLock();
  }
}

// Runs under the script lock. Reads the sheet AFTER the lock is held, so it sees
// whatever the previous holder wrote.
function handleTicks_(e, sheet, idx, touched, singleCell, clearedSent) {
  const lastSheetRow = sheet.getLastRow();
  if (lastSheetRow < 2) return;
  const firstRow = touched('approve') ? 2 : Math.max(e.range.getRow(), 2);
  const lastRow = touched('approve') ? lastSheetRow : Math.min(e.range.getLastRow(), lastSheetRow);
  if (lastRow < firstRow) return;

  const ss = e.source || target_();   // the spreadsheet that was edited, which may not be this one
  const rows = sheet.getRange(2, 1, lastSheetRow - 1, sheet.getLastColumn()).getValues();
  const log = logSheet_(ss);
  const tz = ss.getSpreadsheetTimeZone();

  // A person unticking a box by hand is worth a line too — only detectable for a
  // single cell, where Sheets hands us the old value.
  if (touched('approve') && singleCell && String(e.oldValue).toUpperCase() === 'TRUE' && !isTicked(e.value)) {
    const row = rows[e.range.getRow() - 2];
    if (row && text_(row, idx, 'product')) {
      const groupRows = rowNumbersInGroup(rows, row, idx);
      logLine_(log, [new Date(), groupKey(row, idx), notifyKey_(text_(row, idx, 'status')) || text_(row, idx, 'status'),
        NOACTION_UNTICKED, groupRows.length, rowsLink_(sheet, groupRows),
        'Ptaszek odznaczony ręcznie — nic nie wysłano.'], LOG_BG_NOACTION);
    }
  }

  const done = new Set();
  for (let r = firstRow; r <= lastRow; r++) {
    const row = rows[r - 2];
    const ticked = touched('approve') && isTicked(cell_(row, idx, 'approve'));
    const cleared = clearedSent && !text_(row, idx, 'sentAt');
    if (!ticked && !cleared) continue;   // the common case — no grouping, no API calls

    const status = notifyKey_(text_(row, idx, 'status'));
    const key = groupKey(row, idx);
    // Only rows of the same order, PM and status travel together: a Produkcja mail
    // never lists an item that is already in transport.
    const groupRows = rowNumbersInGroup(rows, row, idx, status);
    const link = rowsLink_(sheet, groupRows);

    if (cleared && status && !done.has('clear' + key)) {
      done.add('clear' + key);
      const stamp = Utilities.formatDate(new Date(), tz, DATE_FMT + ' HH:mm');
      voidLogRows_(log, key, status, stamp);
      logLine_(log, [new Date(), key, status, INVALIDATED_LOG, 0, link,
        `Wyczyszczono „Wysłano" — wcześniejsze wysyłki statusu „${text_(row, idx, 'status')}" przekreślone i nie blokują. `
        + 'Zaznacz „Zatwierdź maila", aby wysłać ponownie.'], LOG_BG_INVALIDATED);
      writeColumn_(sheet, idx, 'sentAt', groupRows, '');
    }

    if (!ticked) continue;
    const pair = key + '\u0000' + status;
    if (done.has(pair)) continue;
    done.add(pair);

    if (!status) {
      writeColumn_(sheet, idx, 'approve', groupRows, false);
      const typed = text_(row, idx, 'status');
      sheet.getRange(r, idx.approve + 1).setNote(`Status „${typed}" nie wysyła maili. `
        + `Maile idą przy: ${Object.keys(NOTIFY).map(k => `„${k}"`).join(', ')}.`);
      logLine_(log, [new Date(), key, typed || '(pusty)', NOACTION_SILENT, groupRows.length, link,
        `Zaznaczono przy statusie „${typed}" — ten status nie wysyła maili. Ptaszek zgaszony.`], LOG_BG_NOACTION);
      continue;
    }
    try {
      sheet.getRange(r, idx.approve + 1).setNote('');
      const outcome = sendGroup_(sheet, r, groupRows, rows, idx, key, status, log, tz, link);
      writeColumn_(sheet, idx, 'approve', groupRows, false);
      if (outcome === 'dedupe') {
        logLine_(log, [new Date(), key, status, NOACTION_DEDUPE, groupRows.length, link,
          `Zaznaczono — nic nie wysłano: każdy adres w „E-mail PM" ma już mail o statusie „${text_(row, idx, 'status')}" `
          + 'dla tego zlecenia. Aby wysłać ponownie, wyczyść „Wysłano" i zaznacz jeszcze raz.'], LOG_BG_NOACTION);
      }
      if (outcome === 'sent') Utilities.sleep(SEND_PAUSE_MS);
    } catch (err) {
      // One failing row must not abort the rest of a bulk tick. The tick stays so a
      // human sees it, the cell explains why, and the Log keeps the error.
      const message = String((err && err.message) || err);
      sheet.getRange(r, idx.approve + 1).setNote('Błąd wysyłki: ' + message + '\nPopraw przyczynę i zaznacz ponownie.');
      logLine_(log, [new Date(), key, status, '(error: ' + message + ')', groupRows.length, link,
        'Błąd wysyłki — nic nie wyszło, ptaszek został. ' + message], LOG_BG_ERROR);
    }
  }
}

// Sends the mail for one (order, PM, status) group. The unit of dedupe is the
// ITEM: only rows whose product has not yet gone out under this key and status
// are listed, so a moved row, a late row or a shared mailbox each get exactly
// the items they have not seen. Returns 'sent' | 'dedupe' | 'no-email'.
function sendGroup_(sheet, rowNumber, groupRows, rows, idx, key, status, log, tz, link) {
  const row = rows[rowNumber - 2];
  const statusCell = sheet.getRange(rowNumber, idx.status + 1);
  const entries = logEntries_(log, key, status);
  const logged = entries.map(x => x.recipients);

  const recipients = recipientsOf(row, idx);
  if (!recipients.length) {
    statusCell.setNote(NO_EMAIL_NOTE);
    if (!logged.includes(NO_EMAIL_LOG)) {
      logLine_(log, [new Date(), key, status, NO_EMAIL_LOG, groupRows.length, link,
        'Brak adresu w „E-mail PM" — nic nie wysłano. Wpisz adres i zaznacz „Zatwierdź maila".']);
    }
    return 'no-email';
  }

  const alreadySent = sentItemSet(entries);
  const pendingRows = groupRows.filter(n => !alreadySent.has(itemId(rows[n - 2], idx)));
  if (!pendingRows.length) return 'dedupe';
  const group = pendingRows.map(n => rows[n - 2]);
  link = rowsLink_(sheet, pendingRows);

  const fmtDate = d => (d instanceof Date ? Utilities.formatDate(d, tz, DATE_FMT) : String(d || ''));

  // Produkcja carries the realisation window — "Dni przed" … "Dni po" around the
  // delivery date — as a "Lead time" line, an inline calendar and an .ics.
  let leadTime = null;
  const delivery = cell_(row, idx, 'delivery');
  if (status === 'Produkcja' && delivery instanceof Date) {
    const day = Utilities.formatDate(delivery, tz, 'yyyyMMdd');
    leadTime = {
      start: shiftYmd(day, -num_(cell_(row, idx, 'daysBefore'))),
      end: shiftYmd(day, num_(cell_(row, idx, 'daysAfter'))),
      today: Utilities.formatDate(new Date(), tz, 'yyyyMMdd'),
    };
  }
  const mail = renderMail(group, idx, status, fmtDate, leadTime);

  const attachments = [];
  if (leadTime) {
    const ics = buildIcs(mail.subject, leadTime.start, leadTime.end, mail.text, key + '|' + status);
    attachments.push(Utilities.newBlob(ics, 'text/calendar', 'realisation.ics'));
  }

  deliver_(recipients, mail.subject, mail.text, mail.html, attachments);
  statusCell.setNote('');
  const items = group.map(r => text_(r, idx, 'product'));
  logLine_(log, [new Date(), key, status, recipients.join(', '), group.length, link,
    `Wysłano „${mail.subject}" → ${plural_(recipients.length, 'adres', 'adresy', 'adresów')}, `
    + `${plural_(group.length, 'pozycja', 'pozycje', 'pozycji')}${attachments.length ? ', z kalendarzem' : ''}.`,
    items.join(ITEMS_SEP)]);

  if (idx.sentAt >= 0) {
    const when = Utilities.formatDate(new Date(), tz, DATE_FMT + ' HH:mm');
    const stateAsTyped = text_(row, idx, 'status');
    writeColumn_(sheet, idx, 'sentAt', pendingRows, `${stateAsTyped} | wysłano ${when} → ${recipients.join(', ')}`);
  }
  return 'sent';
}

// One mail per warehouse address listing the rows it is told about today (warehouseDay).
// Runs from the daily trigger; idempotent per day and address.
function sendDailyDigest() { digest_(new Date(), false); }

// Same, but ignores "already sent today" — run this from the editor to test.
function sendDigestNow() { digest_(new Date(), true); }

function digest_(now, force) {
  // Same critical section as the ticks: the digest reads the Log to see whether it
  // already ran today and writes its own line to it.
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(LOCK_WAIT_MS)) return;
  try {
    digestLocked_(now, force);
  } finally {
    lock.releaseLock();
  }
}

function digestLocked_(now, force) {
  const ss = target_();
  const sheet = ss.getSheetByName(SHEET_NAME);
  const log = logSheet_(ss);
  const tz = ss.getSpreadsheetTimeZone();
  const today = Utilities.formatDate(now, tz, 'yyyyMMdd');
  const dayOf = d => (d instanceof Date ? Utilities.formatDate(d, tz, 'yyyyMMdd') : '');

  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const idx = columnIndex_(headers);
  if (sheet.getLastRow() < 2) return;
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, headers.length).getValues();

  const logRows = log.getLastRow() < 2 ? [] : log.getRange(2, 1, log.getLastRow() - 1, 4).getValues();
  const alreadyDigested = new Set(logRows
    .filter(([, key, st]) => key === DIGEST_KEY && String(st) === today)
    .map(([, , , rec]) => String(rec).toLowerCase()));

  const fmtDate = d => (d instanceof Date ? Utilities.formatDate(d, tz, DATE_FMT) : String(d || ''));
  const dateLabel = Utilities.formatDate(now, tz, DATE_FMT);

  const shippedToday = row => warehouseDay(row, idx, dayOf) === today;

  groupForDigest(rows, idx, shippedToday).forEach((items, address) => {
    if (!force && alreadyDigested.has(address)) return;
    const mail = renderDigest(items, idx, fmtDate, dateLabel);
    deliver_([address], mail.subject, mail.text, mail.html, []);
    logLine_(log, [new Date(), DIGEST_KEY, today, address, items.length, '',
      `Raport dzienny do magazynu z ${dateLabel}: ${plural_(items.length, 'pozycja', 'pozycje', 'pozycji')}.`]);
  });
}

// Sends through Gmail so a copy lands in Sent. Honours the test redirect and the sender alias.
function deliver_(recipients, subject, text, html, attachments) {
  const addressed = addressFor(recipients, subject, TEST_RECIPIENT);
  const options = { htmlBody: html, name: COMPANY_NAME, attachments };
  if (SENDER_ALIAS) options.from = SENDER_ALIAS;
  GmailApp.sendEmail(addressed.to, addressed.subject, text, options);
}

function writeColumn_(sheet, idx, role, rowNumbers, value) {
  if (idx[role] < 0) return;
  rowNumbers.forEach(n => sheet.getRange(n, idx[role] + 1).setValue(value));
}

function logSheet_(ss) {
  let log = ss.getSheetByName(LOG_SHEET_NAME);
  if (!log) {
    log = ss.insertSheet(LOG_SHEET_NAME);
    log.setFrozenRows(1);
  }
  // Rewrites the header on every call so an older Log picks up new columns.
  log.getRange(1, 1, 1, LOG_HEADER.length).setValues([LOG_HEADER]);
  return log;
}

// Newest first: a new line goes in at row 2 and pushes history down.
function logLine_(log, values, background) {
  const commentIndex = LOG_HEADER.indexOf('Comment');
  if (CURRENT_EDITOR && values.length > commentIndex) {
    values = values.slice();
    values[commentIndex] = `${values[commentIndex] || ''} [${CURRENT_EDITOR}]`.trim();
  }
  log.insertRowBefore(2);
  const line = log.getRange(2, 1, 1, values.length);
  line.setValues([values]);
  line.setBackground(background || null).setFontLine('none');
}

// Greys out and strikes through every earlier Log line for this (group, status):
// after a resend request they no longer count towards dedupe. The comment gets
// the moment it happened.
function voidLogRows_(log, key, status, stamp) {
  if (log.getLastRow() < 2) return;
  const commentCol = LOG_HEADER.indexOf('Comment') + 1;
  const cells = log.getRange(2, 2, log.getLastRow() - 1, 2).getValues();
  cells.forEach(([k, s], i) => {
    if (k !== key || s !== status) return;
    const rowNumber = i + 2;
    log.getRange(rowNumber, 1, 1, LOG_HEADER.length).setBackground(LOG_BG_VOIDED).setFontLine('line-through');
    const comment = log.getRange(rowNumber, commentCol);
    comment.setValue(`${String(comment.getValue() || '').trim()} · unieważniono ${stamp}`.replace(/^ · /, ''));
  });
}

// "1 adres", "2 adresy", "5 adresów" — Polish plural for a count.
function plural_(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  const form = n === 1 ? one : (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) ? few : many;
  return `${n} ${form}`;
}

// Every Log line for this (group, status) since the last resend request, as
// { recipients, items }, oldest first. Sorted by time, not by row position, so
// it does not matter whether lines were appended or prepended.
function logEntries_(log, key, status) {
  if (log.getLastRow() < 2) return [];
  const itemsCol = LOG_HEADER.indexOf('Items');
  const all = log.getRange(2, 1, log.getLastRow() - 1, LOG_HEADER.length).getValues()
    .filter(([, k, s]) => k === key && s === status)
    .sort((a, b) => (a[0] instanceof Date ? a[0].getTime() : 0) - (b[0] instanceof Date ? b[0].getTime() : 0))
    .map(line => ({ recipients: String(line[3] || ''), items: String(line[itemsCol] || '') }));
  return afterLastInvalidation(all, x => x.recipients);
}

// A clickable link to the order's rows in the data tab, as they stood at send time.
// Semicolon as the argument separator: it is accepted in every Sheets locale,
// a comma breaks with #ERROR! in Polish and other comma-decimal locales.
function rowsLink_(sheet, groupRows) {
  const { range, label } = rowsRangeLabel(groupRows);
  return `=HYPERLINK("#gid=${sheet.getSheetId()}&range=${range}";"${label}")`;
}

// ---------------------------------------------------------------- Pure logic
// Everything below takes plain arrays/strings and runs in Node for the test.

function columnIndex_(headers) {
  const norm = h => String(h || '').trim().toLowerCase();
  const pos = headers.map(norm);
  const idx = {};
  for (const role in COL) idx[role] = pos.indexOf(norm(COL[role]));
  const required = ['status', 'client', 'project', 'pm', 'product'];
  const missing = required.filter(r => idx[r] < 0).map(r => COL[r]);
  if (missing.length) throw new Error('Missing header(s): ' + missing.join(', '));
  return idx;
}

// "Transport", "transport " and "TRANSPORT" all mean the same status.
function notifyKey_(status) {
  const wanted = String(status || '').trim().toLowerCase();
  return Object.keys(NOTIFY).find(k => k.toLowerCase() === wanted) || '';
}

function cell_(row, idx, role) {
  const i = idx[role];
  return i >= 0 ? row[i] : '';
}

function text_(row, idx, role) {
  return String(cell_(row, idx, role) || '').trim();
}

function num_(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function isTicked(v) {
  return v === true || String(v).trim().toUpperCase() === 'TRUE';
}

function ymd_(d) {
  if (!(d instanceof Date)) return String(d || '');
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

// 'yyyyMMdd' ± days, computed in UTC so no zone can shift it.
function shiftYmd(ymd, days) {
  const y = +ymd.slice(0, 4), m = +ymd.slice(4, 6), d = +ymd.slice(6, 8);
  const t = new Date(Date.UTC(y, m - 1, d) + days * 86400000);
  const p = n => String(n).padStart(2, '0');
  return `${t.getUTCFullYear()}${p(t.getUTCMonth() + 1)}${p(t.getUTCDate())}`;
}

// Order code + PM identity: each PM on an order is their own group. Rows
// without a code fall back to client + project + PM + planned delivery.
function groupKey(row, idx) {
  const pm = (text_(row, idx, 'pmEmail') || text_(row, idx, 'pm')).toLowerCase();
  const code = text_(row, idx, 'orderCode').toUpperCase();
  if (code) return `${code}|${pm}`;
  return [text_(row, idx, 'client'), text_(row, idx, 'project'), pm, ymd_(cell_(row, idx, 'delivery'))].join('|');
}

// Sheet row numbers (1-based, header is row 1) of every product row in the same
// group. With `status` given, only rows currently in that notify status.
function rowNumbersInGroup(rows, row, idx, status) {
  const key = groupKey(row, idx);
  const numbers = [];
  rows.forEach((r, i) => {
    if (!text_(r, idx, 'product') || groupKey(r, idx) !== key) return;
    if (status !== undefined && notifyKey_(text_(r, idx, 'status')) !== status) return;
    numbers.push(i + 2);
  });
  return numbers;
}

function rowsInGroup(rows, row, idx, status) {
  return rowNumbersInGroup(rows, row, idx, status).map(n => rows[n - 2]);
}

// What makes two rows "the same item" for dedupe: the product text.
function itemId(row, idx) {
  return text_(row, idx, 'product').toLowerCase();
}

// The day the warehouse is told about a row. It is told on its OWN date, usually
// a few days before the goods leave, so it can prepare for the delivery
// (customer, 2026-09-21: ship Friday → warehouse told Wednesday → delivery
// Monday). A row with no such date falls back to the ship date.
function warehouseDay(row, idx, dayOf) {
  return dayOf(cell_(row, idx, 'warehouseDate')) || dayOf(cell_(row, idx, 'shipped'));
}

// A1 row range and a human label for a set of sheet row numbers.
function rowsRangeLabel(groupRows) {
  const first = Math.min(...groupRows), last = Math.max(...groupRows);
  return first === last
    ? { range: `${first}:${first}`, label: `row ${first}` }
    : { range: `${first}:${last}`, label: `rows ${first}–${last}` };
}

// Every address in a free-text cell, deduplicated, junk dropped.
function addressesIn(text) {
  const seen = new Set();
  return String(text || '').split(/[\s,;\/]+/)
    .filter(s => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s))
    .filter(s => !seen.has(s.toLowerCase()) && seen.add(s.toLowerCase()));
}

// Per-order mails go to the PM only; the warehouse gets the daily digest.
function recipientsOf(row, idx) {
  return addressesIn(text_(row, idx, 'pmEmail'));
}

// Log entries after the most recent "(invalidated…)" marker; everything before
// it was forgotten when someone cleared "Wysłano". `text` extracts the
// Recipients string from an entry (identity for plain strings).
function afterLastInvalidation(entries, text = s => s) {
  let last = -1;
  entries.forEach((e, i) => { if (text(e) === INVALIDATED_LOG) last = i; });
  return last < 0 ? entries : entries.slice(last + 1);
}

// Item ids that already went out in real sends (a Recipients string holding an
// address), read from the Items column of those Log lines.
function sentItemSet(entries) {
  const sent = new Set();
  entries.forEach(({ recipients, items }) => {
    if (!String(recipients).includes('@')) return;
    String(items || '').split(ITEMS_SEP).map(s => s.trim().toLowerCase()).filter(Boolean).forEach(s => sent.add(s));
  });
  return sent;
}

// Test redirect: everything to one mailbox, real addressee kept in the subject.
function addressFor(recipients, subject, testRecipient) {
  if (!testRecipient) return { to: recipients.join(','), subject };
  return { to: testRecipient, subject: `[TEST → ${recipients.join(', ')}] ${subject}` };
}

function esc_(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// leadTime = { start, end, today } as 'yyyyMMdd', only for the Produkcja mail:
// it replaces "Planned delivery" with a "Lead time" range and adds the calendar.
function renderMail(group, idx, status, fmtDate, leadTime) {
  const first = group[0];
  const n = NOTIFY[status];
  const pm = text_(first, idx, 'pm').split('//')[0].trim();
  const project = text_(first, idx, 'project');
  const client = text_(first, idx, 'client');

  const facts = [
    ['Project', project],
    ['Client', client],
    ['Confirmed on', fmtDate(cell_(first, idx, 'confirmed'))],
    leadTime
      ? ['Lead time', `${ymdToDmy(leadTime.start)} – ${ymdToDmy(leadTime.end)}`]
      : ['Planned delivery', fmtDate(cell_(first, idx, 'delivery'))],
    ['Shipping', [text_(first, idx, 'carrier'), text_(first, idx, 'tracking') && 'tracking ' + text_(first, idx, 'tracking')]
      .filter(Boolean).join(', ')],
    ['Delivery address', text_(first, idx, 'address')],
  ].filter(([, v]) => v);

  const offer = text_(first, idx, 'offer');
  const items = group.map(r => [text_(r, idx, 'product'), text_(r, idx, 'size')]);

  const subject = `${n.subject} – ${project}`;

  const calendar = leadTime ? `
  <p style="margin:20px 0 4px;font:600 11px/1 Arial,sans-serif;color:${CAL.brand};text-transform:uppercase;letter-spacing:.06em">Lead time</p>
  ${buildCalendarHtml(leadTime.today, leadTime.start, leadTime.end)}` : '';

  const html = `
<div style="font:15px/1.5 Arial,sans-serif;color:#222;max-width:640px">
  <p>Dear ${esc_(pm || client)},</p>
  <p>${esc_(n.intro)}</p>
  <table cellpadding="6" style="border-collapse:collapse">
    ${facts.map(([k, v]) => `<tr><td style="color:#666;padding-right:16px">${esc_(k)}</td><td><b>${esc_(v)}</b></td></tr>`).join('')}
    ${offer ? `<tr><td style="color:#666">Offer</td><td><a href="${esc_(offer)}">${esc_(offer)}</a></td></tr>` : ''}
  </table>${calendar}
  <h3 style="margin:20px 0 8px">Items</h3>
  <table cellpadding="6" style="border-collapse:collapse;border:1px solid #ddd">
    <tr style="background:#f3f3f3"><th align="left">Item</th><th align="left">Size / weight</th></tr>
    ${items.map(([p, s]) => `<tr><td style="border-top:1px solid #eee">${esc_(p)}</td><td style="border-top:1px solid #eee">${esc_(s)}</td></tr>`).join('')}
  </table>
  <p style="margin-top:24px">Best regards,<br>${esc_(COMPANY_NAME)}</p>
</div>`;

  const text = [
    `Dear ${pm || client},`, '', n.intro, '',
    ...facts.map(([k, v]) => `${k}: ${v}`),
    offer ? `Offer: ${offer}` : '',
    '', 'Items:',
    ...items.map(([p, s]) => ` - ${p}${s ? ' (' + s + ')' : ''}`),
    '', 'Best regards,', COMPANY_NAME,
  ].filter((l, i, a) => !(l === '' && a[i - 1] === '')).join('\n');

  return { subject, html, text };
}

// Rows for which shippedToday(row) holds, bucketed by warehouse address. A row
// with two warehouse addresses lands in both.
function groupForDigest(rows, idx, shippedToday) {
  const byAddress = new Map();
  rows.forEach(row => {
    if (!text_(row, idx, 'product') || !shippedToday(row)) return;
    addressesIn(text_(row, idx, 'warehouseEmail')).forEach(a => {
      const k = a.toLowerCase();
      if (!byAddress.has(k)) byAddress.set(k, []);
      byAddress.get(k).push(row);
    });
  });
  return byAddress;
}

function renderDigest(items, idx, fmtDate, dateLabel) {
  const cols = [
    ['Item', r => text_(r, idx, 'product')],
    ['Size / weight', r => text_(r, idx, 'size')],
    ['PM', r => text_(r, idx, 'pm').split('//')[0].trim()],
    ['PM e-mail', r => addressesIn(text_(r, idx, 'pmEmail')).join(', ')],
    ['Producer', r => text_(r, idx, 'producer')],
    ['Delivery', r => fmtDate(cell_(r, idx, 'delivery'))],
  ];
  // Sent on "Data maila do magazynu", days before dispatch, so it must not claim the goods have left.
  const subject = `Upcoming deliveries – ${dateLabel}`;
  const intro = 'The following items are scheduled for delivery to your warehouse. Please expect them on the delivery dates listed.';

  const html = `
<div style="font:15px/1.5 Arial,sans-serif;color:#222;max-width:800px">
  <p>Hello,</p>
  <p>${esc_(intro)}</p>
  <table cellpadding="6" style="border-collapse:collapse;border:1px solid #ddd">
    <tr style="background:#f3f3f3">${cols.map(([h]) => `<th align="left">${esc_(h)}</th>`).join('')}</tr>
    ${items.map(r => `<tr>${cols.map(([, f]) => `<td style="border-top:1px solid #eee">${esc_(f(r))}</td>`).join('')}</tr>`).join('')}
  </table>
  <p style="margin-top:24px">Best regards,<br>${esc_(COMPANY_NAME)}</p>
</div>`;

  const text = [
    'Hello,', '', intro, '',
    ...items.map(r => ' - ' + cols.map(([h, f]) => `${h}: ${f(r)}`).join(' | ')),
    '', 'Best regards,', COMPANY_NAME,
  ].join('\n');

  return { subject, html, text };
}

// ------------------------------------------------------------ Inline calendar
// Month grids showing today, the lead-time window on working days, Polish public
// holidays (production is in Poland) and weekends. Saturated fills plus borders
// survive Gmail's dark-mode recolouring. All date maths is on 'yyyyMMdd' strings
// and UTC, so no zone can shift a day.
// Palette: Ambient Hub sky blue (#54b9f0, from the logo) for the window, a deep
// blue for today; holidays stay red because that is meaning, not branding.

const CAL = {
  // Saturated brand blue, not a pastel tint: Gmail's dark mode inverts pastels
  // into navy and the window becomes indistinguishable from "today" (seen live).
  rangeBg: '#54b9f0', rangeFg: '#07304a', rangeBd: '#1f8fd0',
  todayBg: '#0f6fa6', todayFg: '#ffffff',
  holidayBg: '#fca5a5', holidayFg: '#3d0a0a', holidayBd: '#ef6b6b',
  weekendBg: '#cdd6e1', weekendBd: '#aab4c2', weekendFg: '#566070',
  dayFg: '#334155', headFg: '#64748b', brand: '#0f6fa6',
};
const MONTHS_EN = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS_EN = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const CAL_FONT = "'Segoe UI',Arial,sans-serif";

const pad2_ = n => String(n).padStart(2, '0');
const ymdOf_ = (y, m, d) => `${y}${pad2_(m)}${pad2_(d)}`;
const utcOf_ = ymd => new Date(Date.UTC(+ymd.slice(0, 4), +ymd.slice(4, 6) - 1, +ymd.slice(6, 8)));
const utcYmd_ = dt => ymdOf_(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate());

function ymdToDmy(ymd) {
  return `${ymd.slice(6, 8)}.${ymd.slice(4, 6)}.${ymd.slice(0, 4)}`;
}

// Easter Sunday, anonymous Gregorian (Meeus) algorithm.
function easterYmd(year) {
  const a = year % 19, b = Math.floor(year / 100), c = year % 100;
  const d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31), day = ((h + l - 7 * m + 114) % 31) + 1;
  return ymdOf_(year, month, day);
}

function polishHolidays(year) {
  const easter = utcOf_(easterYmd(year));
  const plus = days => utcYmd_(new Date(easter.getTime() + days * 86400000));
  const fixed = ['0101', '0106', '0501', '0503', '0815', '1101', '1111', '1225', '1226'].map(md => `${year}${md}`);
  return new Set([...fixed, plus(0), plus(1), plus(49), plus(60)]);
}

// 0 = Monday … 6 = Sunday.
function weekdayMon0(ymd) {
  return (utcOf_(ymd).getUTCDay() + 6) % 7;
}

function isWorkingDay(ymd, holidays) {
  return weekdayMon0(ymd) < 5 && !holidays.has(ymd);
}

// Ordered [year, month] pairs from startYmd's month through endYmd's month.
function monthsBetween(startYmd, endYmd) {
  const out = [];
  let y = +startYmd.slice(0, 4), m = +startYmd.slice(4, 6);
  const ey = +endYmd.slice(0, 4), em = +endYmd.slice(4, 6);
  while (y < ey || (y === ey && m <= em)) {
    out.push([y, m]);
    m += 1;
    if (m > 12) { m = 1; y += 1; }
  }
  return out;
}

function renderMonthHtml(year, month, todayYmd, startYmd, endYmd) {
  const holidays = polishHolidays(year);
  const head = `<tr><td colspan="7" style="padding:8px 0 4px;font:600 13px/1.4 ${CAL_FONT};color:${CAL.dayFg}">${MONTHS_EN[month]} ${year}</td></tr>`;
  const labels = WEEKDAYS_EN.map(l =>
    `<td align="center" style="padding:4px 0;font:600 10px/1 ${CAL_FONT};color:${CAL.headFg};width:14%">${l}</td>`).join('');
  const weeks = [`<tr>${labels}</tr>`];
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  let cells = Array(weekdayMon0(ymdOf_(year, month, 1))).fill('<td></td>');
  for (let d = 1; d <= daysInMonth; d++) {
    const ymd = ymdOf_(year, month, d);
    let bg = 'transparent', fg = CAL.dayFg, weight = '400', bd = 'transparent';
    if (ymd === todayYmd) { bg = CAL.todayBg; fg = CAL.todayFg; weight = '700'; bd = CAL.todayBg; }
    else if (ymd >= startYmd && ymd <= endYmd && isWorkingDay(ymd, holidays)) { bg = CAL.rangeBg; fg = CAL.rangeFg; weight = '700'; bd = CAL.rangeBd; }
    else if (holidays.has(ymd)) { bg = CAL.holidayBg; fg = CAL.holidayFg; weight = '700'; bd = CAL.holidayBd; }
    else if (weekdayMon0(ymd) >= 5) { bg = CAL.weekendBg; fg = CAL.weekendFg; bd = CAL.weekendBd; }
    cells.push(`<td align="center" style="padding:0"><div style="margin:1px auto;height:28px;line-height:24px;border-radius:6px;border:1px solid ${bd};box-sizing:border-box;background:${bg};color:${fg};font:${weight} 12px/24px ${CAL_FONT}">${d}</div></td>`);
    if (cells.length === 7) { weeks.push(`<tr>${cells.join('')}</tr>`); cells = []; }
  }
  if (cells.length) {
    while (cells.length < 7) cells.push('<td></td>');
    weeks.push(`<tr>${cells.join('')}</tr>`);
  }
  return head + weeks.join('');
}

// Month tables from today's month through the window's last month, capped at
// three without ever dropping the months that hold the window.
function buildCalendarHtml(todayYmd, startYmd, endYmd) {
  if (!startYmd || !endYmd) return '';
  const first = todayYmd < startYmd ? todayYmd : startYmd;
  const last = todayYmd > endYmd ? todayYmd : endYmd;
  let months = monthsBetween(first, last);
  if (months.length > 3) {
    const holdsWindow = ([y, m]) => [startYmd, endYmd].some(x => +x.slice(0, 4) === y && +x.slice(4, 6) === m);
    months = [months[0], ...months.slice(1).filter(holdsWindow)].slice(0, 3);
  }
  const tables = months.map(([y, m]) =>
    `<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;margin:0 0 6px">${renderMonthHtml(y, m, todayYmd, startYmd, endYmd)}</table>`).join('');
  const dot = c => `<span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${c};vertical-align:middle"></span>`;
  const legendCell = (c, label, pad) => `<td style="padding:${pad};font:11px ${CAL_FONT};color:${CAL.headFg}">${dot(c)} ${label}</td>`;
  const legend = `<table cellpadding="0" cellspacing="0" style="margin:6px 0 0"><tr>`
    + legendCell(CAL.todayBg, 'today', '0 10px 0 0')
    + legendCell(CAL.rangeBg, 'lead time', '0 10px')
    + legendCell(CAL.holidayBg, 'public holiday (PL)', '0 10px')
    + '</tr></table>';
  return tables + legend;
}

// All-day event from startYmd to endYmd inclusive, both 'yyyyMMdd' already
// formatted in the sheet's zone. DTEND is exclusive, hence the +1.
function buildIcs(summary, startYmd, endYmd, description, uidSeed) {
  const uid = uidSeed.replace(/[^\w]/g, '') + '@sheet-notify';
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  return [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//sheet-notify//EN', 'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${uid}`, `DTSTAMP:${stamp}`,
    `DTSTART;VALUE=DATE:${startYmd}`, `DTEND;VALUE=DATE:${shiftYmd(endYmd, 1)}`,
    `SUMMARY:${summary.replace(/[,;]/g, m => '\\' + m)}`,
    `DESCRIPTION:${description.replace(/\n/g, '\\n').replace(/[,;]/g, m => '\\' + m)}`,
    'END:VEVENT', 'END:VCALENDAR',
  ].join('\r\n');
}

if (typeof module !== 'undefined') {
  module.exports = {
    COL, NOTIFY, NO_EMAIL_LOG, INVALIDATED_LOG, DIGEST_KEY, ITEMS_SEP,
    columnIndex_, notifyKey_, isTicked, num_, shiftYmd, groupKey, rowNumbersInGroup, rowsInGroup, itemId, warehouseDay,
    rowsRangeLabel, addressesIn, recipientsOf, afterLastInvalidation, sentItemSet,
    addressFor, renderMail, groupForDigest, renderDigest, buildIcs, ymd_,
    ymdToDmy, easterYmd, polishHolidays, isWorkingDay, monthsBetween, buildCalendarHtml, plural_,
  };
}
