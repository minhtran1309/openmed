"""Tag protection with the real clinical models (skipped when not cached).

One synthetic tagged report is run through the disease, pharma, anatomy and
oncology model families with ``HF_HUB_OFFLINE=1`` semantics: a model missing
from the local Hugging Face cache skips instead of downloading.
"""

from __future__ import annotations

import pytest

from openmed import analyze_text
from openmed.core.deid_tags import find_deid_tags
from openmed.core.hf_hub import resolve_repo_id

pytestmark = [pytest.mark.slow, pytest.mark.integration]

REPORT = (
    "[PERSON_1] was seen on [DATE_2]. History of metastatic breast carcinoma "
    "with a lesion in the left femur, treated with tamoxifen and metformin. "
    "Referred by [PERSON_FN_3] [PERSON_LN_4] to [ORGANIZATION_5]. "
    "[PERSON_1][DATE_6] Asthma is stable."
)

MODELS = [
    "disease_detection_superclinical",
    "pharma_detection_superclinical",
    "anatomy_detection_electramed",
    "oncology_detection_superclinical",
]


def _require_cached(model: str) -> None:
    pytest.importorskip("huggingface_hub")
    from huggingface_hub import try_to_load_from_cache

    repo = resolve_repo_id(model)
    cached = try_to_load_from_cache(repo, "config.json")
    if not isinstance(cached, str):
        pytest.skip(f"{repo} is not in the local Hugging Face cache")


@pytest.mark.parametrize("model", MODELS)
def test_tagged_report_with_real_model(model, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    _require_cached(model)

    result = analyze_text(REPORT, model_name=model, protect_deid_tags=True)

    tags = find_deid_tags(result.text)
    assert result.text == REPORT
    assert len(tags) == 7
    for entity in result.entities:
        assert result.text[entity.start : entity.end] == entity.text
        assert not any(entity.start < te and entity.end > ts for ts, te in tags)
