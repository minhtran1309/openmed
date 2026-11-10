"""Australian checksum identifiers: ABN, ACN and IHI validators and patterns.

The patterns here are appended to the ``en_AU`` locale pattern list
(``openmed.core.pii_i18n.LOCALE_PII_PATTERNS["en_au"]``), so they reach
``extract_pii`` smart merging, the deterministic safety sweep and the
``Pipeline`` deterministic stage whenever the AU locale is in effect. The
Medicare and TFN patterns live next to them in ``pii_i18n`` and carry the
``AU_MEDICARE`` / ``AU_TFN`` subtypes.

Design rules shared by every pattern:

* The regex matches the identifier value only, never its field label. Nearby
  context words (``ABN``, ``IHI`` ...) raise the score instead.
* A match whose checksum fails is dropped unless a context word directly
  labels it (``reject_unlabelled_validation_failure``), so random
  9/10/11/16-digit numbers are never tagged as AU identifiers. A labelled
  failure (``ABN: ...``, ``Medicare number ...``) is kept at a reduced score
  with no subtype, so a typo or OCR error of a real identifier is still
  redacted rather than silently leaked.
* A match never starts or ends inside a longer separated digit run, so an
  identifier cannot be carved out of a larger number.
* Each pattern emits its own AU canonical label, next to ``SSN``: raw
  labels ``ihi`` / ``abn`` / ``acn`` (and ``medicare_number`` / ``tfn`` in
  ``pii_i18n``) normalize to ``IHI`` / ``ABN`` / ``ACN`` /
  ``MEDICARE_NUMBER`` / ``TFN``, all ``DIRECT_IDENTIFIER``. Validated matches
  also carry a value-free ``subtype`` (``AU_ABN`` ...) in
  ``metadata["subtype"]``.

Known false-positive property: a checksum is weak evidence for short numbers.
About 1 in 10 random nine-digit numbers pass the ACN check digit and about
1 in 11 pass the TFN mod-11 check; about 10% of ten-digit numbers with a
leading ``2``-``6`` pass the Medicare check (1 in 89 for ABN-shaped
eleven-digit numbers). Bare checksum-valid numbers in prose are therefore
tagged at the base score even without context, which over-redacts such
numbers and may give the wrong subtype (for example a value valid as both
ACN and TFN is ``AU_TFN`` unless ACN context is nearby). The ``800360``
prefix makes IHI matches far more specific.
"""

from __future__ import annotations

import re
from typing import List

from .anonymizer.providers.clinical_ids import _luhn_check_digit
from .pii_entity_merger import PIIPattern

AU_ABN = "AU_ABN"
AU_ACN = "AU_ACN"
AU_IHI = "AU_IHI"
AU_MEDICARE = "AU_MEDICARE"
AU_TFN = "AU_TFN"

_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
_ACN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)
_IHI_PREFIX = "800360"

_ABN_RE = re.compile(r"[1-9]\d ?\d{3} ?\d{3} ?\d{3}")
_ACN_RE = re.compile(r"\d{3} ?\d{3} ?\d{3}")
_IHI_SEPARATORS_RE = re.compile(r"[ -]")

# Digit-run guards: never start right after a digit (optionally one separator
# away) and never stop right before one.
_RUN_START = r"(?<!\d)(?<!\d[ -])"
_RUN_END = r"(?![ -]?\d)"


def validate_australian_abn(text: str) -> bool:
    """Validate an Australian Business Number (ABN).

    An ABN is eleven digits, usually printed ``NN NNN NNN NNN``. Subtract one
    from the first digit, weight the digits by
    ``10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19`` and the sum must be divisible by
    89. The leading check pair is never ``0N``.

    Args:
        text: ABN with or without the printed spaces.

    Returns:
        True when the value has a valid ABN shape and checksum.
    """
    candidate = text.strip()
    if _ABN_RE.fullmatch(candidate) is None:
        return False
    values = [int(char) for char in candidate if char.isdigit()]
    values[0] -= 1
    return sum(w * v for w, v in zip(_ABN_WEIGHTS, values)) % 89 == 0


