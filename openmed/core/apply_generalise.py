"""Generalisers for the ``generalise`` apply treatment (mt.3 E10).

Each function is pure and returns the coarser rendering, or ``None`` when the
value cannot be generalised safely; the caller then falls back to a
``[LABEL_n]`` tag. Every function fails closed: an input it does not
recognise exactly is never partly rendered.

* :func:`generalise_phone` keeps the AU area/mobile prefix and masks the rest.
* :func:`generalise_dob` keeps the year of the *shifted* date of birth.
* :func:`generalise_address` keeps ``<suburb> <state>`` from an AU address
  tail, dropping the street and postcode.
* :func:`generalise_organisation` keeps the organisation kind (``hospital``).

Cancer generalisation lives in :mod:`openmed.core.oncotree`.
"""

from __future__ import annotations

import re
from typing import Final

from .apply_spans import DATE_LOCALE, shift_full_date
from .au_contact import validate_australian_phone
from .locale_formats import parse_date

__all__ = [
    "AU_STATES",
    "generalise_address",
    "generalise_dob",
    "generalise_organisation",
    "generalise_phone",
]

AU_STATES: Final[frozenset[str]] = frozenset(
    {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}
)
_AU_STATE_NAMES: Final = (
    "New South Wales",
    "Victoria",
    "Queensland",
    "South Australia",
    "Western Australia",
    "Tasmania",
    "Northern Territory",
    "Australian Capital Territory",
)
# Mixed-case abbreviations that are not also common words (``Sa``/``Wa``).
_AU_STATE_MIXED_CASE: Final = ("Vic", "Qld", "Tas")

# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------


def generalise_phone(value: str) -> str | None:
    """Keep an AU phone number's prefix and mask every later digit.

    National numbers keep their first two digits (``04``, ``02``...);
    ``+61`` numbers keep ``+61``, an optional trunk ``0`` and the next
    digit. Separators are kept. A bare eight-digit local number has no
    prefix to keep and returns ``None``.

    Args:
        value: The phone number as printed.

    Returns:
        For example ``04xx xxx xxx`` or ``+61 4xx xxx xxx``, or ``None``
        when ``value`` is not a valid AU mobile or geographic number.
    """
    if not validate_australian_phone(value):
        return None
    core = value.strip()
    if core.startswith("+61"):
        prefix, rest = "+61", core[3:]
        keep_trunk_zero = True
        keep_digits = 1
    else:
        if not core.lstrip("(").startswith("0"):
            return None
        prefix, rest = "", core
        keep_trunk_zero = False
        keep_digits = 2
    out: list[str] = [prefix]
    kept = 0
    for char in rest:
        if not char.isdigit():
            out.append(char)
        elif keep_trunk_zero and kept == 0 and char == "0":
            out.append(char)
        elif kept < keep_digits:
            out.append(char)
            kept += 1
        else:
            out.append("x")
    return "".join(out)


# ---------------------------------------------------------------------------
# Date of birth
# ---------------------------------------------------------------------------

_TRAILING_YEAR_RE: Final = re.compile(r"(?<!\d)(\d{4})\s*$")
_FOUR_DIGIT_YEAR_RE: Final = re.compile(r"(?<!\d)\d{4}(?!\d)")
_MIN_YEAR: Final = 1000


