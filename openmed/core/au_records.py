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
  are case-insensitive. ``Hospital``, ``Lab`` and ``Specimen`` take ``No``,
  ``No.`` or ``Number``; ``Accession`` and ``Patient ID`` take one
  optionally (``Accession No: 12345678``, ``Patient ID Number 12345``).
  A research ``Participant`` takes ``ID``, ``No`` or ``Number``, and a
  ``Subject`` takes ``ID``. ``Trial ID`` and ``Study ID`` are not labels:
  they usually hold a public registry or protocol number.
  ``UR No`` wins over ``UR``. A label never starts or ends inside a longer
  word (``your``, ``Urgent``).
* ``Episode`` and ``Visit`` count only before an explicit ``:``, ``#``,
  ``No`` or ``Number`` (``Episode No: 12345``), so ``prior episode 2019`` is
  not a record number.
* The separator is optional spaces with at most one ``:`` or ``#``. A bare
  ``.`` is never a separator (it only appears inside ``No.`` and ``U.R.``),
  so a label cannot reach across a sentence end.
* Values are case-sensitive: up to three capital letters (optionally
  followed by a hyphen), then digits, with optional hyphens or ``NNN NNN
  NNN`` / ``NNN NNN NNNN`` style spacing, then an optional ``-`` and check
  letter (``MRN: 1234567-K``).
* An EMR running title line (``CITIZEN, Jane Ann - 12345678-K``) has no
  field label: the all-caps surname, the comma, the title-case or all-caps
  given names and the spaced dash act as the label. The value is six to ten
  digits with an optional check letter; a bare ten-digit AU mobile or
  geographic number is a phone, not a record. The line is matched against
  the source layout, so the given names never span a line break, though a
  column gap may follow the comma or surround the dash.
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
  ``Name``, which counts only as ``Name`` or ``NAME`` and never directly
  after a non-person noun and one space (``brand name:``, ``Drug Name:``,
  ``Test Name:`` ...). A noun followed by a column gap (a tab or two or more
  spaces in the source) ends the previous cell and does not block it.
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

Print footer user ids (``AU_PRINT_FOOTER``)
-------------------------------------------

An EMR print footer names the user who printed the record and their login:
``Printed by CITIZEN, JANE [CITIZENJ] at 01/02/2026 3:04:05 PM``. The
bracketed login after ``Printed by`` and a name is ``username``
(``USERNAME``). A bracketed value anywhere else (``Final [99]``, a citation
``[12]``) is not.

Labelled values repeated without their label
--------------------------------------------

:func:`propagate_labelled_values` runs in the ``Pipeline`` deterministic
stage for the AU locale. Once a birth date is detected after its ``DOB``
label (``AU_HEADER``) or a record number after its label (``AU_UR``), every
other exact, token-bounded copy of the same text in the run is that value
too: a running header ``Jane CITIZEN 04/07/1961`` becomes
``DATE_OF_BIRTH`` rather than ``DATE``, and a table cell under an ``MRN``
heading becomes ``ID_NUM``. A value that is never labelled in the run is
not propagated, so an unlabelled header date stays ``DATE``.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterator, List, Mapping, Sequence

from .labels import (
    DATE_OF_BIRTH,
    EMAIL,
    FIRST_NAME,
    ID_NUM,
    LAST_NAME,
    MEDICARE_NUMBER,
    PERSON,
    PHONE,
    STREET_ADDRESS,
)
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
    "Participant ID",
    "Participant No",
    "Subject ID",
)


