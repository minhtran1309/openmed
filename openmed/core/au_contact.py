"""Australian contact details: phone/fax numbers, postcodes, states, addresses.

The patterns here are appended to the ``en_AU`` locale pattern list
(``openmed.core.pii_i18n.LOCALE_PII_PATTERNS["en_au"]``) next to the
checksum identifiers in :mod:`openmed.core.au_identifiers`, so they reach
``extract_pii`` smart merging, the deterministic safety sweep and the
``Pipeline`` deterministic stage whenever the AU locale is in effect.

Raw labels reuse openmed's existing vocabulary (``phone_number``,
``postcode``, ``street_address``, ``location``; canonical ``PHONE``,
``ZIPCODE``, ``STREET_ADDRESS``, ``LOCATION``). Each match carries a
value-free ``metadata["subtype"]``:

* ``AU_PHONE``: mobiles (``04``), geographic numbers (``02/03/07/08`` plus an
  eight-digit local number starting ``2``-``9``), ``+61`` forms with the
  leading ``0`` dropped (``+61 (0)2`` is accepted), and labelled bare local
  numbers. Fax lines use the same rules. ``05`` numbers are not accepted:
  the numbering plan reserves them but no mobile services use them.
* ``AU_PHONE_INSTITUTIONAL``: ``13 xx xx``, ``1300`` and ``1800`` numbers,
  which are usually organisational.
* ``AU_POSTCODE``: a four-digit postcode, only directly after a state or
  territory whose postcode ranges contain it, or after the suburb of a
  detected street address. A bare four-digit number is never a postcode.
* ``AU_STATE``: a state/territory name or abbreviation directly before a
  postcode in its ranges, or after a detected street address (and suburb).
* ``AU_ADDRESS``: one span per street address (unit, number, street name and
  street type) or postal address (``PO Box``, ``GPO Box``, ``Locked Bag``,
  ``Private Bag``, ``RMB``), extended over the suburb only when a state or
  postcode follows it.

Design rules:

* Spans are values only. The labelled local-number pattern keeps its field
  label outside a named ``value`` group.
* Phone matches never start or end inside a longer separated digit run.
  In a ``Pipeline`` run a column gap (a tab or two or more spaces in the
  source) is not a separator, so a phone in a table cell is still found when
  the next or previous cell starts with a digit (a provider number).
* Street names, street types and state abbreviations are case-sensitive, so
  ``CT chest``, ``Dr Jones``, ``by the way`` and ``SA node`` are not matched;
  ``Dr`` and ``Ct`` only count after a house number and a capitalised street
  name. All-caps addresses are accepted with full street-type words and the
  abbreviations that do not double as clinical shorthand (``ST``, ``DR``,
  ``CT``, ``LA``, ``PL``, ``GR`` and ``ESP`` are excluded in capitals).

Known limitations: a year directly after a state whose ranges contain it
(``NSW 2019``) is tagged as a postcode, and a state/postcode mismatch
(``NSW 3052``) is not tagged at all.
"""

from __future__ import annotations

import re
from typing import Dict, List

from .au_records import pipeline_layout_view
from .pii_entity_merger import PIIPattern

AU_PHONE = "AU_PHONE"
AU_PHONE_INSTITUTIONAL = "AU_PHONE_INSTITUTIONAL"
AU_POSTCODE = "AU_POSTCODE"
AU_STATE = "AU_STATE"
AU_ADDRESS = "AU_ADDRESS"

# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

_PHONE_STRIP_RE = re.compile(r"[\s\-.·,()]")
_NATIONAL_RE = re.compile(r"0(?:4\d{8}|[2378][2-9]\d{7})")
_LOCAL_RE = re.compile(r"[2-9]\d{7}")
_INSTITUTIONAL_RE = re.compile(r"13\d{4}|1[38]00\d{6}")


