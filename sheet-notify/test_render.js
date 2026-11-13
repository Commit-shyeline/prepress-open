// node sheet-notify/test_render.js — the one check that fails if the pure logic breaks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {
  INVALIDATED_LOG, ITEMS_SEP, columnIndex_, notifyKey_, isTicked, num_, daysOr_, shiftYmd, groupKey, rowNumbersInGroup,
  rowsInGroup, itemId, warehouseDay, workingDaysBefore, orderRow, replyToOf, shippingLine,
  rowsRangeLabel, addressesIn, recipientsOf, afterLastInvalidation, sentItemSet,
  addressFor, renderMail, groupForDigest, renderDigest, buildIcs, ymd_,
  ymdToDmy, easterYmd, polishHolidays, isWorkingDay, monthsBetween, buildCalendarHtml, plural_,
} = require('./Code.js');

// Parse demo.csv the simple way: it only quotes fields that contain commas.
function parseCsv(text) {
  return text.trim().split(/\r?\n/).map(line => {
    const out = []; let cur = ''; let q = false;
    for (const ch of line) {
      if (ch === '"') q = !q;
      else if (ch === ',' && !q) { out.push(cur); cur = ''; }
      else cur += ch;
    }
    out.push(cur);
    return out;
  });
}

const [headers, ...raw] = parseCsv(fs.readFileSync(path.join(__dirname, 'demo.csv'), 'utf8'));
const idx = columnIndex_(headers);
// Sheets hands the script Date objects for date cells and numbers for numeric ones; mimic that.
const dateRoles = ['deadline', 'delivery', 'shipped', 'produced', 'confirmed', 'warehouseDate'];
const numRoles = ['daysBefore', 'daysAfter'];
const rows = raw.map(r => r.map((v, i) => {
  if (dateRoles.some(role => idx[role] === i) && /^\d{4}-\d{2}-\d{2}$/.test(v)) return new Date(v + 'T00:00:00');
  if (numRoles.some(role => idx[role] === i) && v !== '') return Number(v);
  return v;
}));
const fmt = d => (d instanceof Date ? `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}` : String(d || ''));

// 1. Three PMs on one order → three groups of one row each, keyed by code + PM address.
const fjordRows = rows.filter(r => r[idx.project] === 'Fjord Summit 2026');
assert.equal(fjordRows.length, 3);
const keys = new Set(fjordRows.map(r => groupKey(r, idx)));
assert.equal(keys.size, 3, 'each PM is their own group');
assert.equal(groupKey(fjordRows[0], idx), '001/09/2026_FS-2026|commit.shyeline+ola@gmail.com');
assert.deepEqual(rowNumbersInGroup(rows, fjordRows[0], idx), [2], 'Ola gets only her row');

// 1b. One PM with two rows → one group of two.
const harbour = rows.find(r => r[idx.project] === 'Harbour Fest');
assert.deepEqual(rowNumbersInGroup(rows, harbour, idx), [6, 7]);
assert.equal(groupKey(harbour, idx), '003/09/2026_HF|commit.shyeline+kari@gmail.com');

// 1b'. Mixed statuses inside one group split by status: a Produkcja mail never lists a transport row.
const mixed = rows.map(r => r.slice());   // array index = sheet row − 2
mixed[5][idx.status] = 'TRANSPORT';                       // second Harbour row (sheet row 7) already shipped
assert.deepEqual(rowNumbersInGroup(mixed, mixed[4], idx, 'Produkcja'), [6], 'only the Produkcja row');
assert.deepEqual(rowNumbersInGroup(mixed, mixed[5], idx, 'transport'), [7], 'only the transport row');
assert.deepEqual(rowNumbersInGroup(mixed, mixed[4], idx), [6, 7], 'no status filter → whole group');

// 1c. Sloppy text does not split a group: trailing space, mixed case, PM name typo — the code + address decide.
const sloppy = harbour.slice();
sloppy[idx.client] = 'Nordlys Events '; sloppy[idx.pm] = 'kari moen // typo'; sloppy[idx.orderCode] = '003/09/2026_hf';
sloppy[idx.pmEmail] = 'COMMIT.SHYELINE+KARI@GMAIL.COM';
assert.equal(rowsInGroup([...rows, sloppy], harbour, idx).length, 3);

