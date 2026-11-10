"""Day-first Australian date forms (ticket 07, story 15).

Australian clinical text writes dates day-first. The generic English date
patterns already detect ``04/07/2023``, ``4-7-23``, ``4.7.2023``,
``4 July 2023`` and ``July 4, 2023``; the patterns here add the day-first forms
they miss:

* a dotted two-digit year, as ``4.7.23`` or ``04.07.23``;
* a day and month name with an ordinal, ``of``, a ``-`` separator, a comma or
  a two-digit year, as ``04-Jul-23``, ``04-JUL-2023``, ``4th July 2023``,
  ``4th of July 2023``, ``4 Jul 23`` or ``22nd Feb. 2024``.

The patterns are appended to the ``en_AU`` locale pattern list
(``openmed.core.pii_i18n.LOCALE_PII_PATTERNS["en_au"]``), so they reach
``extract_pii`` smart merging, the deterministic safety sweep and the
``Pipeline`` deterministic stage whenever the AU locale is in effect. The raw
label is ``date`` (canonical ``DATE``) with no subtype.

Design rules:

* Every match is validated with :func:`openmed.core.locale_formats.parse_date`
  read in the ``en-au`` day-first order, so an impossible date (``31.2.23``,
  ``4.13.23``, ``29.2.23``) is dropped rather than kept at a lower score.
* A dotted value is never part of a longer dotted or digit run: a version
  (``v4.7.23``, ``4.7.23.1``), an IP address (``10.4.7.23``) or a decimal
  (``4.7``) is not a date.
* A dotted value that is a valid day-first date is tagged even after a lab
  word (``eGFR 4.7.23 trend``): the text cannot tell a result from a date, so
  detection fails closed.
* A month name is capitalised or all caps (``Jul``, ``JUL``), so a lower-case
  word such as ``may`` in ``5 may 20 units`` is not a month.
* Every detected surface is one :func:`openmed.core.apply_spans.shift_full_date`
  accepts, so a ``DATE`` span shifts with its format preserved. ``4th of July
  2023`` is the exception: it is detected but falls back to a tag.
* A report-header birth date (``D.O.B: 04-Jul-23``) keeps its higher-priority
  ``date_of_birth`` (``AU_HEADER``) span.
"""

from __future__ import annotations

import re
from typing import List

from .locale_formats import parse_date
from .pii_entity_merger import PIIPattern

AU_DATE_LOCALE = "en-au"

_MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
# Full names before abbreviations; ``Sept`` before ``Sep``. Each name is
# capitalised or all caps, never lower case.
_MONTH_FORMS = (
    *_MONTH_NAMES,
    "Sept",
    *(name[:3] for name in _MONTH_NAMES),
)
_MONTH = (
    "(?:"
    + "|".join(f"{form}|{form.upper()}" for form in _MONTH_FORMS)
    + r")(?![A-Za-z])\.?"
)

# Never inside a word, a number or a dotted run; never followed by more
# digits or by another separator and digit.
_START = r"(?<![\w.])"
_END = r"(?!\d)(?![./-]\d)(?![A-Za-z])"

_DOTTED_TWO_DIGIT_YEAR = rf"{_START}\d{{1,2}}\.\d{{1,2}}\.\d{{2}}{_END}"
_DAY_MONTH_NAME_YEAR = (
    rf"{_START}\d{{1,2}}(?:st|nd|rd|th|ST|ND|RD|TH)?"
    rf"(?:[ ]of[ ]|[ ]OF[ ]|[ ]|-){_MONTH}(?:,?[ ]|-)(?:\d{{4}}|\d{{2}}){_END}"
)

_DATE_PARTS_RE = re.compile(
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:\s+of\s+|\s+|-|\.)"
    r"(?P<month>\d{1,2}|[A-Za-z]+)\.?(?:,?\s+|-|\.)(?P<year>\d{4}|\d{2})",
    re.IGNORECASE,
)

_DATE_CONTEXT_WORDS = [
    "dob",
    "birth",
    "born",
    "date of birth",
    "birthdate",
    "deceased",
    "died",
    "admitted",
    "discharged",
    "collected",
    "seen",
    "date",
]


def validate_au_day_first_date(text: str) -> bool:
    """Return whether ``text`` is a real calendar date read day-first.

    Args:
        text: A day-first date surface such as ``"4.7.23"``,
            ``"04-Jul-23"`` or ``"4th of July 2023"``.

    Returns:
        ``True`` when the day, month and year form a valid Gregorian date in
        the ``en-au`` day/month/year order; ``False`` otherwise.
    """
    match = _DATE_PARTS_RE.fullmatch(text.strip())
    if match is None:
        return False
    month = match.group("month")
    if not month.isdigit():
        number = _MONTH_NUMBERS.get(month[:3].lower())
        if number is None:
            return False
        month = str(number)
    candidate = f"{match.group('day')}/{month}/{match.group('year')}"
    return parse_date(candidate, lang=AU_DATE_LOCALE).normalized is not None


def _date_pattern(pattern: str) -> PIIPattern:
    return PIIPattern(
        pattern,
        "date",
        priority=9,
        flags=0,
        base_score=0.6,
        context_words=list(_DATE_CONTEXT_WORDS),
        context_boost=0.3,
        validator=validate_au_day_first_date,
        reject_on_validation_failure=True,
    )


AU_DATE_PII_PATTERNS: List[PIIPattern] = [
    _date_pattern(_DAY_MONTH_NAME_YEAR),
    _date_pattern(_DOTTED_TWO_DIGIT_YEAR),
]
