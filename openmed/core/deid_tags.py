"""Helpers for ``[LABEL_n]`` tags left behind by de-identification.

De-identified text carries placeholders such as ``[PERSON_1]`` or
``[DATE_OF_BIRTH_2]``. A clinical NER model should never see them, and no
entity should land on them. These helpers find the tags, blank them to the
same length so offsets are unchanged, and test spans against them.
"""

from __future__ import annotations

import re
from typing import Sequence

DEID_TAG_RE: re.Pattern[str] = re.compile(r"\[[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_\d+\]")
"""Matches ``[PERSON_1]``, ``[PERSON_FN_2]``, ``[DATE_OF_BIRTH_1]`` and similar."""

__all__ = ["DEID_TAG_RE", "find_deid_tags", "mask_deid_tags", "overlaps_tag"]


def find_deid_tags(text: str) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` offsets of every de-identification tag.

    Args:
        text: Text that may contain ``[LABEL_n]`` tags.

    Returns:
        Half-open offsets, in text order.
    """
    return [(m.start(), m.end()) for m in DEID_TAG_RE.finditer(text)]


def mask_deid_tags(text: str) -> str:
    """Replace every tag with the same number of spaces.

    The result has the same length as ``text`` and is identical outside the
    tags, so offsets into it are offsets into ``text``.

    Args:
        text: Text that may contain ``[LABEL_n]`` tags.

    Returns:
        The masked text.
    """
    return DEID_TAG_RE.sub(lambda m: " " * len(m.group(0)), text)


def overlaps_tag(start: int, end: int, tags: Sequence[tuple[int, int]]) -> bool:
    """Return whether ``[start, end)`` overlaps any tag span.

    Args:
        start: Span start offset.
        end: Span end offset (exclusive).
        tags: Tag offsets from :func:`find_deid_tags`.

    Returns:
        ``True`` when the span shares at least one character with a tag.
    """
    return any(start < tag_end and end > tag_start for tag_start, tag_end in tags)