// 1d. No code → fall back to client|project|PM|delivery.
const winter = rows.find(r => r[idx.project] === 'Winter Pop-up');
const noCode = winter.slice(); noCode[idx.orderCode] = '';
assert.equal(groupKey(noCode, idx), 'Aurora Retail|Winter Pop-up|commit.shyeline+mikko@gmail.com|');

// 2. Three statuses notify; matching ignores case and spaces.
assert.equal(notifyKey_('Produkcja'), 'Produkcja');
assert.equal(notifyKey_(' TRANSPORT '), 'transport');
assert.equal(notifyKey_('DELIVERED'), 'Delivered', 'delivery confirmation (customer request 21.09)');
assert.equal(notifyKey_('u klienta'), '', 'the old Polish wording is not a status any more');
assert.equal(notifyKey_('Wyceny'), '');
assert.equal(notifyKey_('Faktura'), '');
const delivered = renderMail([harbour], idx, 'Delivered', fmt0 => String(fmt0 || ''));
assert.equal(delivered.subject, 'Your order has been delivered – Harbour Fest');
assert.ok(delivered.text.includes('has been delivered'));

// 3. Checkbox values as Sheets delivers them.
assert.equal(isTicked(true), true);
assert.equal(isTicked('TRUE'), true);
assert.equal(isTicked(false), false);
assert.equal(isTicked(''), false);
assert.equal(num_(''), 0); assert.equal(num_('3'), 3); assert.equal(num_('x'), 0);
// Lead-time defaults (customer 19.10: 1 day after, never before): empty → default, a typed 0 stays 0.
assert.equal(daysOr_('', 1), 1); assert.equal(daysOr_(undefined, 1), 1);
assert.equal(daysOr_(0, 1), 0); assert.equal(daysOr_(3, 1), 3);

// 4. Recipients: PM only, warehouse is not on the per-order mail.
assert.deepEqual(recipientsOf(fjordRows[0], idx), ['commit.shyeline+ola@gmail.com']);
assert.deepEqual(addressesIn('Ola Berg // ola@x.no // +47 999; OLA@x.no, lager@x.no'), ['ola@x.no', 'lager@x.no']);
// Replies go to the Ambient Hub project leader (customer 19.10); no address in LEADER → no reply-to.
assert.equal(replyToOf(fjordRows[0], idx), 'commit.shyeline+leader@gmail.com');
assert.equal(replyToOf(fjordRows[1], idx), '');

// 5. Dedupe per ITEM, invalidation markers, test redirect.
const both = ['a@x.no', 'b@x.no'];
const entry = (recipients, ...items) => ({ recipients, items: items.join(ITEMS_SEP) });
assert.deepEqual([...sentItemSet([])], []);
assert.deepEqual([...sentItemSet([entry('a@x.no', 'Round table 200x130')])], ['round table 200x130'], 'lower-cased');
assert.deepEqual([...sentItemSet([entry('(no e-mail on row — nothing sent)', 'X')])], [], 'markers do not count as sent');
assert.deepEqual([...sentItemSet([entry('a@x.no', 'A', 'B'), entry('a@x.no', 'C')])].sort(), ['a', 'b', 'c']);
assert.equal(itemId(harbour, idx), 'round table 200x130');
// Only the not-yet-sent item of a group is pending → the mail lists just that one.
const sentSoFar = sentItemSet([entry('commit.shyeline+kari@gmail.com', 'Round table 200x130')]);
assert.deepEqual(rowNumbersInGroup(rows, harbour, idx, 'Produkcja').filter(n => !sentSoFar.has(itemId(rows[n - 2], idx))), [7]);
// Invalidation: entries before the marker are forgotten, on plain strings and on entries alike.
assert.deepEqual(afterLastInvalidation(['a@x.no', INVALIDATED_LOG, 'b@x.no']), ['b@x.no']);
const entries = [entry('a@x.no', 'A'), entry(INVALIDATED_LOG), entry('a@x.no', 'B')];
assert.deepEqual([...sentItemSet(afterLastInvalidation(entries, x => x.recipients))], ['b']);
assert.deepEqual([...sentItemSet(afterLastInvalidation([entry('a@x.no', 'A'), entry(INVALIDATED_LOG)], x => x.recipients))], []);
assert.deepEqual(addressFor(both, 'Hi', ''), { to: 'a@x.no,b@x.no', subject: 'Hi' });
assert.deepEqual(addressFor(both, 'Hi', 'test@x.no'), { to: 'test@x.no', subject: '[TEST → a@x.no, b@x.no] Hi' });

