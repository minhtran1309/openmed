"""Australian hospital record numbers and report header values.

Two families of label-anchored patterns live here because they share the AU
report field-label vocabulary: record numbers (``AU_UR``) and the person,
birth-date and address values of a report header (``AU_HEADER``).

Record numbers (``AU_UR``)
--------------------------

Australian hospitals identify a patient by a UR (unit record) number, and
pathology and radiology systems add episode, visit, accession, lab and
specimen numbers. None of these carries a checksum, so a value is detected
only directly after one of the field labels in
:data:`AU_RECORD_CONTEXT_WORDS`.

The pattern here is appended to the ``en_AU`` locale pattern list
(``openmed.core.pii_i18n.LOCALE_PII_PATTERNS["en_au"]``), so it reaches
``extract_pii`` smart merging, the deterministic safety sweep and the
``Pipeline`` deterministic stage whenever the AU locale is in effect.

The raw label is ``medical_record_number`` (canonical ``ID_NUM``) and every
match carries ``metadata["subtype"] == "AU_UR"``, whichever label precedes it.

Design rules:

* Spans are values only. The field label stays outside the named ``value``
  group.
* The short labels ``UR``, ``URN``, ``U.R.`` and ``MRN`` are case-sensitive;
  the multi-word labels (``Hospital No``, ``Lab No`` ...) and ``Accession``
  are case-insensitive. ``UR No`` wins over ``UR``. A label never starts or
  ends inside a longer word (``your``, ``Urgent``).
* ``Episode`` and ``Visit`` count only before an explicit ``:``, ``#``,
  ``No`` or ``Number`` (``Episode No: 12345``), so ``prior episode 2019`` is
  not a record number.
* The separator is optional spaces with at most one ``:`` or ``#``. A bare
  ``.`` is never a separator (it only appears inside ``No.`` and ``U.R.``),
  so a label cannot reach across a sentence end.
* Values are case-sensitive: up to three capital letters, then digits, with
  optional hyphens or ``NNN NNN NNN`` / ``NNN NNN NNNN`` style spacing.
* Priority 13 and base score 0.9 put a labelled value above the unlabelled
  TFN and ACN checksum patterns, so ``Accession 123 456 782`` is ``AU_UR``.

Header values (``AU_HEADER``)
-----------------------------

A pathology or radiology report header lists the patient and the clinicians
as ``Label: value`` fields. The value after a known label maps to an existing
raw label:

* ``Patient Name``, ``Name``, ``NOK``/``Next of Kin`` and the clinician labels
  ``Referring Doctor``, ``Referred by``, ``Reported by``, ``Requesting
  Clinician`` and ``GP`` give ``name`` (``PERSON``). A ``Dr``/``Dr.``/``Prof``
  title stays outside the value.
* ``Surname`` gives ``last_name``; ``Given Name(s)`` gives ``first_name``.
* ``D.O.B``, ``DOB`` and ``Date of Birth`` give ``date_of_birth``.
* ``Address`` gives ``street_address``.

``UR``/``URN`` are the record-number pattern above; ``Medicare No`` and the
``Ph``/``Phone``/``Mob``/``Mobile``/``Fax`` labels use the existing Medicare
and AU phone patterns.

Design rules:

* Spans are values only; the label and title stay outside ``value``.
* A label counts only before a ``:``, a tab or two or more spaces (the
  report-header layout), so prose such as ``seen by GP today`` or ``Name of
  drug: aspirin`` never matches. A birth-date label also takes a single
  space, because its value must be a date. ``GP``, ``NOK`` and ``DOB`` are
  case-sensitive; the other labels are case-insensitive, except a bare
  ``Name``, which counts only as ``Name`` or ``NAME`` and never after a
  non-person noun (``brand name:``, ``Drug Name:``, ``Test Name:`` ...).
* The ``Pipeline`` collapses tabs, line breaks and repeated spaces to one
  space before detection. :func:`bind_source_layout` lets these patterns see
  the source layout again for the text of one run: a collapsed line break
  reads as ``\n`` and a collapsed tab or column gap as ``\t`` (same length,
  so offsets are unchanged). A value therefore stops at the source line end
  in the ``Pipeline`` too, and never takes the next line's heading word.
* A name is one to four capitalised or all-caps words (hyphens and
  apostrophes allowed) separated by single spaces, as ``Jane CITIZEN`` or
  ``CITIZEN, Jane``. A known field label or a word followed by ``:`` starts
  the next field, and a tab or two spaces ends the value. A placeholder
  (``Unknown``, ``Nil``, ``N/A``, ``NA``, ``None``, ``Not stated``, ``Not
  known``) is not a name.
* An address starts with a capital letter or digit, contains a digit and
  runs to the end of the line (at most 120 characters), so ``Address: see
  referral`` is not an address; nor is a ``Ward``, ``Bed`` or ``Room``
  location. It ends early at a ``STATE POSTCODE`` pair,
  and stops before a column gap, a sentence end, a word in lower case or the
  next known field label, so prose that the ``Pipeline`` joins onto the
  line is not taken. A header address is one span: the street, state and
  postcode patterns do not split it.
* Priority 14 and a labelled score of 1.0 put a header value above the
  street, state, postcode and generic date patterns on the same text, so a
  header address is one span and a header birth date stays
  ``DATE_OF_BIRTH``.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, List, Sequence

from .pii_entity_merger import PIIPattern

AU_UR = "AU_UR"

#: The field labels from story 14 that introduce a record number.
AU_RECORD_CONTEXT_WORDS: tuple[str, ...] = (
    "UR",
    "URN",
    "UR No",
    "U.R.",
    "MRN",
    "Hospital No",
    "Patient ID",
    "Episode",
    "Visit No",
    "Accession",
    "Lab No",
    "Specimen No",
)


_NO = r"(?i:No\.?)"
# Short labels: case-sensitive, so prose ``ur``/``mrn`` never counts.
_SHORT_LABELS = rf"UR[ \t]+{_NO}|URN|U\.R\.|UR|MRN"
# Multi-word labels: case-insensitive.
_FIELD_LABELS = (
    rf"(?i:Hospital[ \t]+{_NO}|Patient[ \t]+ID|Lab[ \t]+{_NO}"
    rf"|Specimen[ \t]+{_NO}|Accession)"
)
# ``Episode`` and ``Visit`` are common prose words, so they count only before
# an explicit ``:``/``#`` or a ``No``/``Number`` label.
_PROSE_LABELS = r"(?i:(?:Episode|Visit)[ \t]*(?:[:#]|(?:Number|No\.?)(?![A-Za-z])))"
_LABEL = (
    rf"(?<![A-Za-z0-9])"
    rf"(?:(?:{_SHORT_LABELS}|{_FIELD_LABELS})(?![A-Za-z])|{_PROSE_LABELS})"
)
# A bare ``.`` is never a separator; it is only part of ``No.`` and ``U.R.``.
_SEPARATOR = r"[ \t]*[:#]?[ \t]*"
_VALUE = (
    r"(?:\d{3} \d{3} \d{4}(?![ ]?\d)"
    r"|\d{3}(?: \d{3}){1,3}(?![ ]?\d)"
    r"|[A-Z]{0,3}\d[\d-]{3,11}\d"
    r"|\d{4,12})"
    r"(?![A-Za-z0-9])"
)

AU_RECORD_PII_PATTERNS: List[PIIPattern] = [
    PIIPattern(
        rf"{_LABEL}{_SEPARATOR}(?P<value>{_VALUE})",
        "medical_record_number",
        priority=13,
        flags=0,
        base_score=0.9,
        context_boost=0.1,
        context_words=[word.casefold() for word in AU_RECORD_CONTEXT_WORDS],
        requires_context=False,
        subtype=AU_UR,
    ),
]


# ---------------------------------------------------------------------------
# Report header values
# ---------------------------------------------------------------------------

AU_HEADER = "AU_HEADER"


# ---------------------------------------------------------------------------
# Source layout for one Pipeline run
# ---------------------------------------------------------------------------


@dataclass
class _SourceLayout:
    normalized_text: str
    original_text: str
    normalized_to_original_span: Sequence[tuple[int, int]]
    _layout_text: str | None = field(default=None, repr=False)

    def layout_text(self) -> str:
        if self._layout_text is None:
            chars = list(self.normalized_text)
            for index, char in enumerate(chars):
                if char != " ":
                    continue
                start, end = self.normalized_to_original_span[index]
                source = self.original_text[start:end]
                if "\n" in source or "\r" in source:
                    chars[index] = "\n"
                elif "\t" in source or end - start > 1:
                    chars[index] = "\t"
            self._layout_text = "".join(chars)
        return self._layout_text


_SOURCE_LAYOUT: ContextVar[_SourceLayout | None] = ContextVar(
    "au_header_source_layout", default=None
)


@contextmanager
def source_layout_scope() -> Iterator[None]:
    """Scope one ``Pipeline`` run; the bound layout is dropped on exit.

    Yields:
        Nothing. Call :func:`bind_source_layout` inside the scope.
    """
    token = _SOURCE_LAYOUT.set(None)
    try:
        yield
    finally:
        _SOURCE_LAYOUT.reset(token)


def bind_source_layout(
    normalized_text: str,
    original_text: str,
    normalized_to_original_span: Sequence[tuple[int, int]],
) -> None:
    """Let the AU header patterns see the source layout of one run's text.

    The ``Pipeline`` collapses each whitespace run to one space. Inside a
    :func:`source_layout_scope`, when an AU header pattern is matched against
    exactly ``normalized_text``, it matches a copy in which a space that
    replaced a line break reads ``\\n`` and one that replaced a tab or a
    column gap reads ``\\t``. The copy has the same length, so spans need no
    remapping. Other patterns and other texts are unaffected.

    Args:
        normalized_text: The text the detectors receive.
        original_text: The source text before whitespace collapsing.
        normalized_to_original_span: For each normalized character, its
            ``(start, end)`` source span.
    """
    _SOURCE_LAYOUT.set(
        _SourceLayout(normalized_text, original_text, normalized_to_original_span)
    )


@dataclass(frozen=True)
class _SameLengthText:
    text: str

    def remap_span(self, start: int, end: int) -> tuple[int, int]:
        return start, end


def _restore_source_layout(text: str) -> _SameLengthText:
    layout = _SOURCE_LAYOUT.get()
    if layout is None or (
        text is not layout.normalized_text and text != layout.normalized_text
    ):
        return _SameLengthText(text)
    return _SameLengthText(layout.layout_text())


#: The story 16 header labels whose values this module detects. ``UR``/``URN``
#: (record numbers), ``Medicare No`` and the phone labels are matched by their
#: own patterns.
AU_HEADER_CONTEXT_WORDS: tuple[str, ...] = (
    "Patient Name",
    "Name",
    "Surname",
    "Given Name",
    "Given Names",
    "Given Name(s)",
    "D.O.B",
    "DOB",
    "Date of Birth",
    "Address",
    "NOK",
    "Next of Kin",
    "Referring Doctor",
    "Referred by",
    "Reported by",
    "Requesting Clinician",
    "GP",
)

_GAP = r"[ \t]+"
# A bare ``Name`` is a label only as ``Name`` or ``NAME`` (``brand name:`` is
# prose), and never directly after a non-person noun (``Drug Name:``).
_NOT_PERSON_NAME_NOUNS = (
    "drug",
    "medication",
    "medicine",
    "test",
    "specimen",
    "hospital",
    "facility",
    "clinic",
    "practice",
    "ward",
    "product",
    "brand",
    "study",
    "trial",
    "procedure",
    "device",
    "organisation",
    "organization",
    "company",
    "program",
    "programme",
    "file",
    "report",
    "sample",
    "unit",
    "department",
    "service",
    "site",
)
# Python look-behinds are fixed width, so there is one per noun.
_NOT_AFTER_NOUN = "".join(rf"(?<!(?i:{noun})[ \t])" for noun in _NOT_PERSON_NAME_NOUNS)
_PATIENT_NAME_CORE = rf"(?i:Patient{_GAP}Name|Next{_GAP}of{_GAP}Kin)|NOK"
_PATIENT_NAME_LABELS = rf"{_PATIENT_NAME_CORE}|{_NOT_AFTER_NOUN}(?:Name|NAME)"
_CLINICIAN_NAME_LABELS = (
    rf"(?i:Referring{_GAP}Doctor|Referred{_GAP}by|Reported{_GAP}by"
    rf"|Requesting{_GAP}Clinician)|GP"
)
_SURNAME_LABELS = r"(?i:Surname)"
_GIVEN_NAME_LABELS = rf"(?i:Given{_GAP}Names?)(?:\((?i:s)\))?"
_DOB_LABELS = rf"(?i:Date{_GAP}of{_GAP}Birth)|DOB|Dob|D\.O\.B\.?"
_ADDRESS_LABELS = r"(?i:Address)"


# A header label needs a ``:``, a tab or two spaces after it (report-header
# layout), never a single space (prose). A birth-date label also accepts a
# single space: its value must be a date, so prose cannot match.
_HEADER_SEPARATOR = r"(?:[ \t]*:[ \t]*|\t[ \t]*|[ ]{2,}[ \t]*)"
_DATE_SEPARATOR = r"(?:[ \t]*:[ \t]*|[ \t]+)"

# Every field label that can follow a value on the same line. The Pipeline
# collapses tabs, newlines and repeated spaces to one space before
# detection, so a value must stop in front of the next label by name too.
# Example: in ``Name: Jane CITIZEN DOB 04/07/1961`` the name stops before
# ``DOB``; in ``Address: 12 Smith St URN: 0012345`` before ``URN``.
_RECORD_LABELS = "|".join(
    re.escape(word).replace(r"\ ", "[ ]")
    for word in sorted(AU_RECORD_CONTEXT_WORDS, key=len, reverse=True)
)
_ANY_LABEL = (
    rf"(?:{_PATIENT_NAME_CORE}|Name|NAME|{_CLINICIAN_NAME_LABELS}"
    rf"|{_SURNAME_LABELS}|{_GIVEN_NAME_LABELS}|{_DOB_LABELS}|{_ADDRESS_LABELS}"
    rf"|{_RECORD_LABELS}"
    r"|(?i:Medicare(?:[ ]No\.?)?|Phone|Mobile|Tel|Fax|Email|Sex)|Ph|Mob"
    r")(?![A-Za-z])"
)

_TITLE = r"(?:(?:Dr|DR|Prof|PROF)(?:\.[ \t]*|[ \t]+))"
# A capitalised or all-caps word, with hyphens and apostrophes. A known
# label, a title or a word followed by ``:`` (or ``/``) is not part of it.
_NAME_WORD = (
    rf"(?!{_ANY_LABEL}|(?:Dr|DR|Prof|PROF)(?![A-Za-z]))"
    r"[A-Z][A-Za-z]*(?:['’-][A-Z]?[A-Za-z]+)*(?![A-Za-z0-9'’/-])(?![ \t]*:)"
)
# A whole-value placeholder (``Unknown``, ``Nil``, ``N/A`` ...) is not a name.
_PLACEHOLDER = (
    r"(?i:unknown|nil|n/a|na|none|not[ ]stated|not[ ]known|-)"
    r"(?![A-Za-z0-9'’/-])(?![ ,]+[A-Z])"
)
# ``Jane CITIZEN`` or ``CITIZEN, Jane``: one to four words, single spaces.
_NAME_VALUE = (
    rf"(?!{_PLACEHOLDER}){_NAME_WORD}(?:,?[ ]{_NAME_WORD})?"
    rf"(?:[ ]{_NAME_WORD}){{0,2}}"
)

_MONTH = r"(?i:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_DOB_VALUE = (
    r"(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}(?:/\d{1,2}/|-\d{1,2}-|\.\d{1,2}\.)(?:\d{4}|\d{2})"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?[ -]{_MONTH}[ -](?:\d{{4}}|\d{{2}})"
    rf"|{_MONTH}[ ]\d{{1,2}}(?:st|nd|rd|th)?,?[ ]\d{{4}})"
    r"(?!\d)(?![./-]\d)(?![A-Za-z])"
)

_STATE = (
    r"(?:New South Wales|Victoria|Queensland|South Australia|Western Australia"
    r"|Tasmania|Northern Territory|Australian Capital Territory"
    r"|N\.S\.W\.|A\.C\.T\.|S\.A\.|W\.A\.|N\.T\."
    r"|NSW|ACT|VIC|Vic|QLD|Qld|SA|WA|TAS|Tas|NT)"
)
# An address character is never a tab, a line break, a ``:`` or a ``;``. It
# never starts a column gap, a sentence end (``. ``), the next field label
# (``... 2031 Ph: ...``) or a word that starts in lower case, so prose after
# the address (which the Pipeline joins onto the same line) is not taken.
# Example: ``Address: 3/12 Smith St. Next of kin notified`` stops at ``St``.
_ADDRESS_CHAR = (
    rf"(?:(?![ ]{{2}}|\.[ ]|[ ](?![A-Z0-9(])|[ ]+{_ANY_LABEL}[ \t]*[:#])"
    r"[^\t\r\n:;])"
)
# Starts with a capital or digit and is at most 120 characters long. It ends
# at the first ``STATE POSTCODE`` (``Randwick NSW 2031``) when one follows;
# otherwise it runs to the end of the line, must contain a digit and ends on
# a letter, digit or ``)``. Example: ``Address: 12 Smith St, Randwick NSW
# 2031 Collected 10am`` gives ``12 Smith St, Randwick NSW 2031``.
_ADDRESS_VALUE = (
    r"(?=[A-Z0-9])(?!(?i:ward|bed|room)(?![A-Za-z]))"
    rf"(?:(?:{_ADDRESS_CHAR}){{0,110}}?(?<![A-Za-z.]){_STATE},?[ ]\d{{4}}(?!\d)"
    rf"|(?=(?:{_ADDRESS_CHAR}){{0,119}}?\d)(?:{_ADDRESS_CHAR}){{0,119}}"
    r"[A-Za-z0-9)])"
)


def _header_pattern(
    labels: str,
    value: str,
    entity_type: str,
    words: tuple[str, ...],
    *,
    separator: str = _HEADER_SEPARATOR,
) -> PIIPattern:
    # A label never starts or ends inside a word; a name may carry a title
    # between the label and the value.
    title = _TITLE + "?" if entity_type == "name" else ""
    return PIIPattern(
        rf"(?<![A-Za-z0-9])(?:{labels})(?![A-Za-z]){separator}{title}"
        rf"(?P<value>{value})",
        entity_type,
        priority=14,
        flags=0,
        base_score=0.95,
        context_boost=0.05,
        context_words=[word.casefold() for word in words],
        requires_context=False,
        match_normalizer=_restore_source_layout,
        subtype=AU_HEADER,
    )


_CLINICIAN_NAME_PATTERN = _header_pattern(
    _CLINICIAN_NAME_LABELS,
    _NAME_VALUE,
    "name",
    (
        "Referring Doctor",
        "Referred by",
        "Reported by",
        "Requesting Clinician",
        "GP",
    ),
)

AU_HEADER_PII_PATTERNS: List[PIIPattern] = [
    _header_pattern(
        _PATIENT_NAME_LABELS,
        _NAME_VALUE,
        "name",
        ("Patient Name", "Name", "NOK", "Next of Kin"),
    ),
    _CLINICIAN_NAME_PATTERN,
    _header_pattern(_SURNAME_LABELS, _NAME_VALUE, "last_name", ("Surname",)),
    _header_pattern(
        _GIVEN_NAME_LABELS,
        _NAME_VALUE,
        "first_name",
        ("Given Name", "Given Names", "Given Name(s)"),
    ),
    _header_pattern(
        _DOB_LABELS,
        _DOB_VALUE,
        "date_of_birth",
        ("D.O.B", "DOB", "Date of Birth"),
        separator=_DATE_SEPARATOR,
    ),
    _header_pattern(_ADDRESS_LABELS, _ADDRESS_VALUE, "street_address", ("Address",)),
]


def au_header_role(pattern: PIIPattern) -> str:
    """Return the clinical role of the person a header pattern identifies.

    Args:
        pattern: One of :data:`AU_HEADER_PII_PATTERNS`.

    Returns:
        ``"clinician"`` for the referring, reporting, requesting and GP name
        labels; ``"patient"`` for every other header value (the patient's own
        fields and the next of kin).
    """
    return "clinician" if pattern is _CLINICIAN_NAME_PATTERN else "patient"
