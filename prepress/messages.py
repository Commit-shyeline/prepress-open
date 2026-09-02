"""Customer-facing text, as data.

The engine never writes a sentence. It emits a NOTICE CODE plus the numbers behind it, and the text
comes from here — editable by the shop, per language, and overridable per material.

That split exists because of a real mistake: the first version told customers "wider than the 3100 mm
roll — it will be printed rotated", which is a production detail, not a problem. It made a fine job
look broken. A shop knows which of its constraints a customer should worry about; the code does not,
so the code stops guessing and the shop writes the words.

Placeholders are `{name}` and are filled from the notice's values. A template referring to a
placeholder that does not exist falls back to the default rather than raising — a typo in an admin's
message must not take the generator down.
"""
import string

# Every notice the engine can raise, with the text a shop starts from. Keys are stable identifiers:
# renaming one is a breaking change for anyone who has customised it.
NOTICE_MESSAGES = {
    # Informational: the job is fine, this just describes how it will be made.
    #
    # The panel COUNT is deliberately not in this sentence (shop rule, 2026-08-23: "mówi że będzie
    # brytowana ale nie mówimy na ile i nie pokazujemy jak — to już będzie po stronie grafika"). The
    # fact of panelling is the customer's business, because the welds are visible in the product; how
    # many strips it takes is a production decision that can still change. `{panels}` is still in the
    # notice's values, so a shop that wants the number puts the placeholder back from the panel.
    "panelled": (
        "Przy tym rozmiarze grafika będzie brytowana, czyli drukowana w pasach i zgrzewana "
        "w jedną całość. Zgrzewy są widoczne z bliska; przy dużych formatach "
        "to standardowa technologia."
    ),
    "scaled": (
        "Szablon jest w skali 1:{scale}, bo w pełnym rozmiarze plik PDF nie mógłby istnieć. "
        "Projektuj w rozmiarze strony, my przeskalujemy do {netto_w}×{netto_h} mm."
    ),
    # Refusals: the job cannot be made as asked.
    "too_big_to_panel": (
        "{netto_w}×{netto_h} mm przekracza możliwy rozmiar brytowania "
        "({panel_max_long}×{panel_max_short} mm). Podziel grafikę na mniejsze części."
    ),
    "safe_area_eats_job": (
        "Przy marginesie bezpiecznym {safe} mm na tym materiale rozmiar {netto_w}×{netto_h} mm "
        "nie ma użytecznej powierzchni. Wybierz większy rozmiar albo inny materiał."
    ),
    "too_big_for_pdf": (
        "{longest} mm nie zmieści się na stronie PDF nawet w największej skali."
    ),
    "unknown_material": "Nie znamy tego materiału. Odśwież stronę.",
    "empty_queue": "Lista jest pusta. Dodaj przynajmniej jeden rozmiar.",
    "dimensions_required": "Podaj oba wymiary.",
    "not_a_number": "„{value}” nie jest liczbą.",
    "must_be_positive": "Wymiary muszą być większe od zera.",
    "unknown_unit": "Nieznana jednostka „{unit}”. Użyj mm, cm albo m.",
    "absurd_dimension": "{millimetres} mm to więcej niż jakakolwiek maszyna. Sprawdź jednostkę.",
}