// 6. Per-order mail: English, greets the PM, lists the group's items, shows dates and the offer link.
const mail = renderMail(rowsInGroup(rows, harbour, idx), idx, 'transport', fmt);
assert.equal(mail.subject, 'Your order is on its way – Harbour Fest');
assert.ok(mail.html.includes('Dear Kari Moen'));
assert.ok(mail.html.includes('Round table 200x130') && mail.html.includes('Step-style shelving unit'));
assert.ok(mail.html.includes('19.09.2026') && mail.html.includes('01.09.2026'));
const dhlUrl = 'https://www.dhl.com/pl-en/home/tracking.html?tracking-id=00340434161094012345';
assert.ok(mail.html.includes(`DHL, tracking <a href="${dhlUrl}">00340434161094012345</a>`), 'DHL number is a tracking link');
assert.ok(mail.text.includes(`DHL, tracking 00340434161094012345: ${dhlUrl}`));
assert.ok(mail.html.includes('https://example.com/offer/HF-2026-004'));
assert.ok(mail.html.includes('Planned delivery') && !mail.html.includes('Lead time'), 'transport shows the delivery date');

// 6a. The carrier decides the Shipping line (logistics 07.10, customer 19.10).
const withShipping = (carrier, tracking) => {
  const r = harbour.slice(); r[idx.carrier] = carrier; r[idx.tracking] = tracking; return shippingLine(r, idx);
};
const driver = withShipping('own transport', 'Adam Wałek\n+48 600 000 000\nWX 12345');
assert.ok(driver.html.includes('Own transport, driver:<br>Adam Wałek<br>+48 600 000 000<br>WX 12345'), 'driver details, one per line');
const sheetLink = 'https://docs.google.com/spreadsheets/d/abc/edit';
assert.equal(withShipping('EXCEL', sheetLink).html, `<a href="${sheetLink}">Parcel list</a>`, 'any carrier: link to the parcel-list sheet');
assert.equal(withShipping('EXCEL', ''), null, 'no link yet → no Shipping line');
// Order-level details may sit on any one row of the order (Shyeline's demo test 21.10).
const blankRow = harbour.slice(); blankRow[idx.carrier] = ''; blankRow[idx.tracking] = '';
const filledRow = harbour.slice(); filledRow[idx.carrier] = 'Own transport'; filledRow[idx.tracking] = 'Adam\n+48 600';
assert.equal(orderRow([blankRow, filledRow])[idx.tracking], 'Adam\n+48 600');
assert.equal(orderRow([filledRow, blankRow])[idx.carrier], 'Own transport', 'first filled wins');
assert.ok(renderMail([blankRow, filledRow], idx, 'transport', fmt).html.includes('Own transport, driver:<br>Adam<br>+48 600'));
assert.deepEqual(withShipping('Spedycja', 'anything'), { text: 'SUPPLY CHAIN', html: 'SUPPLY CHAIN' }, 'freight shows only the label');
assert.equal(withShipping('DPD', '12345678901234').html,
  'DPD, tracking <a href="https://tracktrace.dpd.com.pl/parcelDetails?typ=1&amp;p1=12345678901234">12345678901234</a>');