def validate_australian_phone(text: str) -> bool:
    """Validate an Australian mobile, geographic or local phone/fax number.

    Accepted after removing spaces, hyphens, dots, middle dots, commas and
    parentheses: a ten-digit national number (mobile ``04`` or geographic
    ``02``/``03``/``07``/``08`` followed by a local number starting
    ``2``-``9``), the same number as ``+61`` with the leading ``0`` dropped
    (an explicit ``(0)`` or a stray ``0`` after ``+61`` is tolerated), or a
    bare eight-digit local number starting ``2``-``9``.

    Args:
        text: Candidate number as printed.

    Returns:
        True when the digits form a valid Australian number of those kinds.
    """
    candidate = _PHONE_STRIP_RE.sub("", text.strip())
    if candidate.startswith("+61"):
        rest = candidate[3:]
        national = rest if rest.startswith("0") else "0" + rest
        return _NATIONAL_RE.fullmatch(national) is not None
    if not candidate.isdigit():
        return False
    return bool(_NATIONAL_RE.fullmatch(candidate) or _LOCAL_RE.fullmatch(candidate))


def validate_australian_institutional_phone(text: str) -> bool:
    """Validate an Australian ``13``/``1300``/``1800`` number.

    These are six-digit numbers beginning ``13`` and ten-digit numbers
    beginning ``1300`` or ``1800``. Spaces and hyphens are ignored.

    Args:
        text: Candidate number as printed.

    Returns:
        True when the digits form one of those institutional numbers.
    """
    candidate = _PHONE_STRIP_RE.sub("", text.strip())
    return candidate.isdigit() and _INSTITUTIONAL_RE.fullmatch(candidate) is not None


# Never start inside a word, after ``+`` or one separator away from a digit;
# never stop right before a digit or a word character.
_PHONE_START = r"(?<![\w+])(?<!\d[ -])"
_PHONE_END = r"(?![ -]?\d)(?!\w)"

_MOBILE = r"04(?:\d{2}[ -]?\d{3}[ -]?\d{3}|[ -]?\d{4}[ -]?\d{4})"
_GEOGRAPHIC = r"(?:\(0[2378]\) ?|0[2378][ -]?)[2-9]\d{3}[ -]?\d{4}"
_INTERNATIONAL = (
    r"\+61[ -]?(?:\(0\)[ -]?)?"
    r"(?:4(?:\d{2}[ -]?\d{3}[ -]?\d{3}|[ -]?\d{4}[ -]?\d{4})"
    r"|[2378][ -]?[2-9]\d{3}[ -]?\d{4})"
)

# Field labels that make a bare eight-digit local number a phone/fax number.
# ``F:`` and ``T:`` only count with their colon.
_PHONE_LABEL = (
    r"(?i:\b(?:telephone|phone|tel|ph|mobile|mob|fax|contact)\b\.?"
    r"(?:[ \t]*(?:no|number)\b\.?)?|\b[ft](?=[ \t]*:))"
)

_PHONE_CONTEXT = [
    "phone",
    "telephone",
    "tel",
    "ph:",
    "ph.",
    "mobile",
    "mob",
    "cell",
    "fax",
    "contact",
    "call",
]
_INSTITUTIONAL_CONTEXT = [
    "phone",
    "telephone",
    "tel",
    "call",
    "contact",
    "fax",
    "mobile",
    "freecall",
    "hotline",
    "enquiries",
    "bookings",
]

# ---------------------------------------------------------------------------
# States, territories and postcodes
# ---------------------------------------------------------------------------

#: Postcode ranges per state/territory as regexes over four digits.
STATE_POSTCODE_RANGES: Dict[str, str] = {
    "NSW": (r"(?:1\d{3}|2[0-5]\d{2}|2619|26[2-9]\d|2[78]\d{2}|292[1-9]|29[3-9]\d)"),
    "ACT": r"(?:02\d{2}|260\d|261[0-8]|29[01]\d|2920)",
    "VIC": r"(?:[38]\d{3})",
    "QLD": r"(?:[49]\d{3})",
    "SA": r"(?:5\d{3})",
    "WA": r"(?:6\d{3})",
    "TAS": r"(?:7\d{3})",
    "NT": r"(?:0[89]\d{2})",
}