def generalise_dob(value: str, offset: int) -> str | None:
    """Return the year of the date of birth after the date shift.

    The year is taken from the shifted date, so ages stay consistent with
    the shifted document dates and the true year is not restated. A year
    written with fewer than four digits (``12/03/48``) returns ``None``:
    the century would be a guess, and ``48`` would read as 2048, a DOB in
    the future. The caller then falls back to a tag.

    Args:
        value: A full date (day-first numeric, ISO, or English month name)
            with a four-digit year.
        offset: The patient's date-shift offset in days.

    Returns:
        The four-digit shifted year, or ``None`` for a value that is not a
        full date with a four-digit year from 1000 on (year-only,
        month-year, two-digit year or unparseable).
    """
    if _FOUR_DIGIT_YEAR_RE.search(value) is None:
        return None
    shifted = shift_full_date(value, offset)
    if shifted is None:
        return None
    core = shifted.strip()
    parsed = parse_date(core, lang=DATE_LOCALE)
    if parsed.normalized is not None:
        year = parsed.normalized[0]
    else:
        # English month-name forms end with the year (``4 July 2015``,
        # ``July 4, 2015``); ``shift_full_date`` already validated the date.
        match = _TRAILING_YEAR_RE.search(core)
        if match is None:
            return None
        year = int(match.group(1))
    return str(year) if year >= _MIN_YEAR else None


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

_STATE_ALT: Final = "|".join(
    sorted(
        [
            *AU_STATES,
            *_AU_STATE_MIXED_CASE,
            *_AU_STATE_NAMES,
            *(name.upper() for name in _AU_STATE_NAMES),
        ],
        key=len,
        reverse=True,
    )
)
_STATE_ONLY_RE: Final = re.compile(rf"(?:{_STATE_ALT})")
_TAIL_RE: Final = re.compile(
    rf"(?P<body>[\s\S]+?)(?:[ \t]*[,\n][ \t]*|[ \t]+)(?P<state>{_STATE_ALT})"
    r"(?:[ \t]*,?[ \t]*\d{4})?"
)
_SUBURB_WORD_RE: Final = re.compile(r"[A-Za-z][A-Za-z'’.-]*")
# Australia Post / AS 4590 street types, full names and abbreviations.
_STREET_TYPES: Final = frozenset(
    word.casefold()
    for word in (
        "Access Accs Alley Ally Alleyway Alwy Amble Ambl Anchorage Anch "
        "Approach App Arcade Arc Artery Art Avenue Ave Av Basin Basn Beach Bch "
        "Bend Bnd Block Blk Boulevard Bvd Blvd Boulevarde Bvde Brace Brce Brae "
        "Break Brk Bridge Bdge Broadway Bdwy Brow Bypass Bypa Byway Bywy "
        "Causeway Cswy Centre Ctr Centreway Cnwy Chase Ch Circle Cir Circlet "
        "Cclt Circuit Cct Circus Crcs Close Cl Colonnade Clde Common Cmmn "
        "Concourse Con Copse Cps Corner Cnr Corso Cso Court Ct Courtyard Ctyd "
        "Cove Crescent Cres Crest Cst Cross Crss Crossing Crsg Crossroad Crd "
        "Crossway Cowy Cruiseway Cuwy Cul-de-sac Cds Cutting Cttg Dale Dell "
        "Deviation Devn Dip Distributor Dstr Drive Dr Drv Driveway Drwy Edge "
        "Elbow Elb End Entrance Ent Esplanade Esp Estate Est Expressway Exp "
        "Extension Extn Fairway Fawy Firetrail Fitr Firetrack Ftrk Flat Follow "
        "Folw Footway Ftwy Foreshore Fshr Formation Form Freeway Fwy Front Frnt "
        "Frontage Frtg Gap Garden Gdn Gardens Gdns Gate Gte Gates Gtes Glade "
        "Gld Glen Grange Gra Green Grn Ground Grnd Grove Gr Gv Gully Gly "
        "Heights Hts Highroad Hrd Highway Hwy Hill Interchange Intg "
        "Intersection Intn Junction Jnc Key Landing Ldg Lane Ln La Laneway "
        "Lnwy Lees Line Link Little Lt Lookout Lkt Loop Lower Lwr Mall Meander "
        "Mndr Mew Mews Motorway Mwy Mount Mt Nook Outlook Otlk Parade Pde Park "
        "Parklands Pkld Parkway Pkwy Part Pass Path Pathway Phwy Piazza Piaz "
        "Place Pl Plateau Plat Plaza Plza Pocket Pkt Point Pnt Port Promenade "
        "Prom Quad Quadrangle Qdgl Quadrant Qdrt Quay Qy Quays Qys Ramble Rmbl "
        "Ramp Range Rnge Reach Rch Reserve Res Rest Retreat Rtt Ride Ridge Rdge "
        "Ridgeway Rgwy Ring Rise River Rvr Riverway Rvwy Riviera Rvra Road Rd "
        "Roads Rds Roadside Rdsd Roadway Rdwy Ronde Rnde Rosebowl Rsbl Rotary "
        "Rty Round Rnd Route Rte Row Rue Run Siding Sdng Slope Slpe Sound Snd "
        "Spur Square Sq Stairs Strs Steps Stps Strand Stra Street St Strip Strp "
        "Subway Sbwy Tarn Terrace Tce Thoroughfare Thor Tollway Twy Top Tor "
        "Towers Twrs Track Trk Trail Trl Trailer Trlr Triangle Tri Trunkway "
        "Tkwy Turn Underpass Upas Upper Upr Vale Viaduct Vdct View Villas Vlls "
        "Vista Vsta Wade Walk Walkway Wkwy Way Wharf Whrf Wynd Yard"
    ).split()
)
# Street-type words that commonly open a suburb name (``St Kilda``,
# ``Mount Waverley``); they are allowed as a suburb's first word only.
_SUBURB_OPENERS: Final = frozenset(
    {"st", "mount", "mt", "glen", "point", "port", "upper", "lower", "little"}
)
# Postal and rural-property prefixes never yield a suburb (fail closed).
_POSTAL_PREFIX_RE: Final = re.compile(
    r"(?:P\.?[ \t]*O\.?|G\.?[ \t]*P\.?[ \t]*O\.?|Post[ \t]+Office)[ \t]*Box\b"
    r"|(?:Locked|Private)[ \t]+Bag\b|R\.?[ \t]*M\.?[ \t]*B\b\.?|RSD\b|CMB\b"
    r"|Roadside[ \t]+(?:Mail|Delivery)\b|Lot\b|Farm\b|C/[-O]",
    re.IGNORECASE,
)
_UNIT_PREFIX_RE: Final = re.compile(
    r"(?:Unit|Flat|Apartment|Apt|Suite|Shop|Villa|Level|Lvl)\b", re.IGNORECASE
)
_HOUSE_NUMBER_RE: Final = re.compile(
    r"\d{1,5}[A-Za-z]?(?:[ \t]*[/-][ \t]*\d{1,5}[A-Za-z]?)?"
)
_MAX_SUBURB_WORDS: Final = 4