assert.ok(withShipping('inpost', '6200').html.includes('href="https://inpost.pl/sledzenie-przesylek?number=6200"'), 'carrier match ignores case');
assert.ok(withShipping('UPS', '1Z999').html.includes('href="https://www.ups.com/track?loc=en_US&amp;tracknum=1Z999"'));
assert.ok(withShipping('FedEx', '7712').html.includes('href="https://www.fedex.com/fedextrack/?trknbr=7712"'));
assert.equal(withShipping('Schenker', 'X1').text, 'Schenker, tracking X1', 'unknown carrier keeps the old wording');
assert.equal(withShipping('DHL', '').text, 'DHL');
assert.equal(withShipping('', ''), null);
// A size typed on several lines (Alt+Enter) stays on several lines in the HTML mail.
const multiLine = harbour.slice(); multiLine[idx.size] = '40x40x200 – 2 pcs\n40x40x180 – 1 pc';
assert.ok(renderMail([multiLine], idx, 'transport', fmt).html.includes('40x40x200 – 2 pcs<br>40x40x180 – 1 pc'));

// 6b. Produkcja mail: "Lead time" range replaces "Planned delivery", inline calendar follows.
const lead = { start: '20260929', end: '20261003', today: '20260917' };
const prod = renderMail([fjordRows[0]], idx, 'Produkcja', fmt, lead);
assert.equal(prod.subject, 'Production has started – Fjord Summit 2026');
assert.ok(prod.html.includes('Lead time') && prod.html.includes('29.09.2026 – 03.10.2026'));
assert.ok(!prod.html.includes('Planned delivery'), 'Produkcja shows the window, not a single date');
assert.ok(prod.text.includes('Lead time: 29.09.2026 – 03.10.2026'));
assert.ok(prod.html.includes('September 2026') && prod.html.includes('October 2026'), 'calendar spans both months');
assert.ok(prod.html.includes('public holiday (PL)'), 'legend present');

