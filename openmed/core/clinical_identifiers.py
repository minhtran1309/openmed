"""Context-anchored identifiers in German and English clinical headers.

These rules supplement model candidates. They do not establish language or
clinical qualification and never treat a capitalized word alone as a name.
"""

from __future__ import annotations

import re
from datetime import date

from openmed.processing.outputs import EntityPrediction

from .au_records import AU_HEADER_PII_PATTERNS, au_header_role
from .labels import (
    DATE_OF_BIRTH,
    ID_NUM,
    PERSON,
    PHONE,
    STREET_ADDRESS,
    normalize_label,
)
from .pii_entity_merger import iter_pattern_spans

CLINICAL_IDENTIFIER_VERSION = "clinical-identifiers-de-en-v3"
# Carried only by the Australian header rules, so every other entity (and the
# whole output of a non-AU call) keeps ``CLINICAL_IDENTIFIER_VERSION``.
AU_HEADER_RULES_VERSION = f"{CLINICAL_IDENTIFIER_VERSION}+au-header-v1"
_WORD = r"[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:[\u0300-\u036f][^\W\d_]*)*(?:[’'-][^\W\d_]+)*"
_INITIAL = r"[A-ZÀ-ÖØ-Þ]\."
_MULTIWORD_FIELD = (
    r"(?i:date[ \t]+of[ \t]+birth|medical[ \t]+record[ \t]+(?:number|no\.)|"
    r"patient[ \t]+(?:id|name)|blood[ \t]+pressure|heart[ \t]+rate)"
)
# A following field is not part of a name. The trailing word boundary prevents
# regex backtracking from retaining a truncated prefix of its heading.
_PART = (
    rf"(?!{_MULTIWORD_FIELD}[ \t]*:)(?:{_INITIAL}|{_WORD})"
    rf"(?![\w’'-]|[ \t]*:)"
)
_NAME = rf"{_PART}(?:[ \t]+(?:(?:von|van|de|der|den|zu|zur)[ \t]+)?{_PART}){{0,5}}"
_TITLE = r"(?:(?i:dr|prof)\.[ \t]*(?:(?i:med)\.[ \t]*)?)"
_PATIENT = re.compile(
    rf"\b(?i:patient(?:in)?(?:[ \t]+name)?|patientenname|name|vorname|nachname)"
    rf"[ \t]*:[ \t]*(?:{_TITLE})?(?P<value>{_NAME})"
)
_CLINICIAN = re.compile(
    rf"\b(?i:arzt|ärztin|behandelnder[ \t]+arzt|behandelnde[ \t]+ärztin|"
    rf"doctor|physician|clinician|surgeon)[ \t]*:[ \t]*(?:{_TITLE})?"
    rf"(?P<value>{_NAME})"
)
_DOCTOR_TITLE = re.compile(rf"\b{_TITLE}(?P<value>{_NAME})")
_DOB = re.compile(
    r"\b(?i:geboren(?:[ \t]+am)?|geburtsdatum|geb\.|date[ \t]+of[ \t]+birth|"
    r"dob|born(?:[ \t]+on)?)[ \t]*:?[ \t]*"
    r"(?P<value>\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})\b"
)
_ID = re.compile(
    r"\b(?i:patienten[- ]?(?:id|nummer)|fallnummer|versichertennummer|"
    r"patient[ \t]+id|medical[ \t]+record[ \t]+(?:number|no\.)|mrn)"
    r"[ \t]*[:#][ \t]*(?P<value>[A-Za-z0-9][A-Za-z0-9/-]{2,63})\b"
)
_PHONE = re.compile(
    r"\b(?i:phone|telephone|telefon|tel\.|fax|mobile|mobil|handy)"
    r"[ \t]*:[ \t]*(?P<value>\+?\d[\d ()/.-]{5,30}\d)(?!\d)"
)
_ADDRESS_WORD = r"(?:[^\W\d_]|[\u0300-\u036f])+(?:[’'-](?:[^\W\d_]|[\u0300-\u036f])+)*"
_CITY = (
    rf"(?:(?i:bad|sankt|st\.)[ \t]+)?{_WORD}"
    rf"(?:[ \t]+(?i:am|im|bei|an[ \t]+der|vor[ \t]+der|ob[ \t]+der)[ \t]+{_WORD}){{0,2}}"
)
_GERMAN_POSTAL_ADDRESS = re.compile(
    r"\b(?i:anschrift|adresse|wohnadresse|patientenadresse)[ \t]*:[ \t]*"
    rf"(?P<value>(?:{_ADDRESS_WORD}[ \t]+){{0,3}}{_ADDRESS_WORD}[ \t]+"
    r"\d{1,4}[a-zA-Z]?(?:[-/]\d{1,4}[a-zA-Z]?)?[ \t]*,[ \t]*"
    rf"\d{{5}}(?!\d)[ \t]+{_CITY})(?![\w’'-])"
)