def _is_street_type(word: str) -> bool:
    return word.rstrip(".").casefold() in _STREET_TYPES


def _street_end(words: list[str]) -> int | None:
    """Index just past a leading street part, or ``None`` if there is none.

    A street part is an optional unit (``Unit 4``), a house number
    (``12``, ``4/12``), one or more name words and a street type
    (``Smith St``). The first street type after the name ends it.
    """
    count = len(words)
    index = 0
    unit = count > 0 and _UNIT_PREFIX_RE.fullmatch(words[0]) is not None
    if unit:
        index = 1
    if index >= count or _HOUSE_NUMBER_RE.fullmatch(words[index]) is None:
        return None
    index += 1
    if unit and index < count and _HOUSE_NUMBER_RE.fullmatch(words[index]):
        index += 1
    for end in range(index + 1, count):
        if not _SUBURB_WORD_RE.fullmatch(words[end - 1]):
            return None
        if _is_street_type(words[end]):
            return end + 1
    return None


def _is_street_segment(segment: str) -> bool:
    """``12 Smith St`` / ``Unit 4, 12 Smith Street``: exactly a street part."""
    words = segment.split()
    return bool(words) and _street_end(words) == len(words)


def _suburb_ok(words: list[str]) -> bool:
    if not 1 <= len(words) <= _MAX_SUBURB_WORDS:
        return False
    if not all(_SUBURB_WORD_RE.fullmatch(word) for word in words):
        return False
    if not words[0][0].isupper():
        return False
    first = words[0].rstrip(".").casefold()
    if _is_street_type(words[0]) and first not in _SUBURB_OPENERS:
        return False
    return not any(_is_street_type(word) for word in words[1:])


