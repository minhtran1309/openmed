"""Australian hospital record numbers after a field label (``AU_UR``).

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
"""

from __future__ import annotations

from typing import List

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
