"""Australian NRP deny list (ticket 07, story 17).

NRP (nationality, religious or political group) terms describe a group, not a
person, but in a small community they narrow who a record is about. The
Australian deny list covers the Aboriginal and Torres Strait Islander
descriptors:

* ``Aboriginal and Torres Strait Islander``
* ``Torres Strait Islander``
* ``Indigenous Australian``
* ``Aboriginal``

The pattern here is appended to the ``en_AU`` locale pattern list
(``openmed.core.pii_i18n.LOCALE_PII_PATTERNS["en_au"]``), so it reaches
``extract_pii`` smart merging, the deterministic safety sweep and the
``Pipeline`` deterministic stage whenever the AU locale is in effect. The raw
label is ``ethnicity`` (canonical ``ETHNICITY``) and every match carries
``metadata["subtype"] == "NRP"``. The policies decide the action: ``ccia_l1``
keeps the term, ``ccia_l2`` and ``ccia_l3`` mask it.

Design rules:

* One case-insensitive alternation ordered longest first, so ``Aboriginal and
  Torres Strait Islander`` is one span and never ``Aboriginal`` plus the rest.
* Words are separated by any run of whitespace, so a phrase broken across a
  line is still one span.
* A term is word-bounded (``Aboriginality`` and ``Non-Indigenous`` are not
  matched); a plural ``s`` is part of the span (``Torres Strait Islanders``).
"""

from __future__ import annotations

import re
from typing import List

from .pii_entity_merger import PIIPattern

NRP = "NRP"

AU_NRP_TERMS: tuple[str, ...] = tuple(
    sorted(
        (
            "Aboriginal and Torres Strait Islander",
            "Torres Strait Islander",
            "Indigenous Australian",
            "Aboriginal",
        ),
        key=len,
        reverse=True,
    )
)

_TERMS = "|".join(r"\s+".join(map(re.escape, term.split())) for term in AU_NRP_TERMS)

AU_NRP_PII_PATTERNS: List[PIIPattern] = [
    PIIPattern(
        rf"(?<![\w-])(?:{_TERMS})s?(?![\w-])",
        "ethnicity",
        priority=8,
        flags=re.IGNORECASE,
        base_score=0.9,
        context_boost=0.05,
        subtype=NRP,
    ),
]