# What the CHECK says about a returned file. One code per outcome, plus an optional `.detail` holding
# the advice — a title tells the customer what is wrong, the detail tells them what to do about it.
#
# The codes read `check.<rule id>.<outcome>`, and the rule id is the same one a shop's severity
# settings are keyed on, so a rule, its wording and its severity are always found under one name.
#
# The Polish here is carried over from the in-house engine, where it has been read by real customers
# for months — with the production internals taken out. Nothing in this block may explain HOW a job is
# made: a first version told customers a banner was "wider than the 3100 mm roll", which is our
# business, not theirs, and it made a perfectly good job look broken.
CHECK_MESSAGES = {
    # Geometry.
    "check.page_size.ok": "Rozmiar strony zgodny: {page_w}×{page_h} mm",
    "check.page_size.rotated": "Strona jest obrócona",
    "check.page_size.rotated.detail":
        "Szablon ma {expected_w}×{expected_h} mm, plik ma {page_w}×{page_h} mm. "
        "Sprawdzimy go, ale upewnij się, że obrót jest zamierzony.",
    "check.page_size.scaled": "Plik jest w skali 1:{scale}",
    "check.page_size.scaled.detail":
        "Strona ma {page_w}×{page_h} mm, gotowy produkt {expected_w}×{expected_h} mm. "
        "Rozdzielczość i wielkość tekstu ocenialiśmy w rozmiarze docelowym.",
    "check.page_size.no_bleed": "Plik ma rozmiar gotowy, bez spadu: {page_w}×{page_h} mm",
    "check.page_size.no_bleed.detail":
        "Ten materiał wymaga {bleed} mm spadu z każdej strony, czyli strony {expected_w}×{expected_h} mm. "
        "Powiększ tło poza rozmiar gotowy — przy cięciu zostanie inaczej biały rąbek.",
    "check.page_size.two_up": "To wygląda na dwie strony w jednym pliku: {page_w}×{page_h} mm",
    "check.page_size.two_up.detail":
        "Połowa pliku odpowiada rozmiarowi {expected_w}×{expected_h} mm, więc wygląda to na dwie "
        "strony obok siebie. Prześlij każdą stronę jako osobny plik, inaczej wydruk pójdzie "
        "w złym rozmiarze.",
    "check.page_size.finishing": "Rozmiar z zapasem na wykończenie: {page_w}×{page_h} mm",
    "check.page_size.finishing.detail":
        "Format docelowy {expected_w}×{expected_h} mm. Różnica to zapewne tunel, rękaw albo zakładka.",
    "check.page_size.oversize": "Plik znacznie większy niż podany rozmiar: {page_w}×{page_h} mm",
    "check.page_size.oversize.detail":
        "Podany rozmiar to {expected_w}×{expected_h} mm, a różnica przekracza {allowance} mm zapasu "
        "na wykończenie. Sprawdź, czy to właściwy plik i czy rozmiar jest podany dobrze.",
    "check.page_size.wrong": "Rozmiar strony nie zgadza się z szablonem",
    "check.page_size.wrong.detail":
        "Oczekujemy {expected_w}×{expected_h} mm, plik ma {page_w}×{page_h} mm. "
        "Projektuj na naszym szablonie i nie zmieniaj rozmiaru strony.",

    "check.declared_trim.ok": "Linia cięcia zgodna z rozmiarem gotowym: {trim_w}×{trim_h} mm",
    "check.declared_trim.rotated": "Zadeklarowana linia cięcia jest obrócona względem szablonu",
    "check.declared_trim.rotated.detail":
        "Szablon: {expected_w}×{expected_h} mm, plik: {trim_w}×{trim_h} mm.",
    "check.declared_trim.wrong": "Zadeklarowana linia cięcia nie zgadza się z rozmiarem gotowym",
    "check.declared_trim.wrong.detail":
        "Plik deklaruje cięcie na {trim_w}×{trim_h} mm, a szablon jest na "
        "{expected_w}×{expected_h} mm. Sprawdź ustawienia dokumentu, bo rozmiar strony może być "
        "poprawny, a i tak obetniemy w złym miejscu.",

    "check.template_guides.ok": "Linie szablonu usunięte",
    "check.template_guides.present": "W pliku zostały linie szablonu",
    "check.template_guides.present.detail":
        "Linie spadu, cięcia i obszaru bezpiecznego wydrukują się na gotowym produkcie. "
        "Usuń warstwę z szablonem przed wysłaniem.",

    "check.bleed_coverage.ok": "Grafika sięga spadu",
    "check.bleed_coverage.short": "Grafika nie sięga spadu (brakuje {missing} mm)",
    "check.bleed_coverage.short.detail":
        "Po obcięciu zostanie biały pasek. Rozciągnij tło do krawędzi strony, "
        "spad to {bleed} mm z każdej strony.",
    "check.bleed_coverage.unmeasured": "Nie udało się zmierzyć pokrycia spadu",

    "check.safe_area.ok": "Obszar bezpieczny zachowany",
    "check.safe_area.intrusion": "Treść wchodzi w obszar bezpieczny ({intrusion} mm)",
    "check.safe_area.intrusion.detail":
        "Napisy i logo trzymaj {safe} mm od linii cięcia, bo przy wykończeniu ta strefa "
        "może zniknąć.",
    "check.safe_area.unmeasured": "Nie udało się zmierzyć obszaru bezpiecznego",

    "check.resolution.ok": "Rozdzielczość w porządku: {dpi} DPI",
    "check.resolution.low": "Rozdzielczość za niska: {dpi} DPI",
    "check.resolution.low.detail":
        "Ten materiał wymaga minimum {floor} DPI w rozmiarze docelowym.",
    "check.resolution.unmeasured": "Nie sprawdziliśmy rozdzielczości",
    "check.resolution.unmeasured.detail":
        "Nie znaleźliśmy w pliku grafiki rastrowej. Grafika wektorowa jest w porządku.",

    # What the file declares about itself.
    "check.office_origin.office": "To wygląda na dokument, nie na plik do druku",
    "check.office_origin.office.detail":
        "Plik został wyeksportowany z programu biurowego ({application}). "
        "Prześlij grafikę przygotowaną do druku.",

    "check.colour_mode.ok": "Tryb kolorów CMYK",
    "check.colour_mode.rgb": "Plik zawiera kolory RGB ({spaces})",
    "check.colour_mode.rgb.detail":
        "Przyjmujemy CMYK. Przy RGB nie odpowiadamy za kolorystykę wydruku, "
        "przekonwertuj plik na CMYK.",
    "check.colour_mode.icc": "Kolory w profilu ICC",
    "check.colour_mode.icc.detail":
        "Sprawdź, czy profil jest CMYK. Profil RGB może zmienić kolorystykę wydruku.",

    "check.spot_inks.found": "Plik zawiera kolory dodatkowe: {inks}",
    "check.spot_inks.found.detail":
        "Przekonwertuj je na wartości CMYK, inaczej kolor może wyjść inaczej, niż oczekujesz. "
        "Linii cięcia i zagięcia to nie dotyczy.",

    "check.cut_path.ok": "Wykrojnik obecny (kolor dodatkowy: {cut})",
    "check.cut_path.missing": "Nie znaleźliśmy wykrojnika w kolorze dodatkowym",
    "check.cut_path.missing.detail":
        "Obrys cięcia podaj jako kolor dodatkowy nazwany „Cut”, w krzywych, tylko obrys bez "
        "wypełnienia. Jeśli wykrojnik jest w czerni albo w Pantone, sprawdzimy go ręcznie.",

    "check.cut_geometry.ok": "Wykrojnik „{cut}”: {cut_w}×{cut_h} mm",
    "check.cut_geometry.ok.detail":
        "Długość cięcia: {length}, kontury: {contours}. Zmierzone z krzywych w tym kolorze dodatkowym.",
    "check.cut_geometry.filled": "Wykrojnik „{cut}” jest wypełniony, a nie obrysowany",
    "check.cut_geometry.filled.detail":
        "Linia cięcia ma być samym obrysem bez wypełnienia — wypełnienie w kolorze „{cut}” "
        "wydrukuje się albo zakryje grafikę. Zostaw tylko kontur.",
    "check.cut_geometry.open": "Wykrojnik „{cut}” nie jest zamknięty",
    "check.cut_geometry.open.detail":
        "Ploter tnie po zamkniętym kontrze. Połącz końce ścieżki cięcia.",

    "check.cut_margins.ok": "Grafika dochodzi do linii cięcia",
    "check.cut_margins.ok.detail":
        "Tło wychodzi poza wykrojnik „{cut}” na całym obwodzie. Napisy i logo trzymaj wewnątrz "
        "obszaru bezpiecznego.",
    "check.cut_margins.bare": "Grafika nie dochodzi do linii cięcia ({share} obwodu)",
    "check.cut_margins.bare.detail":
        "Tuż za linią „{cut}” nie ma już grafiki na około {share} obwodu. Nóż nigdy nie trafia "
        "w linię co do milimetra, więc wyjdzie tam biały rąbek — rozciągnij tło co najmniej "
        "{bleed} mm POZA linię cięcia. Jeśli grafika jest tam po prostu biała, zignoruj tę uwagę.",
    "check.cut_margins.off_sheet": "Wykrojnik wychodzi poza arkusz ({sides})",
    "check.cut_margins.off_sheet.detail":
        "Linia cięcia „{cut}” wychodzi poza plik. Nóż nie ma po czym jechać — powiększ arkusz "
        "albo przesuń wykrojnik.",
    "check.cut_margins.unmeasured": "Nie zmierzyliśmy grafiki wokół wykrojnika „{cut}”",

    "check.raster_flat.ok": "Plik spłaszczony",
    "check.raster_flat.layers": "Plik nie jest spłaszczony ({mode})",
    "check.raster_flat.layers.detail":
        "Bitmapa ma kanał przezroczystości albo dodatkowe kanały. Spłaszcz obraz (bez warstw i "
        "masek) i zapisz ponownie, np. TIFF z kompresją LZW.",

    "check.fonts.ok": "Teksty zamienione na krzywe",
    "check.fonts.present": "Teksty nie są zamienione na krzywe",
    "check.fonts.present.detail":
        "Znaleźliśmy fonty: {fonts}. Zamień teksty na krzywe, bo inaczej mogą zmienić kształt "
        "przy druku.",

    "check.overprint.on": "Włączone nadrukowania (overprint)",
    "check.overprint.on.detail":
        "Wyłącz nadrukowania. Mogą zmienić kolor albo sprawić, że element nie pojawi się "
        "na wydruku.",

    "check.page_count.many": "Plik ma więcej niż jedną stronę ({pages})",
    "check.page_count.many.detail":
        "Przyjmujemy pliki jednostronicowe. Prześlij każdy wzór jako osobny plik.",
    "check.page_count.cut_work": "Plik ma {pages} strony",
    "check.page_count.cut_work.detail":
        "Przy plikach ciętych to prawidłowe: wykrojnik na osobnej stronie.",

    "check.text_height.ok": "Najmniejszy tekst ma {smallest} mm",
    "check.text_height.small": "Za mały tekst: {smallest} mm w rozmiarze docelowym",
    "check.text_height.small.detail":
        "Napisy poniżej {floor} mm wysokości są nieczytelne z odległości, z której ogląda się "
        "wydruk wielkoformatowy. Powiększ najmniejsze teksty.",

    "check.split.required": "Grafika {netto_w}×{netto_h} mm będzie dzielona",
    "check.split.required.detail":
        "Oba wymiary przekraczają {over} mm, więc wydruk powstanie z kilku części łączonych "
        "przy wykończeniu. Trzymaj ważne elementy z dala od środka grafiki.",

    "check.named_size.ok": "Rozmiar z nazwy pliku zgodny: {named_w}×{named_h} mm",
    "check.named_size.differs": "Nazwa pliku mówi {named_w}×{named_h} mm, sprawdzamy {netto_w}×{netto_h} mm",
    "check.named_size.differs.detail":
        "Jedno z tych dwóch jest pomyłką. Jeśli zamawiasz {netto_w}×{netto_h} mm, wszystko gra — "
        "jeśli nie, popraw rozmiar i sprawdź ponownie.",

    "check.filename.diacritics": "Nazwa pliku zawiera polskie znaki",
    "check.filename.diacritics.detail":
        "Używaj tylko liter bez ogonków, cyfr, myślników i podkreśleń, "
        "np. baner-800x2000mm.pdf.",
    "check.filename.dots": "Nazwa pliku zawiera dodatkowe kropki",
    "check.filename.dots.detail":
        "Kropka w nazwie myli programy, które rozpoznają plik po rozszerzeniu. "
        "Zostaw jedną, przed rozszerzeniem.",

    # A rule that broke. Reported rather than hidden, because a silent gap in a check list reads as
    # a pass.
    "check.rule.failed": "Nie udało się sprawdzić jednej z reguł",
    "check.rule.failed.detail": "{error}",

    # The one line at the top of a report.
    "summary.errors": "BŁĘDY ({count}): {titles}",
    "summary.warnings": "UWAGI ({count}): {titles}",
    "summary.ok": "OK: plik wygląda poprawnie",
    # The bare template sent straight back: recognised, but there is no design to judge — and the
    # summary must never read as a pass, or a blank file walks into production stamped "OK".
    "summary.bare_template": "Czysty szablon, bez projektu do sprawdzenia",
}

