"""Tag grammar and masking helpers. All values are synthetic."""

from __future__ import annotations

import pytest

from openmed.core.deid_tags import (
    DEID_TAG_RE,
    find_deid_tags,
    mask_deid_tags,
    overlaps_tag,
)


@pytest.mark.parametrize(
    "tag",
    [
        "[PERSON_1]",
        "[PERSON_FN_2]",
        "[DATE_OF_BIRTH_1]",
        "[POTENTIAL_IDENTIFIER_1]",
        "[CANCER_3]",
    ],
)
def test_tag_grammar_matches(tag):
    assert DEID_TAG_RE.fullmatch(tag)


@pytest.mark.parametrize("text", ["[1]", "[person_1]", "[PERSON]", "[x]", "[PERSON_1"])
def test_tag_grammar_rejects(text):
    assert DEID_TAG_RE.search(text) is None


def test_mask_preserves_length_and_outside_text():
    text = "[PERSON_1] saw Dr [PERSON_FN_2] on [DATE_3]; [x] stays."
    masked = mask_deid_tags(text)
    assert len(masked) == len(text)
    tags = find_deid_tags(text)
    assert len(tags) == 3
    for i, (ch, orig) in enumerate(zip(masked, text)):
        if any(s <= i < e for s, e in tags):
            assert ch == " "
        else:
            assert ch == orig


def test_find_adjacent_tags():
    assert find_deid_tags("[A_1][B_2]") == [(0, 5), (5, 10)]


def test_overlaps_tag_half_open():
    tags = [(5, 10)]
    assert overlaps_tag(8, 12, tags)
    assert overlaps_tag(0, 6, tags)
    assert overlaps_tag(6, 8, tags)
    assert not overlaps_tag(0, 5, tags)
    assert not overlaps_tag(10, 12, tags)