def _suburb_after_street(body: str) -> list[str] | None:
    segments = [part.strip() for part in re.split(r"[,\n]", body)]
    if not all(segments):
        return None
    words = segments[-1].split()
    end = _street_end(words)
    if end is not None:
        # ``12 Smith St Carlton``: the suburb follows the street type.
        return words[end:]
    if len(segments) >= 2 and _is_street_segment(segments[-2]):
        # ``12 Smith St, Carlton``: the street is the preceding segment.
        return words
    return None


def generalise_address(value: str) -> str | None:
    """Reduce an AU address to ``<suburb> <STATE>``, dropping the postcode.

    A suburb is rendered only when a street part comes directly before it:
    a house number (optionally after a unit, ``Unit 4/12``) with a name and
    an Australia Post street type, either in the preceding comma- or
    line-separated segment (``12 Smith St, Fitzroy VIC 3065``) or in the
    same segment (``Unit 4/12 Smith Street Fitzroy VIC 3065``). The suburb
    is the words between the street part and the state; it must be one to
    four words of letters, with no street type except a known opener as its
    first word (``St Kilda``, ``Mount Waverley``). A value that is only a
    state is returned unchanged. Applies to ``STREET_ADDRESS`` and
    ``LOCATION``.

    Everything else returns ``None`` so the caller renders a tag (fail
    closed): a postcode alone does not identify a suburb (``Jane Doe VIC
    3053``), nor does a bare ``<words>, STATE`` (``John Smith, VIC``);
    postal and rural prefixes (PO/GPO Box, Locked/Private Bag, RMB, Lot) and
    a unit with no street are rejected; and suburbs that contain a
    street-type word (``Box Hill``, ``Lane Cove``) are accepted
    over-redaction.

    Args:
        value: The address or location text.

    Returns:
        For example ``Fitzroy VIC``, the state alone, or ``None`` when no
        safe AU street/suburb/state tail is found.
    """
    core = value.strip()
    if not core:
        return None
    if _STATE_ONLY_RE.fullmatch(core):
        return core
    if _POSTAL_PREFIX_RE.match(core):
        return None
    match = _TAIL_RE.fullmatch(core)
    if match is None:
        return None
    words = _suburb_after_street(match.group("body"))
    if words is None or not _suburb_ok(words):
        return None
    return f"{' '.join(words)} {match.group('state')}"


# ---------------------------------------------------------------------------
# Organisation
# ---------------------------------------------------------------------------

_ORGANISATION_KINDS: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE), kind)
    for pattern, kind in (
        ("hospital", "hospital"),
        (r"health[ \t]+service", "health service"),
        ("clinic", "clinic"),
        (r"medical[ \t]+cent(?:re|er)", "medical centre"),
        (r"general[ \t]+practice", "general practice"),
        ("practice", "practice"),
        ("pathology", "pathology"),
        ("radiology|imaging", "radiology"),
        ("laboratory", "laboratory"),
        ("pharmacy", "pharmacy"),
        ("school", "school"),
        ("university", "university"),
    )
)


def generalise_organisation(value: str) -> str | None:
    """Reduce an organisation name to its kind.

    Keywords are matched case-insensitively on word boundaries, in table
    order, so ``Hospital Pharmacy`` is a ``hospital``.

    Args:
        value: The organisation name.

    Returns:
        The lowercase kind (``hospital``, ``medical centre``...), or
        ``None`` when no keyword matches.
    """
    for pattern, kind in _ORGANISATION_KINDS:
        if pattern.search(value):
            return kind
    return None