# One namespace, so `render` and the shop's overrides work the same way for a notice and a check.
DEFAULT_MESSAGES = {**NOTICE_MESSAGES, **CHECK_MESSAGES}

# Which notices are advice rather than problems. The UI colours them differently, and — the point of
# A correction from the shop — it can move a code between these by editing its own text, without
# anyone touching Python.
INFO_CODES = frozenset({"panelled", "scaled"})


class _Safe(dict):
    """A mapping that leaves unknown placeholders visibly alone instead of raising."""

    def __missing__(self, key):
        return "{" + key + "}"


class _Elided(dict):
    """A mapping that turns every placeholder into an ellipsis, for labels with no numbers yet."""

    def __missing__(self, key):
        return "…"


def render(code, values=None, overrides=None):
    """The text for one notice code, with its numbers filled in.

    `overrides` is the shop's own `messages` block; anything missing falls back to the default, so a
    partially customised install still produces complete sentences.
    """
    template = (overrides or {}).get(code) or DEFAULT_MESSAGES.get(code)
    if not template:
        # An unknown code is a bug, but a bug that must not silence the notice entirely.
        return code
    try:
        return string.Formatter().vformat(template, (), _Safe(values or {}))
    except (ValueError, IndexError):
        fallback = DEFAULT_MESSAGES.get(code, code)
        try:
            return string.Formatter().vformat(fallback, (), _Safe(values or {}))
        except (ValueError, IndexError):
            return fallback