// 6c. Calendar maths: Easter, holidays, working days, month trimming.
assert.equal(easterYmd(2026), '20260405');
assert.equal(easterYmd(2024), '20240331');
const hol = polishHolidays(2026);
assert.ok(hol.has('20260406') && hol.has('20260604') && hol.has('20261111'), 'Easter Monday, Corpus Christi, Independence Day');
assert.equal(isWorkingDay('20261003', hol), false, 'Saturday');
assert.equal(isWorkingDay('20260929', hol), true, 'Tuesday');
assert.equal(isWorkingDay('20261111', hol), false, 'holiday on a Wednesday');
assert.deepEqual(monthsBetween('20261101', '20270201'), [[2026, 11], [2026, 12], [2027, 1], [2027, 2]]);
assert.equal(ymdToDmy('20261002'), '02.10.2026');
const cal = buildCalendarHtml('20260917', '20260929', '20261003');
assert.equal((cal.match(/#54b9f0/g) || []).length, 4 + 1, 'four working days tinted (29, 30 Sep, 1, 2 Oct) + legend; Sat 3 Oct is not');
assert.ok(cal.includes('#0f6fa6'), 'today cell in Ambient Hub blue');
assert.ok(!cal.includes('#86e6a3') && !cal.includes('#1f7a3d'), 'no Luxpol green anywhere');
const far = buildCalendarHtml('20260917', '20270105', '20270107');
assert.ok(far.includes('September 2026') && far.includes('January 2027') && !far.includes('November 2026'),
  'more than three months → keep today\'s month and the months holding the window');
assert.equal(buildCalendarHtml('20260917', '', ''), '');

// 7. Calendar range: delivery 02.10 with 3 days before and 1 after → 29.09 to 03.10 inclusive; DTEND exclusive.
assert.equal(shiftYmd('20261002', -3), '20260929');
assert.equal(shiftYmd('20261002', 1), '20261003');
assert.equal(shiftYmd('20261231', 1), '20270101', 'year rollover');
const ics = buildIcs('x', shiftYmd('20261002', -3), shiftYmd('20261002', 1), 'desc', 'k');
assert.ok(ics.includes('DTSTART;VALUE=DATE:20260929'));
assert.ok(ics.includes('DTEND;VALUE=DATE:20261004'), 'end is inclusive, DTEND is the day after');
assert.ok(ics.startsWith('BEGIN:VCALENDAR') && ics.endsWith('END:VCALENDAR'));
assert.equal(ymd_('not a date'), 'not a date');

// 8. Warehouse digest: rows whose "Data wysyłki z Polski" is today, bucketed by warehouse address.
const shippedOn = day => row => ymd_(row[idx.shipped]) === day;
const buckets = groupForDigest(rows, idx, shippedOn('20260916'));
assert.deepEqual([...buckets.keys()], ['commit.shyeline+magazyn@gmail.com']);
const items = buckets.get('commit.shyeline+magazyn@gmail.com');
assert.equal(items.length, 2, 'both Harbour rows ship on 16.09; Fjord ships 29.09');
const digest = renderDigest(items, idx, fmt, '16.09.2026');
assert.equal(digest.subject, 'Upcoming deliveries – 16.09.2026');
assert.ok(!/dispatched|shipped/i.test(digest.text), 'the warehouse is told before dispatch, so the mail must not say the goods left');
for (const h of ['Item', 'Size / weight', 'PM', 'PM e-mail', 'Delivery']) assert.ok(digest.html.includes(`<th align="left">${h}</th>`), h);
assert.ok(digest.html.includes('Kari Moen') && digest.html.includes('Round table 200x130'));
assert.ok(!digest.html.includes('Ola Berg'), 'Fjord did not ship that day');
assert.ok(!digest.html.includes('Producer') && !digest.html.includes('Box Demo'), 'the recipient\'s warehouse never sees the producer');
const toldSize = items[0].slice(); toldSize[idx.warehouseSize] = '2 pallets, 120 kg';
const sizedDigest = renderDigest([toldSize, items[1]], idx, fmt, '16.09.2026').html;
assert.ok(sizedDigest.includes('2 pallets, 120 kg') && !sizedDigest.includes('1 pallet – approx. 60 kg'), 'warehouse figure wins');
assert.ok(sizedDigest.includes('100x125x80 – 4 pcs'), 'empty warehouse figure → the estimate');
assert.equal(groupForDigest(rows, idx, shippedOn('20260929')).get('commit.shyeline+magazyn@gmail.com').length, 3, 'all three Fjord rows ship 29.09');
assert.equal(groupForDigest(rows, idx, () => false).size, 0, 'nothing shipped → no digest');

// 8c. The warehouse is told on its own date; without one, two working days before delivery.
const dayOf = d => ymd_(d);
assert.equal(warehouseDay(harbour, idx, dayOf), '20260914', 'own "Data maila do magazynu" wins');
const noWarehouseDate = harbour.slice();
noWarehouseDate[idx.warehouseDate] = '';
assert.equal(ymd_(harbour[idx.delivery]), '20260919', 'Harbour is delivered on Saturday 19.09…');
assert.equal(warehouseDay(noWarehouseDate, idx, dayOf), '20260917', '…so the warehouse hears on Thursday 17.09');
assert.equal(workingDaysBefore('20261005', 2), '20261001', 'Monday delivery → Thursday, the weekend does not count');
assert.equal(workingDaysBefore('20261112', 2), '20261109', '11.11 is a Polish holiday and does not count');
const noDates = noWarehouseDate.slice();
noDates[idx.delivery] = '';
assert.equal(warehouseDay(noDates, idx, dayOf), '', 'no dates → never in a digest');

// 8b. Polish plurals for the Log comments.
assert.equal(plural_(1, 'adres', 'adresy', 'adresów'), '1 adres');
assert.equal(plural_(2, 'adres', 'adresy', 'adresów'), '2 adresy');
assert.equal(plural_(5, 'adres', 'adresy', 'adresów'), '5 adresów');
assert.equal(plural_(12, 'pozycja', 'pozycje', 'pozycji'), '12 pozycji');
assert.equal(plural_(22, 'pozycja', 'pozycje', 'pozycji'), '22 pozycje');
assert.equal(plural_(0, 'pozycja', 'pozycje', 'pozycji'), '0 pozycji');

// 9. A sheet missing a required header fails loudly, not silently.
assert.throws(() => columnIndex_(headers.filter(h => h !== 'Stan')), /Missing header\(s\): Stan/);
// A header with a hint on its second line still matches by its first line.
const hinted = headers.map(h => (h === 'Numer śledzenia' ? 'Numer śledzenia\n\\ dane śledzenia' : h));
assert.equal(columnIndex_(hinted).tracking, idx.tracking);
assert.deepEqual(rowsRangeLabel([6, 7]), { range: '6:7', label: 'rows 6–7' });

console.log('sheet-notify: all checks passed');