_NO = r"(?i:No\.?)"
# ``No``, ``No.`` or ``Number`` after a multi-word field label.
_NUMBER_WORD = rf"(?:Number|{_NO})"
# Short labels: case-sensitive, so prose ``ur``/``mrn`` never counts.
_SHORT_LABELS = rf"UR[ \t]+{_NO}|URN|U\.R\.|UR|MRN"
# Multi-word labels: case-insensitive. ``Hospital``, ``Lab`` and ``Specimen``
# need the number word; ``Accession`` and ``Patient ID`` take it optionally.
_FIELD_LABELS = (
    rf"(?i:Hospital[ \t]+{_NUMBER_WORD}"
    rf"|Patient[ \t]+ID(?:[ \t]+{_NUMBER_WORD})?"
    rf"|Lab[ \t]+{_NUMBER_WORD}"
    rf"|Specimen[ \t]+{_NUMBER_WORD}"
    rf"|Participant[ \t]+(?:ID|{_NUMBER_WORD})"
    rf"|Subject[ \t]+ID"
    rf"|Accession(?:[ \t]+{_NUMBER_WORD})?)"
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
    r"|(?:[A-Z]{1,3}-?)?\d[\d-]{3,11}\d(?:-[A-Z])?"
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


def pipeline_layout_view(text: str) -> _SameLengthText:
    """Return the bound source layout of ``text``, or text nothing matches.

    A ``match_normalizer`` for patterns that only make sense on the source
    layout of one ``Pipeline`` run (see :func:`bind_source_layout`). Inside
    the run, the text reads as the layout copy (``\n`` for a collapsed line
    break, ``\t`` for a collapsed tab or column gap). For any other text, or
    outside a run, it reads as NUL characters of the same length, so the
    pattern finds nothing and ``extract_pii`` and the stand-alone safety
    sweep behave exactly as without it.

    Args:
        text: The text a pattern is about to be matched against.

    Returns:
        A same-length view whose spans need no remapping.
    """
    layout = _SOURCE_LAYOUT.get()
    if layout is None or (
        text is not layout.normalized_text and text != layout.normalized_text
    ):
        return _SameLengthText("\0" * len(text))
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


def table_header_key(header: str) -> str:
    """Normalise a table header the way column classification keys it.

    Matches ``openmed.multimodal.tabular_csv``'s header key: lowercase, with
    every non-alphanumeric character removed, so ``U.R.`` and ``UR`` agree.

    Args:
        header: The header text as it appears in the table.

    Returns:
        The normalised key, for example ``"givennames"`` for ``Given Name(s)``.
    """
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


_TABLE_PERSON_HEADERS = (
    "Patient Name",
    "Name",
    "NOK",
    "Next of Kin",
    "Referring Doctor",
    "Referred by",
    "Reported by",
    "Requesting Clinician",
    "GP",
)

#: AU table header text -> canonical label, for ``classify_columns``'
#: ``header_heuristics``. A column is classified from its header only.
AU_TABLE_HEADER_LABELS: Mapping[str, str] = MappingProxyType(
    {
        **{word: ID_NUM for word in AU_RECORD_CONTEXT_WORDS},
        **{word: PERSON for word in _TABLE_PERSON_HEADERS},
        "Surname": LAST_NAME,
        "Given Name": FIRST_NAME,
        "Given Names": FIRST_NAME,
        "Given Name(s)": FIRST_NAME,
        "D.O.B": DATE_OF_BIRTH,
        "DOB": DATE_OF_BIRTH,
        "Date of Birth": DATE_OF_BIRTH,
        "Address": STREET_ADDRESS,
        "Medicare No": MEDICARE_NUMBER,
        "Ph": PHONE,
        "Phone": PHONE,
        "Mob": PHONE,
        "Mobile": PHONE,
        "Tel": PHONE,
        "Fax": PHONE,
        "Email": EMAIL,
    }
)

#: Normalised header key -> subtype. Only record-number columns get one: a
#: header proves which field a cell is, not that its value passes a checksum.
AU_TABLE_HEADER_SUBTYPES: Mapping[str, str] = MappingProxyType(
    {
        **{table_header_key(word): AU_UR for word in AU_RECORD_CONTEXT_WORDS},
        # Generic record-number headers that tabular_csv already maps to ID_NUM.
        "mrn": AU_UR,
        "patientid": AU_UR,
    }
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
    # Imaging, nuclear medicine, laboratory and report-template nouns.
    "scanner",
    "contrast",
    "protocol",
    "sequence",
    "modality",
    "equipment",
    "machine",
    # ``Agent Name`` alone can be a person (``Health Care Agent Name``), so
    # only a drug-type agent is a non-person noun.
    "contrast agent",
    "imaging agent",
    "diagnostic agent",
    "stress agent",
    "sedation agent",
    "sedative agent",
    "anaesthetic agent",
    "anesthetic agent",
    "therapeutic agent",
    "chemotherapy agent",
    "chemotherapeutic agent",
    "radiopharmaceutical agent",
    "tracer",
    "radiopharmaceutical",
    "isotope",
    "examination",
    "exam",
    "series",
    "template",
    "form",
    "camera",
    "coil",
    "kit",
    "instrument",
    "analyser",
    "analyzer",
    "station",
)
# Python look-behinds are fixed width, so there is one per noun. The noun
# blocks only when it is part of the same label: one space before ``Name``.
# Header patterns match the source layout, where a column gap reads ``\t``,
# so in ``MEDICAL IMAGING REPORT      NAME:  CITIZEN, JANE`` the noun is the
# previous cell's last word and the label is a bare ``NAME:``.
_NOT_AFTER_NOUN = "".join(rf"(?<!(?i:{noun})[ ])" for noun in _NOT_PERSON_NAME_NOUNS)
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


# ---------------------------------------------------------------------------
# EMR running title line: ``SURNAME, Given Middle - <MRN>-<check letter>``
# ---------------------------------------------------------------------------

# An EMR result print repeats the patient on a title line as an all-caps
# surname, a comma, title-case given names, a spaced dash and the record
# number, often with a check letter (``CITIZEN, Jane Ann - 12345678-K``). The
# name is the label: without it a dashed number is never a record number.
_TITLE_SURNAME = r"[A-Z][A-Z'’-]*[A-Z]"
# Title-case (``Jane``) or all-caps (``JANE``) given names.
_TITLE_GIVEN = r"(?:[A-Z][a-z]+(?:['’-][A-Z]?[a-z]+)*|[A-Z][A-Z'’-]*[A-Z])"
# A bare ten-digit AU mobile or geographic number is a phone, not a record.
_TITLE_NOT_PHONE = r"(?!0[23478]\d{8}(?![\d-]))"
_TITLE_MRN = rf"{_TITLE_NOT_PHONE}\d{{6,10}}(?:-[A-Z])?"

AU_RECORD_PII_PATTERNS.append(
    PIIPattern(
        rf"(?<![A-Za-z0-9'’-]){_TITLE_SURNAME}(?:[ ]{_TITLE_SURNAME})?,"
        rf"[ \t]+{_TITLE_GIVEN}(?:[ ]{_TITLE_GIVEN}){{0,3}}[ \t]+[-–][ \t]+"
        rf"(?P<value>{_TITLE_MRN})(?![A-Za-z0-9])(?![-/.][A-Za-z0-9])",
        "medical_record_number",
        priority=13,
        flags=0,
        base_score=0.9,
        context_boost=0.1,
        context_words=[word.casefold() for word in AU_RECORD_CONTEXT_WORDS],
        requires_context=False,
        match_normalizer=_restore_source_layout,
        subtype=AU_UR,
    )
)


# ---------------------------------------------------------------------------
# Print footer user id: ``Printed by <NAME> [<USERID>]``
# ---------------------------------------------------------------------------

AU_PRINT_FOOTER = "AU_PRINT_FOOTER"

# A print footer names the user who printed the record, then that user's
# login in square brackets (``Printed by CITIZEN, JANE [CITIZENJ] at ...``).
# Only the bracketed login is a value here; the name is left to the model.
_FOOTER_NAME_WORD = r"[A-Z][A-Za-z'’-]*"
_FOOTER_NAME = rf"{_FOOTER_NAME_WORD}(?:,?[ ]{_FOOTER_NAME_WORD}){{0,4}}"
_FOOTER_USER_ID = r"[A-Za-z][A-Za-z0-9._-]{1,31}"

#: ``Printed by <NAME> [<USERID>]`` print footer user ids (raw label
#: ``username``, canonical ``USERNAME``).
AU_PRINT_FOOTER_PII_PATTERNS: List[PIIPattern] = [
    PIIPattern(
        rf"(?<![A-Za-z])(?i:Printed)[ ](?i:by):?[ ]{_FOOTER_NAME}[ ]?"
        rf"\[(?P<value>{_FOOTER_USER_ID})\]",
        "username",
        priority=14,
        flags=0,
        base_score=0.95,
        context_boost=0.05,
        context_words=["printed by"],
        requires_context=False,
        match_normalizer=_restore_source_layout,
        subtype=AU_PRINT_FOOTER,
    ),
]


# ---------------------------------------------------------------------------
# Labelled values repeated without their label
# ---------------------------------------------------------------------------

# Raw label -> the subtype that marks it as a labelled AU value.
_PROPAGATED_SUBTYPES: Mapping[str, str] = MappingProxyType(
    {"date_of_birth": AU_HEADER, "medical_record_number": AU_UR}
)
# Raw label -> the raw label an exact same-span finding may be relabelled from.
_RELABELLED_FROM: Mapping[str, frozenset[str]] = MappingProxyType(
    {"date_of_birth": frozenset({"date"}), "medical_record_number": frozenset()}
)
#: A record number shorter than this many letters and digits is not copied:
#: four or five digits can be a year, a postcode, a time or a count.
MIN_PROPAGATED_RECORD_LENGTH = 6


def _occurrences(view: str, surface: str) -> Iterator[tuple[int, int]]:
    # Never inside a longer word, number, date or code. An all-digit value
    # also never continues across one space into more digits.
    guard = r"(?<!\d[ ])" if surface.isdigit() else ""
    tail = r"(?![ ]\d)" if surface.isdigit() else ""
    pattern = re.compile(
        rf"(?<![A-Za-z0-9])(?<![A-Za-z0-9][-/.]){guard}"
        rf"{re.escape(surface)}"
        rf"(?![A-Za-z0-9])(?![-/.][A-Za-z0-9]){tail}"
    )
    for match in pattern.finditer(view):
        yield match.span()


def propagate_labelled_values(
    text: str,
    entities: Sequence[object],
    patterns: Sequence[PIIPattern],
) -> Sequence[object]:
    """Carry a labelled birth date or record number to its unlabelled copies.

    A report repeats the patient's values without their labels: a running
    header shows ``Jane CITIZEN 04/07/1961`` after the ``DOB:`` field, and a
    table cell under an ``MRN`` column heading repeats the ``MRN# 1234567``
    in the title. Once a value is detected after its own label
    (``AU_HEADER`` birth date or ``AU_UR`` record number), every other exact,
    token-bounded occurrence of the same surface in the document is that
    value too:

    * an occurrence already detected as a plain ``date`` with exactly the
      same span becomes ``date_of_birth``;
    * an occurrence nothing covers is added with the labelled value's label,
      subtype and confidence;
    * an occurrence that overlaps any other finding is left alone.

    A record number labelled with a check letter (``UR: 1234567-K``) also
    covers bare copies of its digit stem (``1234567``). A record number or
    stem needs at least :data:`MIN_PROPAGATED_RECORD_LENGTH` letters and
    digits. Nothing changes unless ``patterns`` holds the AU
    record-number patterns, so other locales are untouched.

    Args:
        text: The text the entities index.
        entities: Deterministic findings (``EntityPrediction``-like objects
            with ``start``, ``end``, ``label``, ``confidence`` and
            ``metadata``).
        patterns: The pattern list the findings came from.

    Returns:
        ``entities`` itself when nothing changes, otherwise a new list sorted
        by offset. Values are compared in memory only and never logged.
    """
    pattern_ids = {id(pattern) for pattern in patterns}
    if not any(id(pattern) in pattern_ids for pattern in AU_RECORD_PII_PATTERNS):
        return entities

    anchors: dict[str, object] = {}
    for entity in entities:
        label = getattr(entity, "label", None)
        metadata = getattr(entity, "metadata", None) or {}
        if label not in _PROPAGATED_SUBTYPES:
            continue
        if metadata.get("subtype") != _PROPAGATED_SUBTYPES[label]:
            continue
        surface = text[entity.start : entity.end]
        if label == "medical_record_number" and (
            sum(char.isalnum() for char in surface) < MIN_PROPAGATED_RECORD_LENGTH
        ):
            continue
        anchors.setdefault(surface, entity)
        # ``UR: 1234567-K`` also covers a bare ``1234567`` elsewhere.
        stem = re.fullmatch(r"(.+\d)-[A-Z]", surface)
        if label == "medical_record_number" and stem is not None:
            body = stem.group(1)
            if sum(char.isalnum() for char in body) >= MIN_PROPAGATED_RECORD_LENGTH:
                anchors.setdefault(body, entity)
    if not anchors:
        return entities

    from ..processing.outputs import EntityPrediction

    view = _restore_source_layout(text).text
    result = list(entities)
    changed = False
    for surface, anchor in anchors.items():
        label = anchor.label
        for start, end in _occurrences(view, surface):
            overlapping = [
                index
                for index, entity in enumerate(result)
                if entity.start < end and entity.end > start
            ]
            if overlapping and not (
                len(overlapping) == 1
                and (result[overlapping[0]].start, result[overlapping[0]].end)
                == (start, end)
                and result[overlapping[0]].label in _RELABELLED_FROM[label]
            ):
                continue
            propagated = EntityPrediction(
                text=text[start:end],
                label=label,
                start=start,
                end=end,
                confidence=anchor.confidence,
                metadata={
                    **dict(anchor.metadata or {}),
                    "propagated_from_label": True,
                },
            )
            if overlapping:
                result[overlapping[0]] = propagated
            else:
                result.append(propagated)
            changed = True
    if not changed:
        return entities
    return sorted(result, key=lambda entity: (entity.start, entity.end))
