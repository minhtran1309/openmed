"""Model offsets stay exact when source whitespace is preserved.

``analyze_text`` strips its input before inference, so a model sees
``text.strip()`` and reports offsets into that. With ``preserve_whitespace``
on, ``extract_pii`` and ``Pipeline.run`` index the unstripped text, so the
model offsets must be rebased by the stripped leading run. The model is
stubbed at the Hugging Face pipeline level so the real ``analyze_text`` input
validation runs. Names are synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest

from openmed.core import pii
from openmed.core.pipeline import Pipeline

FIRST = "Jane"
LAST = "Citizen"


class _NameFindingLoader:
    """Loader whose token-classification pipeline finds fixed names.

    Offsets index exactly the text the pipeline is called with, as a real
    tokenizer-backed pipeline's do.
    """

    def __init__(self) -> None:
        self.received: list[str] = []

    def create_pipeline(self, *_args: Any, **_kwargs: Any):
        def ner(text: Any, **_call_kwargs: Any):
            if isinstance(text, list):
                return [ner(item) for item in text]
            self.received.append(text)
            found = []
            for word, label in ((FIRST, "first_name"), (LAST, "last_name")):
                start = text.find(word)
                if start >= 0:
                    found.append(
                        {
                            "word": word,
                            "entity_group": label,
                            "score": 0.99,
                            "start": start,
                            "end": start + len(word),
                        }
                    )
            return found

        return ner

    def get_max_sequence_length(self, *_args: Any, **_kwargs: Any) -> int:
        return 512


TEXTS = [
    "   Patient Jane Citizen was seen.",
    "\n  Patient Jane Citizen was seen.",
    "\t\r\n Patient  Jane \n\n Citizen was seen.\n  ",
    "Patient   Jane\t\tCitizen was seen.   ",
    "Patient Jane Citizen was seen.",
]


@pytest.mark.parametrize("text", TEXTS)
def test_extract_pii_preserved_whitespace_offsets_slice_source(text) -> None:
    result = pii.extract_pii(
        text, preserve_whitespace=True, loader=_NameFindingLoader()
    )

    assert result.text == text
    assert {text[e.start : e.end] for e in result.entities} == {FIRST, LAST}
    for entity in result.entities:
        assert entity.text == text[entity.start : entity.end]


@pytest.mark.parametrize("text", TEXTS)
def test_extract_pii_stripped_offsets_unchanged(text) -> None:
    result = pii.extract_pii(text, loader=_NameFindingLoader())

    stripped = text.strip()
    assert result.text == stripped
    assert {stripped[e.start : e.end] for e in result.entities} == {FIRST, LAST}


@pytest.mark.parametrize("preserve_whitespace", [True, False])
@pytest.mark.parametrize("text", TEXTS)
def test_pipeline_run_model_spans_slice_source(text, preserve_whitespace) -> None:
    pipeline = Pipeline(
        preserve_whitespace=preserve_whitespace, loader=_NameFindingLoader()
    )
    result = pipeline.run(text)

    source = text if preserve_whitespace else text.strip()
    sliced = {
        source[span.start : span.end]
        for span in result.spans
        if span.canonical_label in {"FIRST_NAME", "LAST_NAME"}
    }
    assert sliced == {FIRST, LAST}
    assert FIRST not in result.redacted_text
    assert LAST not in result.redacted_text
