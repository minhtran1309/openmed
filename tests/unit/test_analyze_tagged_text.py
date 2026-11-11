"""``analyze_text(protect_deid_tags=True)`` on ``[LABEL_n]`` tagged text.

The pipeline is a fake loader that records its input and finds fixed words, plus
one canned entity placed over every tag it can see. Synthetic values only.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from openmed import analyze_text
from openmed.core.deid_tags import find_deid_tags

WORDS = ("asthma", "metformin", "femur")
TAG_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")


class _Loader:
    config = None

    def __init__(self, *, entity_over_tags: bool = True) -> None:
        self.received: list[str] = []
        self.entity_over_tags = entity_over_tags

    def create_pipeline(self, *_a: Any, **_k: Any):
        def ner(text: Any, **_kw: Any):
            if isinstance(text, list):
                return [ner(t) for t in text]
            self.received.append(text)
            found = []
            for word in WORDS:
                for m in re.finditer(word, text):
                    found.append(
                        {
                            "word": word,
                            "entity_group": "CONDITION",
                            "score": 0.99,
                            "start": m.start(),
                            "end": m.end(),
                        }
                    )
            if self.entity_over_tags:
                # A model reading a tag as text would mark the tag itself and a
                # span that straddles the tag and its neighbour.
                for m in TAG_RE.finditer(text):
                    found.append(
                        {
                            "word": m.group(0),
                            "entity_group": "PERSON",
                            "score": 0.99,
                            "start": m.start(),
                            "end": m.end(),
                        }
                    )
                    found.append(
                        {
                            "word": "x",
                            "entity_group": "PERSON",
                            "score": 0.99,
                            "start": max(m.start() - 2, 0),
                            "end": m.start() + 2,
                        }
                    )
            return found

        return ner

    def get_max_sequence_length(self, *_a: Any, **_k: Any) -> int:
        return 512


TAGGED = [
    "[PERSON_1] has asthma and takes metformin.",
    "Seen by [PERSON_FN_2] [PERSON_LN_3] for asthma on [DATE_4]",
    "[PERSON_1][DATE_2] asthma. Fracture of the femur. Follow up with "
    "[PERSON_5] about metformin. Review [DATE_6]",
    "Known asthma.   [DATE_OF_BIRTH_1]",
    "  [CANCER_1] and asthma  ",
]


def _key(result):
    return [(e.text, e.label, e.start, e.end, e.confidence) for e in result.entities]


def _run(text, loader, **kw):
    return analyze_text(
        text,
        model_name="fixture-ner-model",
        loader=loader,
        **kw,
    )


@pytest.mark.parametrize("sentences", [False, True])
@pytest.mark.parametrize("text", TAGGED)
def test_tags_never_reach_model(text, sentences):
    loader = _Loader()
    _run(text, loader, protect_deid_tags=True, sentence_detection=sentences)
    assert loader.received
    for seen in loader.received:
        assert "[" not in seen and "]" not in seen
        assert not TAG_RE.search(seen)


@pytest.mark.parametrize("sentences", [False, True])
@pytest.mark.parametrize("text", TAGGED)
def test_entity_overlapping_tag_dropped(text, sentences):
    result = _run(text, _Loader(), protect_deid_tags=True, sentence_detection=sentences)
    tags = find_deid_tags(result.text)
    assert tags
    for e in result.entities:
        assert not any(e.start < te and e.end > ts for ts, te in tags)
    assert {e.label for e in result.entities} == {"CONDITION"}


@pytest.mark.parametrize("sentences", [False, True])
@pytest.mark.parametrize("text", TAGGED)
def test_offsets_slice_original(text, sentences):
    result = _run(text, _Loader(), protect_deid_tags=True, sentence_detection=sentences)
    assert result.entities
    assert result.text == text.strip()
    for e in result.entities:
        assert result.text[e.start : e.end] == e.text
    # Tags survive in the returned text.
    assert find_deid_tags(result.text) == find_deid_tags(text.strip())


def test_tag_inside_sentence_and_chunking():
    body = "[PERSON_1] reported asthma. " * 40 + "Taking metformin [DATE_9] daily."
    loader = _Loader()
    result = _run(body, loader, protect_deid_tags=True, sentence_detection=True)
    assert len(loader.received) >= 1
    assert all("[" not in seen for seen in loader.received)
    for e in result.entities:
        assert result.text[e.start : e.end] == e.text
        assert e.label == "CONDITION"
    assert sum(e.text == "asthma" for e in result.entities) == 40


@pytest.mark.parametrize("text", TAGGED + ["No tags here, asthma only."])
def test_flag_off_unchanged(text):
    omitted = _run(text, _Loader())
    explicit = _run(text, _Loader(), protect_deid_tags=False)
    assert omitted.text == explicit.text
    assert _key(omitted) == _key(explicit)
    assert omitted.metadata == explicit.metadata


def test_flag_off_tags_still_reach_model():
    loader = _Loader()
    result = _run(TAGGED[0], loader)
    assert any("[PERSON_1]" in seen for seen in loader.received)
    assert any(e.label == "PERSON" for e in result.entities)


def test_untagged_text_same_with_flag():
    text = "Asthma and metformin for the femur."
    off = _run(text, _Loader())
    on = _run(text, _Loader(), protect_deid_tags=True)
    assert _key(off) == _key(on)
    assert off.text == on.text


def test_cache_key_distinguishes_tagged_texts():
    a = _run(
        "[PERSON_1] has asthma.", _Loader(), protect_deid_tags=True, cache_results=True
    )
    b = _run(
        "[PERSON_2] has asthma.", _Loader(), protect_deid_tags=True, cache_results=True
    )
    assert a.text == "[PERSON_1] has asthma."
    assert b.text == "[PERSON_2] has asthma."