# State names and abbreviations, longest first within each state. Mixed-case
# abbreviations may carry their period, which is part of the span.
_STATE_NAMES: Dict[str, str] = {
    "NSW": r"New South Wales|NEW SOUTH WALES|N\.S\.W\.|NSW",
    "ACT": (
        r"Australian Capital Territory|AUSTRALIAN CAPITAL TERRITORY"
        r"|A\.C\.T\.|ACT"
    ),
    "VIC": r"Victoria|VICTORIA|VIC|Vic\.?",
    "QLD": r"Queensland|QUEENSLAND|QLD|Qld\.?",
    "SA": r"South Australia|SOUTH AUSTRALIA|S\.A\.|SA",
    "WA": r"Western Australia|WESTERN AUSTRALIA|W\.A\.|WA",
    "TAS": r"Tasmania|TASMANIA|TAS|Tas\.?",
    "NT": r"Northern Territory|NORTHERN TERRITORY|N\.T\.|NT",
}

_STATE_START = r"(?<![A-Za-z0-9.'’-])"
_STATE_END = r"(?![A-Za-z])"
_STATE_ANY = (
    "(?:" + "|".join(_STATE_NAMES[state] for state in _STATE_NAMES) + ")" + _STATE_END
)
_POSTCODE_ANY = r"(?:0[289]\d{2}|[1-9]\d{3})"
# Measurement units that make a number a dose, distance, mass or duration.
_UNIT_GUARD = (
    r"(?![ \t]*(?:mg|mcg|ug|g|kg|ml|mL|L|IU|units?|mmol|mol|mm|cm|m|km"
    r"|ms|s|secs?|seconds?|mins?|minutes?|h|hrs?|hours?)\b)(?![ \t]*%)"
)
# A postcode never runs into more digits, a decimal or a unit.
_POSTCODE_END = rf"(?!\d)(?![.,]\d){_UNIT_GUARD}"
_STATE_POSTCODE = (
    _STATE_START
    + "(?:"
    + "|".join(
        f"(?:{_STATE_NAMES[state]})"
        f"(?=,?[ \\t]+{STATE_POSTCODE_RANGES[state]}{_POSTCODE_END})"
        for state in _STATE_NAMES
    )
    + ")"
)

# ---------------------------------------------------------------------------
# Street and postal addresses
# ---------------------------------------------------------------------------