def render_label(code, overrides=None):
    """One message with its numbers elided, for use as a LABEL rather than as a sentence.

    The admin panel labels each severity control with the wording that rule uses, and there are no
    numbers to fill in yet — `Rozdzielczość za niska: … DPI` is a label, `… {dpi} DPI` is a template
    leaking into the interface.
    """
    template = (overrides or {}).get(code) or DEFAULT_MESSAGES.get(code)
    if not template:
        return code
    try:
        return string.Formatter().vformat(template, (), _Elided())
    except (ValueError, IndexError):
        return template


def render_optional(code, values=None, overrides=None):
    """The text for a code that may legitimately not exist, or None.

    `render` answers with the code itself for an unknown key, which is right for a notice — a missing
    sentence there is a bug worth seeing. A finding's `.detail` is different: plenty of findings are
    a title and nothing else, so absence is normal and must come back empty rather than printing
    `check.fonts.ok.detail` at a customer.
    """
    if code not in DEFAULT_MESSAGES and code not in (overrides or {}):
        return None
    return render(code, values, overrides)


def level_for(code):
    return "info" if code in INFO_CODES else "error"


def notice(code, **values):
    """What the engine emits instead of a sentence."""
    return {"code": code, "values": values}


def render_all(notices, overrides=None):
    """Notices → [{code, level, text}] for display."""
    return [{"code": n["code"], "level": level_for(n["code"]),
             "text": render(n["code"], n.get("values"), overrides)}
            for n in (notices or [])]
