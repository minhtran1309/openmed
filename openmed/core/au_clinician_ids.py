"""Australian clinician and veteran identifiers: AHPRA, DVA, provider number.

Three label-anchored recognisers share this module. Each value is detected
only directly after one of its field labels (the label is the context word
and is part of the regex, outside the ``value`` group), so an unlabelled
value of the same shape is never tagged.

The patterns are appended to the ``en_AU`` locale pattern list
(``openmed.core.pii_i18n.LOCALE_PII_PATTERNS["en_au"]``), so they reach
``extract_pii`` smart merging, the deterministic safety sweep and the
``Pipeline`` deterministic stage whenever the AU locale is in effect.

Identifiers
-----------

* **AHPRA registration number** (``AHPRA``): three capital letters (the
  profession) and ten digits, e.g. ``MED0001234567``. No checksum is
  documented. The prefixes ``MED``, ``NMW`` and ``PHY`` score higher than any
  other three-letter prefix. Raw label ``ahpra_number``.
* **DVA file number** (``AU_DVA``): strict METeOR 339127 grammar, state letter
  ``N V Q W S T``, then a war code and digits (one letter and six digits, two
  and five, three and four, or six digits with no war code), then an optional
  dependant segment letter. Raw label ``dva_number``. Lenient forms with
  dropped zeros (``WSR111``) are a known gap and are not detected.
* **Medicare provider number** (``AU_PROVIDER``): six-digit stem, practice
  location character and check character, validated with the mod-11 check in
  :func:`validate_medicare_provider_number`. A failed checksum is dropped.
  Raw label ``medicare_provider_number``.

All three are ``DIRECT_IDENTIFIER`` canonical labels. Priority 14 and a base
score of 0.9 or more put a labelled value above the generic ID_NUM and
au_records (priority 13) patterns for the same text.
"""

from __future__ import annotations

import re
from typing import List

from .pii_entity_merger import PIIPattern

AU_AHPRA = "AHPRA"
AU_DVA = "AU_DVA"
AU_PROVIDER = "AU_PROVIDER"

#: Field labels that introduce each identifier.
AU_AHPRA_CONTEXT_WORDS: tuple[str, ...] = (
    "AHPRA",
    "AHPRA reg",
    "AHPRA registration",
    "registration no",
    "reg no",
    "practitioner no",
)
AU_DVA_CONTEXT_WORDS: tuple[str, ...] = (
    "DVA",
    "DVA file no",
    "DVA number",
    "Gold Card",
    "White Card",
    "Veteran file no",
)
AU_PROVIDER_CONTEXT_WORDS: tuple[str, ...] = (
    "Medicare provider",
    "provider number",
    "provider no",
    "prescriber",
    "referrer",
)

_STEM_WEIGHTS = (3, 5, 8, 4, 2, 1)
_PLV = {char: index for index, char in enumerate("0123456789")}
_PLV.update({char: 10 + index for index, char in enumerate("ABCDEFGHJKLMNPQRTUVWXY")})
_CHECK_CHARS = "YXWTLKJHFBA"
_PROVIDER_RE = re.compile(r"\d{6} ?[0-9A-HJ-NP-RT-Y][YXWTLKJHFBA]")


def validate_medicare_provider_number(value: str) -> bool:
    """Validate an Australian Medicare provider number.

    A provider number is a six-digit stem, a practice location character (a
    digit or a letter other than ``I``, ``O``, ``S``, ``Z``) and a check
    character. The sum ``3*d1 + 5*d2 + 8*d3 + 4*d4 + 2*d5 + d6 + 6*PLV``
    (``PLV`` is 0-9 for digits and 10-31 for ``A``-``Y`` skipping ``I``,
    ``O`` and ``S``) is reduced mod 11 and the remainder selects the check
    character from ``Y X W T L K J H F B A``. One space between the stem and the
    last two characters is allowed. An all-zero stem is rejected.

    Args:
        value: Provider number, uppercase, e.g. ``2447781L``.

    Returns:
        True when the shape and check character are valid.
    """
    candidate = value.strip()
    if _PROVIDER_RE.fullmatch(candidate) is None:
        return False
    candidate = candidate.replace(" ", "")
    stem = [int(char) for char in candidate[:6]]
    if not any(stem):
        return False
    total = sum(w * d for w, d in zip(_STEM_WEIGHTS, stem))
    total += 6 * _PLV[candidate[6]]
    return _CHECK_CHARS[total % 11] == candidate[7]


_NUM = r"(?:[ ]+(?:no\.?|number|#))?"
_SEPARATOR = r"[ \t]*[:#]?[ \t]*"
_START = r"(?<![A-Za-z0-9])"
_END = r"(?![A-Za-z0-9])"

_AHPRA_LABEL = (
    rf"(?i:AHPRA(?:[ ]+reg(?:istration)?\.?)?{_NUM}"
    r"|(?:registration|reg)[ ]+(?:no\.?|number)"
    r"|practitioner[ ]+(?:no\.?|number))"
)
_DVA_LABEL = (
    rf"(?i:DVA(?:[ ]+file)?{_NUM}"
    rf"|(?:Gold|White)[ ]+Card{_NUM}"
    r"|veteran(?:[ ]+file)?[ ]+(?:no\.?|number))"
)
_PROVIDER_LABEL = (
    rf"(?i:Medicare[ ]+provider{_NUM}"
    r"|provider[ ]+(?:no\.?|number|#)"
    rf"|prescriber{_NUM}"
    rf"|referrer{_NUM})"
)

_DVA_VALUE = (
    r"[NVQWST](?:[A-Z][ ]?\d{6}|[A-Z]{2}[ ]?\d{5}|[A-Z]{3}[ ]?\d{4}|[ ]?\d{6})"
    r"[A-Z]?"
)
_PROVIDER_VALUE = r"\d{6}[ ]?[0-9A-HJ-NP-RT-Y][YXWTLKJHFBA]"


def _pattern(
    label: str,
    value: str,
    entity_type: str,
    words: tuple[str, ...],
    subtype: str,
    score: float,
    **extra,
) -> PIIPattern:
    return PIIPattern(
        rf"{_START}{label}(?![A-Za-z]){_SEPARATOR}(?P<value>{value}){_END}",
        entity_type,
        priority=14,
        flags=0,
        base_score=score,
        context_boost=1.0 - score,
        context_words=[word.casefold() for word in words],
        requires_context=False,
        subtype=subtype,
        **extra,
    )


AU_CLINICIAN_ID_PII_PATTERNS: List[PIIPattern] = [
    # Known profession prefixes score higher than an arbitrary prefix.
    _pattern(
        _AHPRA_LABEL,
        r"(?:MED|NMW|PHY)\d{10}",
        "ahpra_number",
        AU_AHPRA_CONTEXT_WORDS,
        AU_AHPRA,
        0.95,
    ),
    _pattern(
        _AHPRA_LABEL,
        r"(?!MED|NMW|PHY)[A-Z]{3}\d{10}",
        "ahpra_number",
        AU_AHPRA_CONTEXT_WORDS,
        AU_AHPRA,
        0.9,
    ),
    _pattern(
        _DVA_LABEL,
        _DVA_VALUE,
        "dva_number",
        AU_DVA_CONTEXT_WORDS,
        AU_DVA,
        0.9,
    ),
    _pattern(
        _PROVIDER_LABEL,
        _PROVIDER_VALUE,
        "medicare_provider_number",
        AU_PROVIDER_CONTEXT_WORDS,
        AU_PROVIDER,
        0.95,
        validator=validate_medicare_provider_number,
        reject_on_validation_failure=True,
    ),
]