_UNIT_WORDS = (
    r"Unit|Apartment|Apt|Flat|Suite|Shop|Villa|Level|Lvl|Lot"
    r"|UNIT|APARTMENT|APT|FLAT|SUITE|SHOP|VILLA|LEVEL|LOT"
)
_UNIT_PREFIX = rf"(?:\b(?:{_UNIT_WORDS})\.?[ \t]?\d{{1,4}}[A-Za-z]?,?[ \t]+)"
_HOUSE_NUMBER = (
    r"(?<![\w/.-])(?:\d{1,4}[A-Za-z]?/)?\d{1,5}[A-Za-z]?(?:-\d{1,5}[A-Za-z]?)?"
)
_NAME_TITLE = r"(?:O['’]|Mc|Mac|D['’])?[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
_NAME_UPPER = r"(?:O['’])?[A-Z]{2,}(?:-[A-Z]{2,})?"
_TYPE_TITLE = (
    r"Street|St|Road|Rd|Avenue|Ave|Crescent|Cres|Parade|Pde|Highway|Hwy"
    r"|Court|Ct|Place|Pl|Terrace|Tce|Drive|Dr|Boulevard|Blvd|Circuit|Cct"
    r"|Esplanade|Esp|Grove|Gr|Lane|La|Way"
)
_TYPE_UPPER = (
    r"STREET|ROAD|RD|AVENUE|AVE|CRESCENT|CRES|PARADE|PDE|HIGHWAY|HWY"
    r"|COURT|PLACE|TERRACE|TCE|DRIVE|BOULEVARD|BLVD|CIRCUIT|CCT"
    r"|ESPLANADE|GROVE|LANE|WAY"
)
# A rural ``Lot N`` stands in for the house number.
_LOT_NUMBER = r"\b(?:Lot|LOT)\.?[ \t]?\d{1,5}[A-Za-z]?"
_STREET = (
    rf"(?:{_UNIT_PREFIX}?{_HOUSE_NUMBER}|{_LOT_NUMBER})[ \t]+"
    rf"(?:{_NAME_TITLE}(?:[ \t]+{_NAME_TITLE}){{0,2}}[ \t]+(?:{_TYPE_TITLE})"
    rf"|{_NAME_UPPER}(?:[ \t]+{_NAME_UPPER}){{0,2}}[ \t]+(?:{_TYPE_UPPER}))\b"
)
_POSTAL = (
    r"\b(?:(?:P\.?[ \t]?O\.?|G\.?P\.?O\.?)[ \t]?(?:Box|BOX)"
    r"|Locked[ \t]+Bag|LOCKED[ \t]+BAG|Private[ \t]+Bag|PRIVATE[ \t]+BAG"
    r"|R\.?M\.?B\.?)[ \t]*\d{1,6}[A-Z]?\b"
)
_ADDRESS_CORE = rf"(?:{_STREET}|{_POSTAL})"
_SUBURB_WORD = rf"(?!{_STATE_ANY})[A-Z][A-Za-z'’-]+"
_SUBURB = rf"{_SUBURB_WORD}(?:[ \t]+{_SUBURB_WORD}){{0,2}}"
_SUBURB_END = rf"(?=,?\s+(?:{_STATE_ANY}|{_POSTCODE_ANY}{_POSTCODE_END}))"

_ADDRESS_CONTEXT = [
    "address",
    "street",
    "resides",
    "residence",
    "lives at",
    "located at",
    "home",
    "postal",
    "mail",
    "suburb",
]
_POSTCODE_CONTEXT = [
    "postcode",
    "post code",
    "address",
    "suburb",
    "resides",
    "lives",
    "home",
    "mail",
]