def personal_name_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """Return name-field/title spans with explicit patient or clinician roles."""
    found: dict[tuple[int, int], str] = {}
    # Header context wins over an embedded title, e.g. Patient: Dr. Parkinson.
    for pattern, role in ((_PATIENT, "patient"), (_CLINICIAN, "clinician")):
        for match in pattern.finditer(text):
            found[match.span("value")] = role
    for match in _DOCTOR_TITLE.finditer(text):
        bounds = match.span("value")
        if not any(start <= bounds[0] and bounds[1] <= end for start, end in found):
            found[bounds] = "clinician"
    return tuple((start, end, found[start, end]) for start, end in sorted(found))


def personal_name_role(text: str, start: int, end: int) -> str | None:
    """Resolve a contained name fragment without guessing a role from its name."""
    for name_start, name_end, role in personal_name_spans(text):
        if name_start <= start < end <= name_end:
            return role
    return None


def detect_clinical_identifiers(
    text: str, *, language: str, locale: str | None = None
) -> list[EntityPrediction]:
    """Return anchored names, IDs, birth dates and bounded German postal addresses.

    All offsets address the supplied text. The caller must remap offsets if
    it applies normalization. Unsupported language requests fail explicitly.

    Args:
        text: Source text to scan.
        language: ``"de"`` or ``"en"``.
        locale: Optional locale. When it is Australian English (``en_AU``,
            ``en-au`` ...) and ``language`` is ``"en"``, the values after
            Australian report header labels are also returned with
            ``metadata["rule"] == "au_header"`` and
            ``metadata["subtype"] == "AU_HEADER"``. Any other locale leaves
            the output unchanged.

    Returns:
        Entities sorted by start and end offset.

    Raises:
        ValueError: If ``language`` is not ``"de"`` or ``"en"``.
    """
    if language not in {"de", "en"}:
        raise ValueError("clinical context identifiers support de and en")
    entities = [
        _entity(text, start, end, PERSON, role, "name_context")
        for start, end, role in personal_name_spans(text)
    ]
    if language == "de":
        for match in _GERMAN_POSTAL_ADDRESS.finditer(text):
            start, end = match.span("value")
            entities.append(
                _entity(
                    text,
                    start,
                    end,
                    STREET_ADDRESS,
                    "patient",
                    "postal_address_context",
                )
            )
    for pattern, label, rule in (
        (_DOB, DATE_OF_BIRTH, "birth_context"),
        (_ID, ID_NUM, "id_context"),
        (_PHONE, PHONE, "phone_context"),
    ):
        for match in pattern.finditer(text):
            start, end = match.span("value")
            if label == DATE_OF_BIRTH and not _valid_date(text[start:end], language):
                continue
            if (
                label == PHONE
                and not 7 <= sum(char.isdigit() for char in text[start:end]) <= 15
            ):
                continue
            entities.append(_entity(text, start, end, label, "patient", rule))
    if language == "en" and _is_en_au(locale):
        entities = _with_au_header_values(text, entities)
    return sorted(entities, key=lambda entity: (entity.start, entity.end))


def _is_en_au(locale: str | None) -> bool:
    if not locale:
        return False
    return locale.strip().replace("-", "_").casefold() == "en_au"


def _with_au_header_values(
    text: str, entities: list[EntityPrediction]
) -> list[EntityPrediction]:
    """Add AU header values; a longer header value replaces a contained one.

    A header value already covered by an existing entity (``Dr Tamsin
    VELLACOTT`` around ``Tamsin VELLACOTT``) is not duplicated, and an
    existing entity inside a longer header value (``CITIZEN`` inside
    ``CITIZEN, Jane``) gives way to it.
    """
    for pattern in AU_HEADER_PII_PATTERNS:
        label = normalize_label(pattern.entity_type)
        role = au_header_role(pattern)
        for start, end in iter_pattern_spans(text, pattern):
            if any(e.start <= start and end <= e.end for e in entities):
                continue
            entities = [e for e in entities if not (start <= e.start and e.end <= end)]
            entities.append(
                _entity(
                    text,
                    start,
                    end,
                    label,
                    role,
                    "au_header",
                    version=AU_HEADER_RULES_VERSION,
                    subtype=pattern.subtype,
                )
            )
    return entities


def _entity(
    text: str,
    start: int,
    end: int,
    label: str,
    role: str,
    rule: str,
    *,
    version: str = CLINICAL_IDENTIFIER_VERSION,
    subtype: str | None = None,
) -> EntityPrediction:
    metadata = {
        "detector": "rules:clinical_context",
        "clinical_role": role,
        "rule": rule,
        "version": version,
    }
    if subtype is not None:
        metadata["subtype"] = subtype
    return EntityPrediction(
        text=text[start:end],
        label=label,
        confidence=1.0,
        start=start,
        end=end,
        metadata=metadata,
    )


def _valid_date(value: str, language: str) -> bool:
    parts = re.split(r"[./-]", value)
    if len(parts[0]) == 4:
        year, month, day = map(int, parts)
    elif language == "de" or "." in value:
        day, month, year = map(int, parts)
    else:
        # The English route does not assume a US date order. Both calendar
        # interpretations identify the entire DOB span and retain ambiguity.
        first, second, year = map(int, parts)
        month, day = (first, second) if first <= 12 else (second, first)
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


__all__ = [
    "AU_HEADER_RULES_VERSION",
    "CLINICAL_IDENTIFIER_VERSION",
    "detect_clinical_identifiers",
    "personal_name_role",
    "personal_name_spans",
]