def validate_australian_acn(text: str) -> bool:
    """Validate an Australian Company Number (ACN).

    An ACN is nine digits, usually printed ``NNN NNN NNN``. The first eight
    digits are weighted ``8, 7, ..., 1``; the check digit is
    ``(10 - sum % 10) % 10`` and must equal the ninth digit. The all-zero
    value is rejected.

    Args:
        text: ACN with or without the printed spaces.

    Returns:
        True when the value has a valid ACN shape and check digit.
    """
    candidate = text.strip()
    if _ACN_RE.fullmatch(candidate) is None:
        return False
    values = [int(char) for char in candidate if char.isdigit()]
    if not any(values):
        return False
    total = sum(w * v for w, v in zip(_ACN_WEIGHTS, values))
    return (10 - total % 10) % 10 == values[8]


def validate_australian_ihi(text: str) -> bool:
    """Validate an Australian Individual Healthcare Identifier (IHI).

    An IHI is sixteen digits beginning ``800360`` whose last digit is a Luhn
    check digit over the first fifteen. Single spaces or hyphens between
    digits are ignored.

    Args:
        text: IHI with or without spacing.

    Returns:
        True when the value has the IHI prefix, length and Luhn check digit.
    """
    candidate = _IHI_SEPARATORS_RE.sub("", text.strip())
    if len(candidate) != 16 or not candidate.isdigit():
        return False
    if not candidate.startswith(_IHI_PREFIX):
        return False
    body = [int(char) for char in candidate[:-1]]
    return _luhn_check_digit(body) == int(candidate[-1])


_ABN_CONTEXT = [
    "abn",
    "australian business number",
    "business number",
]
_ACN_CONTEXT = [
    "acn",
    "australian company number",
    "company number",
]
_IHI_CONTEXT = [
    "ihi",
    "individual healthcare identifier",
    "individual health identifier",
    "healthcare identifier",
    "health identifier",
]

_ACN_PATTERN = rf"{_RUN_START}\b\d{{3}} ?\d{{3}} ?\d{{3}}\b{_RUN_END}"

AU_IDENTIFIER_PII_PATTERNS: List[PIIPattern] = [
    # Individual Healthcare Identifier: 16 digits, ``800360`` prefix, Luhn,
    # any single-space or hyphen grouping. The prefix plus Luhn is specific
    # enough to tag without context and to outrank the generic 16-digit card
    # pattern (priority 8, base score 0.4).
    PIIPattern(
        rf"{_RUN_START}\b8[ -]?0[ -]?0[ -]?3[ -]?6[ -]?0(?:[ -]?\d){{10}}\b{_RUN_END}",
        "ihi",
        priority=14,
        base_score=0.85,
        context_boost=0.1,
        context_words=list(_IHI_CONTEXT),
        validator=validate_australian_ihi,
        reject_unlabelled_validation_failure=True,
        subtype=AU_IHI,
    ),
    # Australian Business Number: ``NN NNN NNN NNN`` or unspaced. Outranks the
    # unspaced Medicare-plus-IRN shape, which is also eleven digits.
    PIIPattern(
        rf"{_RUN_START}\b[1-9]\d ?\d{{3}} ?\d{{3}} ?\d{{3}}\b{_RUN_END}",
        "abn",
        priority=13,
        base_score=0.45,
        context_boost=0.5,
        context_words=list(_ABN_CONTEXT),
        validator=validate_australian_abn,
        reject_unlabelled_validation_failure=True,
        subtype=AU_ABN,
    ),
    # Australian Company Number, labelled. ACNs share the nine-digit shape of a
    # TFN, so an ACN named by nearby context outranks the TFN pattern ...
    PIIPattern(
        _ACN_PATTERN,
        "acn",
        priority=12,
        base_score=0.45,
        context_boost=0.5,
        context_words=list(_ACN_CONTEXT),
        context_required=True,
        validator=validate_australian_acn,
        reject_unlabelled_validation_failure=True,
        subtype=AU_ACN,
    ),
    # ... while an unlabelled nine-digit value that is valid as both is kept as
    # the more sensitive personal TFN (TFN priority 11 and equal base score).
    PIIPattern(
        _ACN_PATTERN,
        "acn",
        priority=10,
        base_score=0.45,
        context_boost=0.5,
        context_words=list(_ACN_CONTEXT),
        validator=validate_australian_acn,
        reject_unlabelled_validation_failure=True,
        subtype=AU_ACN,
    ),
]