AU_CONTACT_PII_PATTERNS: List[PIIPattern] = [
    # Mobile, geographic and +61 numbers. These shapes start with ``0`` or
    # ``+``, so they never collide with the checksum identifiers, and the
    # priority keeps a ``+61`` mobile from being read as an ABN or Medicare
    # number. Fax lines use the same rules. First AU_PHONE pattern: the
    # safety sweep validates OCR-split phones with its validator.
    PIIPattern(
        rf"{_PHONE_START}(?:{_INTERNATIONAL}|{_MOBILE}|{_GEOGRAPHIC}){_PHONE_END}",
        "phone_number",
        priority=15,
        flags=0,
        base_score=0.6,
        context_boost=0.35,
        context_words=list(_PHONE_CONTEXT),
        validator=validate_australian_phone,
        reject_on_validation_failure=True,
        subtype=AU_PHONE,
    ),
    # The same shapes in a table row. The ``Pipeline`` collapses the column
    # gap between cells to one space, so a phone whose next cell starts with
    # a digit (``(02) 5550 1234        123456AB``) reads as part of a longer
    # digit run above. This copy matches the run's source layout instead,
    # where the gap reads as a tab and so ends the number; a single space
    # still joins digits into one longer run. Outside a ``Pipeline`` run it
    # matches nothing.
    PIIPattern(
        rf"{_PHONE_START}(?:{_INTERNATIONAL}|{_MOBILE}|{_GEOGRAPHIC}){_PHONE_END}",
        "phone_number",
        priority=15,
        flags=0,
        base_score=0.6,
        context_boost=0.35,
        context_words=list(_PHONE_CONTEXT),
        validator=validate_australian_phone,
        reject_on_validation_failure=True,
        match_normalizer=pipeline_layout_view,
        subtype=AU_PHONE,
    ),
    # Bare eight-digit local number, only directly after a phone/fax label.
    # The label stays outside the ``value`` span.
    PIIPattern(
        rf"{_PHONE_LABEL}[ \t]*[:#]?[ \t]*"
        rf"(?P<value>(?<!\d)[2-9]\d{{3}}[ -]?\d{{4}}){_PHONE_END}",
        "phone_number",
        priority=15,
        flags=0,
        base_score=0.85,
        context_boost=0.1,
        context_words=list(_PHONE_CONTEXT),
        validator=validate_australian_phone,
        reject_on_validation_failure=True,
        subtype=AU_PHONE,
    ),
    # 1300 / 1800 numbers.
    PIIPattern(
        rf"{_PHONE_START}1[38]00(?:[ -]?\d{{3}}[ -]?\d{{3}}"
        rf"|[ -]\d{{2}}[ -]\d{{2}}[ -]\d{{2}}){_PHONE_END}",
        "phone_number",
        priority=15,
        flags=0,
        base_score=0.6,
        context_boost=0.3,
        context_words=list(_INSTITUTIONAL_CONTEXT),
        validator=validate_australian_institutional_phone,
        reject_on_validation_failure=True,
        subtype=AU_PHONE_INSTITUTIONAL,
    ),
    # Six-digit 13 numbers are short enough to be counts or scores, so they
    # need nearby phone context. Spaces only: ``13-11-14`` reads as a date.
    PIIPattern(
        rf"{_PHONE_START}13 ?\d{{2}} ?\d{{2}}(?! ?\d)(?!\w){_UNIT_GUARD}",
        "phone_number",
        priority=15,
        flags=0,
        base_score=0.5,
        context_boost=0.4,
        context_words=list(_INSTITUTIONAL_CONTEXT),
        requires_context=True,
        validator=validate_australian_institutional_phone,
        reject_on_validation_failure=True,
        subtype=AU_PHONE_INSTITUTIONAL,
    ),
    # Street or postal address as one span, extended over the suburb only
    # when a state or postcode follows. Outranks the generic street pattern.
    PIIPattern(
        rf"{_ADDRESS_CORE}(?:,?\s+{_SUBURB}{_SUBURB_END})?",
        "street_address",
        priority=8,
        flags=0,
        base_score=0.8,
        context_boost=0.15,
        context_words=list(_ADDRESS_CONTEXT),
        subtype=AU_ADDRESS,
    ),
    # Postcode directly after a state/territory whose ranges contain it.
    PIIPattern(
        rf"{_STATE_POSTCODE},?[ \t]+(?P<value>{_POSTCODE_ANY}){_POSTCODE_END}",
        "postcode",
        priority=8,
        flags=0,
        base_score=0.85,
        context_boost=0.1,
        context_words=list(_POSTCODE_CONTEXT),
        subtype=AU_POSTCODE,
    ),
    # Postcode after the suburb of a detected street or postal address.
    PIIPattern(
        rf"{_ADDRESS_CORE},?\s+{_SUBURB},?[ \t]+"
        rf"(?P<value>{_POSTCODE_ANY}){_POSTCODE_END}",
        "postcode",
        priority=8,
        flags=0,
        base_score=0.85,
        context_boost=0.1,
        context_words=list(_POSTCODE_CONTEXT),
        subtype=AU_POSTCODE,
    ),
    # State/territory directly before a postcode in its ranges ...
    PIIPattern(
        _STATE_POSTCODE,
        "location",
        priority=8,
        flags=0,
        base_score=0.8,
        context_boost=0.1,
        context_words=list(_POSTCODE_CONTEXT),
        subtype=AU_STATE,
    ),
    # ... or after a detected street or postal address (and its suburb).
    PIIPattern(
        rf"{_ADDRESS_CORE}(?:,?\s+{_SUBURB})?,?\s+(?P<value>{_STATE_ANY})",
        "location",
        priority=8,
        flags=0,
        base_score=0.8,
        context_boost=0.1,
        context_words=list(_POSTCODE_CONTEXT),
        subtype=AU_STATE,
    ),
]
