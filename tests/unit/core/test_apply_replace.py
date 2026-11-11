"""Tests for the ``replace`` apply treatment (mt.3 ticket 09).

Every value is synthetic: names are invented, checksum identifiers come from
the repo generators or validators (the same sources as
``test_au_surrogates.py``), phones are from the ACMA fictional ranges, and the
patient keys and secrets are test-only material.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

from openmed.core import apply_surrogates as apply_surrogates_module
from openmed.core.anonymizer import Anonymizer
from openmed.core.anonymizer.providers.au_surrogates import ACMA_FICTIONAL_MOBILES
from openmed.core.anonymizer.providers.clinical_ids import (
    validate_australian_medicare,
    validate_australian_tfn,
)
from openmed.core.apply_spans import (
    MARK_SHIFT_LABELS,
    SHIFT_LABELS_BY_TREATMENT,
    SUPPORTED_TREATMENTS,
    ApplySpansError,
    apply_spans,
    shift_full_date,
)
from openmed.core.apply_surrogates import (
    SURROGATE_DOMAIN,
    SURROGATE_LABELS,
    PatientSurrogates,
    consistency_key,
)
from openmed.core.au_clinician_ids import validate_medicare_provider_number
from openmed.core.au_contact import validate_australian_phone
from openmed.core.au_identifiers import (
    validate_australian_abn,
    validate_australian_acn,
    validate_australian_ihi,
)
from openmed.core.date_shift import stable_offset_for
from openmed.risk.surrogate_audit import audit_surrogate_maps

KEY_A = "synthetic-patient-key-alpha-77"
KEY_B = "synthetic-patient-key-bravo-31"
SECRET = "synthetic-secret-material-replace"

T1 = (
    "Jane CITIZEN, DOB 12/01/1961, Medicare 3664 73786 3, IHI 8003 6065 6665 "
    "8120, ph 0491 570 156. Admitted 04/07/2023, also seen in 2019. "
    "Identifies as Wiradjuri."
)
T2 = "Leiomyosarcoma of the uterus. Review by jane  citizen on 5 March 2024."

AU_SOURCES = {
    "MEDICARE_NUMBER": "3664 73786 3",
    "TFN": "587 150 211",
    "IHI": "8003 6065 6665 8120",
    "ABN": "15 062 984 812",
    "ACN": "824 646 924",
    "MEDICARE_PROVIDER_NUMBER": "1756048Y",
}
AU_VALIDATORS = {
    "MEDICARE_NUMBER": validate_australian_medicare,
    "TFN": validate_australian_tfn,
    "IHI": validate_australian_ihi,
    "ABN": validate_australian_abn,
    "ACN": validate_australian_acn,
    "MEDICARE_PROVIDER_NUMBER": validate_medicare_provider_number,
}


def _span(span_id, text, value, label, *, occurrence=0, **extra):
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(value, start + 1)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(value),
        "label": label,
        **extra,
    }


def _seg(segment_id, text, spans=()):
    return {"segment_id": segment_id, "text": text, "spans": list(spans)}


def _doc():
    return [
        _seg(
            "s1",
            T1,
            [
                _span("p1", T1, "Jane CITIZEN", "PERSON"),
                _span("b1", T1, "12/01/1961", "DATE_OF_BIRTH"),
                _span("m1", T1, "3664 73786 3", "MEDICARE_NUMBER"),
                _span("i1", T1, "8003 6065 6665 8120", "IHI"),
                _span("ph", T1, "0491 570 156", "PHONE"),
                _span("d1", T1, "04/07/2023", "DATE"),
                _span("y1", T1, "2019", "DATE"),
                _span("e1", T1, "Wiradjuri", "ETHNICITY"),
            ],
        ),
        _seg(
            "s2",
            T2,
            [
                _span(
                    "c1",
                    T2,
                    "Leiomyosarcoma of the uterus",
                    "CANCER",
                    replacement="Soft Tissue",
                ),
                _span("p2", T2, "jane  citizen", "PERSON"),
                _span("d2", T2, "5 March 2024", "DATE"),
            ],
        ),
    ]


def _replace(document, *, patient_key=KEY_A, secret=SECRET, policy="ccia_l3"):
    return apply_spans(
        document,
        treatment="replace",
        policy=policy,
        patient_key=patient_key,
        secret=secret,
    )


def _out(result, span_id):
    matches = [s for s in result.spans if s["span_id"] == span_id]
    assert len(matches) == 1, result.spans
    return matches[0]


def _sliced(result, span_id):
    span = _out(result, span_id)
    text = next(
        s["text"] for s in result.segments if s["segment_id"] == span["segment_id"]
    )
    return text[span["out_start"] : span["out_end"]]


def _patient_seed(patient_key: str, secret: str) -> int:
    patient_secret = hmac.new(
        secret.encode("utf-8"),
        b"openmed.apply.surrogate.v1\x00" + patient_key.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return int.from_bytes(patient_secret[:8], "big")


def _compact(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value).upper()


# ---------------------------------------------------------------------------
# Constants and vocabulary
# ---------------------------------------------------------------------------


def test_treatment_vocabulary_and_shift_labels() -> None:
    assert SUPPORTED_TREATMENTS == ("mark", "replace", "generalise", "keep")
    assert SURROGATE_DOMAIN == b"openmed.apply.surrogate.v1\x00"
    assert SHIFT_LABELS_BY_TREATMENT["mark"] == frozenset({"DATE"})
    assert SHIFT_LABELS_BY_TREATMENT["generalise"] == frozenset({"DATE"})
    assert SHIFT_LABELS_BY_TREATMENT["replace"] == frozenset({"DATE", "DATE_OF_BIRTH"})
    assert SHIFT_LABELS_BY_TREATMENT["keep"] == frozenset({"DATE", "DATE_OF_BIRTH"})
    assert MARK_SHIFT_LABELS == SHIFT_LABELS_BY_TREATMENT["mark"]
    assert SURROGATE_LABELS == frozenset(
        {
            "PERSON",
            "FIRST_NAME",
            "MIDDLE_NAME",
            "LAST_NAME",
            "EMAIL",
            "PHONE",
            "STREET_ADDRESS",
            "LOCATION",
            "ZIPCODE",
            "ORGANIZATION",
            "URL",
            "USERNAME",
            "ID_NUM",
            "ACCOUNT_NUMBER",
            "MEDICARE_NUMBER",
            "TFN",
            "IHI",
            "ABN",
            "ACN",
            "AHPRA_NUMBER",
            "DVA_NUMBER",
            "MEDICARE_PROVIDER_NUMBER",
        }
    )


def test_consistency_key_normalises_surface_per_label() -> None:
    assert consistency_key("Jane  CITIZEN", "PERSON") == "jane citizen"
    assert consistency_key("jane citizen", "PERSON") == "jane citizen"
    assert consistency_key("0491 570 156", "PHONE") == "0491570156"
    assert consistency_key("+61 491 570 156", "PHONE") == "0491570156"
    assert consistency_key("3664 73786 3", "MEDICARE_NUMBER") == "3664737863"
    # Letter-bearing IDs keep their letters so NX/QX values stay distinct.
    assert consistency_key("nx 123456", "DVA_NUMBER") == "NX123456"
    assert consistency_key("QX123456", "DVA_NUMBER") != consistency_key(
        "NX123456", "DVA_NUMBER"
    )


# ---------------------------------------------------------------------------
# Ticket acceptance 1: per-patient consistency
# ---------------------------------------------------------------------------


def test_same_patient_key_same_surrogate_across_calls() -> None:
    first = _replace(_doc())
    text = "Seen: jane citizen."
    second = _replace([_seg("x", text, [_span("p", text, "jane citizen", "PERSON")])])
    surrogate = _sliced(first, "p1")
    assert _out(first, "p1")["action"] == "replace"
    assert _out(second, "p")["action"] == "replace"
    assert _sliced(second, "p") == surrogate
    # Within one call, the second casing variant maps to the same surrogate.
    assert _sliced(first, "p2") == surrogate
    assert surrogate.casefold() != "jane citizen"


def test_different_patient_key_different_surrogate() -> None:
    first = _replace(_doc(), patient_key=KEY_A)
    second = _replace(_doc(), patient_key=KEY_B)
    assert _sliced(first, "p1") != _sliced(second, "p1")
    assert _sliced(first, "m1") != _sliced(second, "m1")


def test_surrogate_matches_anonymizer_provider() -> None:
    result = _replace(_doc())
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    independent = Anonymizer(
        lang="en",
        locale="en_AU",
        consistent=True,
        seed=_patient_seed(KEY_A, SECRET),
    )
    for span_id, source, label in (
        ("p1", "Jane CITIZEN", "PERSON"),
        ("m1", "3664 73786 3", "MEDICARE_NUMBER"),
    ):
        rendered = _sliced(result, span_id)
        assert rendered == patients.surrogate(source, label)
        expected = independent.surrogate(consistency_key(source, label), label)
        assert rendered == expected


# ---------------------------------------------------------------------------
# Ticket acceptance 2: AU surrogates are valid and differ from the source
# ---------------------------------------------------------------------------


def test_au_surrogates_pass_validators_and_differ() -> None:
    text = " | ".join(AU_SOURCES.values()) + " | 0491 570 156 | (02) 9876 5432"
    spans = [_span(label, text, value, label) for label, value in AU_SOURCES.items()]
    spans.append(_span("mob", text, "0491 570 156", "PHONE"))
    spans.append(_span("land", text, "(02) 9876 5432", "PHONE"))
    result = _replace([_seg("s", text, spans)])
    for label, source in AU_SOURCES.items():
        rendered = _sliced(result, label)
        assert _out(result, label)["action"] == "replace", label
        assert AU_VALIDATORS[label](rendered), label
        assert _compact(rendered) != _compact(source), label
    for span_id, source in (("mob", "0491 570 156"), ("land", "(02) 9876 5432")):
        rendered = _sliced(result, span_id)
        assert _out(result, span_id)["action"] == "replace"
        assert validate_australian_phone(rendered), span_id
        assert _compact(rendered) != _compact(source)
    assert _sliced(result, "land").startswith("02 ")


# ---------------------------------------------------------------------------
# Ticket acceptance 3: replacement honoured, dates shifted
# ---------------------------------------------------------------------------


def test_replacement_honoured() -> None:
    result = _replace(_doc())
    assert _sliced(result, "c1") == "Soft Tissue"
    assert _out(result, "c1")["action"] == "replacement"
    assert result.segments[1]["text"].startswith("Soft Tissue. Review by ")


def test_replacement_beats_surrogate_and_shift_on_single_span() -> None:
    text = "Jane CITIZEN on 04/07/2023."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("p", text, "Jane CITIZEN", "PERSON", replacement="the patient"),
                _span("d", text, "04/07/2023", "DATE", replacement="admission day"),
            ],
        )
    ]
    result = _replace(doc)
    assert result.segments[0]["text"] == "the patient on admission day."
    assert _out(result, "p")["action"] == "replacement"
    assert _out(result, "d")["action"] == "replacement"


def test_replacement_ignored_in_merged_cluster() -> None:
    text = "Seen by Jane Citizen today."
    doc = [
        _seg(
            "s",
            text,
            [
                _span("full", text, "Jane Citizen", "PERSON", replacement="Ally One"),
                _span("last", text, "Citizen", "LAST_NAME", replacement="Two"),
            ],
        )
    ]
    result = _replace(doc)
    assert result.merges == [{"kept_span_id": "full", "merged_span_ids": ["last"]}]
    rendered = _sliced(result, "full")
    assert _out(result, "full")["action"] == "replace"
    assert "Ally One" not in result.segments[0]["text"]
    assert "Two" not in result.segments[0]["text"]
    assert "Citizen" not in result.segments[0]["text"]
    assert rendered == PatientSurrogates(patient_key=KEY_A, secret=SECRET).surrogate(
        "Jane Citizen", "PERSON"
    )


def test_dates_shift_under_replace() -> None:
    result = _replace(_doc())
    offset = stable_offset_for(KEY_A, max_days=365, secret=SECRET)
    for span_id, source in (
        ("d1", "04/07/2023"),
        ("b1", "12/01/1961"),
        ("d2", "5 March 2024"),
    ):
        assert _sliced(result, span_id) == shift_full_date(source, offset)
        assert _out(result, span_id)["action"] == "shift"
    assert _sliced(result, "y1") == "[DATE_1]"
    assert _out(result, "y1")["action"] == "mark"


def test_unparseable_dob_falls_back_to_its_own_tag() -> None:
    text = "DOB 1961."
    doc = [_seg("s", text, [_span("b", text, "1961", "DATE_OF_BIRTH")])]
    result = _replace(doc)
    assert result.segments[0]["text"] == "DOB [DATE_OF_BIRTH_1]."
    assert _out(result, "b")["action"] == "mark"


def test_unsupported_label_falls_back_to_tag() -> None:
    result = _replace(_doc())
    assert _sliced(result, "e1") == "[ETHNICITY_1]"
    assert _out(result, "e1")["action"] == "mark"


def test_policy_keep_is_kept_under_replace() -> None:
    text = "Leiomyosarcoma noted."
    doc = [_seg("s", text, [_span("c", text, "Leiomyosarcoma", "CANCER")])]
    result = _replace(doc)
    assert result.segments[0]["text"] == text
    assert _out(result, "c")["action"] == "keep"


def test_replace_requires_patient_key() -> None:
    for kwargs in ({}, {"offset_days": 10}, {"secret": SECRET}):
        with pytest.raises(ApplySpansError) as info:
            apply_spans(_doc(), treatment="replace", policy="ccia_l3", **kwargs)
        expected = "INVALID_REQUEST" if "secret" in kwargs else "SURROGATE_KEY_REQUIRED"
        assert info.value.error_code == expected
        assert SECRET not in str(info.value)
    with pytest.raises(ApplySpansError) as info:
        apply_spans(
            _doc(),
            treatment="replace",
            policy="ccia_l3",
            offset_days=10,
            patient_key=KEY_A,
            secret=SECRET,
        )
    assert info.value.error_code == "INVALID_REQUEST"
    # Even a document needing no surrogate requires the key (E8).
    with pytest.raises(ApplySpansError) as info:
        apply_spans([_seg("s", "no spans")], treatment="replace", policy="ccia_l3")
    assert info.value.error_code == "SURROGATE_KEY_REQUIRED"


@pytest.mark.parametrize("treatment", ["mark", "replace"])
@pytest.mark.parametrize("replacement", ["", 7])
def test_empty_or_non_string_replacement_is_invalid(treatment, replacement) -> None:
    text = "Jane Citizen."
    doc = [
        _seg(
            "s",
            text,
            [_span("p", text, "Jane Citizen", "PERSON", replacement=replacement)],
        )
    ]
    with pytest.raises(ApplySpansError) as info:
        apply_spans(
            doc,
            treatment=treatment,
            policy="ccia_l3",
            patient_key=KEY_A,
            secret=SECRET,
        )
    assert info.value.error_code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Collisions, surrogate quality and the mobile pool
# ---------------------------------------------------------------------------


def _bindings(result, document, patient_key):
    """``{key_hash: surrogate}`` for every ``replace`` span, keyed per patient."""
    sources = {
        span["span_id"]: (segment["text"][span["start"] : span["end"]], span["label"])
        for segment in document
        for span in segment["spans"]
    }
    entries = []
    for out in result.spans:
        if out["action"] != "replace":
            continue
        source, label = sources[out["span_id"]]
        material = f"{patient_key}|{label}|{consistency_key(source, label)}"
        key_hash = hmac.new(b"test-only", material.encode(), hashlib.sha256)
        entries.append(
            {
                "key_hash": f"hmac-sha256:{key_hash.hexdigest()}",
                "surrogate": _sliced(result, out["span_id"]),
            }
        )
    return entries


def test_surrogate_map_integrity() -> None:
    text = "Ollie Brandt rang Mia Torvald on 0491 570 157; Ollie Brandt again."
    other = [
        _seg(
            "s",
            text,
            [
                _span("a", text, "Ollie Brandt", "PERSON"),
                _span("b", text, "Mia Torvald", "PERSON"),
                _span("c", text, "0491 570 157", "PHONE"),
                _span("a2", text, "Ollie Brandt", "PERSON", occurrence=1),
            ],
        )
    ]
    maps = {"patient_a": [], "patient_b": []}
    for name, key, document in (
        ("patient_a", KEY_A, _doc()),
        ("patient_a", KEY_A, other),
        ("patient_b", KEY_B, _doc()),
    ):
        maps[name].extend(_bindings(_replace(document, patient_key=key), document, key))
    payload = {
        name: {
            "entries": entries,
            "cardinality": len({e["key_hash"] for e in entries}),
        }
        for name, entries in maps.items()
    }
    report = audit_surrogate_maps(payload)
    assert report.failure_categories["collision"] == 0
    assert report.failure_categories["cardinality"] == 0


def test_mobile_pool_exhaustion_falls_back_to_tag() -> None:
    # 40 distinct synthetic mobiles against a 30-number ACMA fictional pool.
    mobiles = [f"0412 000 {n:03d}" for n in range(40)]
    text = "; ".join(mobiles)
    spans = [_span(f"m{n}", text, value, "PHONE") for n, value in enumerate(mobiles)]
    document = [_seg("s", text, spans)]
    result = _replace(document)
    rendered = {}
    tags = 0
    for n, source in enumerate(mobiles):
        out = _out(result, f"m{n}")
        value = _sliced(result, f"m{n}")
        if out["action"] == "replace":
            assert value in ACMA_FICTIONAL_MOBILES
            assert _compact(value) != _compact(source)
            rendered[source] = value
        else:
            assert out["action"] == "mark"
            assert re.fullmatch(r"\[PHONE_\d+\]", value)
            tags += 1
    assert tags >= 10
    # No two different sources ever share a surrogate within one call.
    assert len(set(rendered.values())) == len(rendered)
    report = audit_surrogate_maps({"phones": _bindings(result, document, KEY_A)})
    assert report.failure_categories["collision"] == 0


def test_two_sources_never_share_a_surrogate_across_labels(monkeypatch) -> None:
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: "Same Value"
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    assert patients.surrogate("Ollie Brandt", "PERSON") == "Same Value"
    assert patients.surrogate("Ollie Brandt", "PERSON") == "Same Value"
    assert patients.surrogate("Mia Torvald", "PERSON") is None
    assert patients.surrogate("Torvald", "LAST_NAME") is None
    assert patients.surrogate("mia torvald", "PERSON") is None


def test_surrogate_never_equals_source(monkeypatch) -> None:
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: original
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    assert patients.surrogate("Ollie Brandt", "PERSON") is None
    assert patients.surrogate("3664 73786 3", "MEDICARE_NUMBER") is None


def test_engine_tag_fallback_is_not_a_surrogate(monkeypatch) -> None:
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: f"[{label}]"
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    assert patients.surrogate("Ollie Brandt", "PERSON") is None


def test_unsupported_label_has_no_surrogate() -> None:
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    assert patients.surrogate("Wiradjuri", "ETHNICITY") is None
    assert patients.surrogate("SSN-ish 123", "SSN") is None


# ---------------------------------------------------------------------------
# Determinism and statelessness (E5, E6)
# ---------------------------------------------------------------------------

_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import json, sys
    from openmed.core.apply_spans import apply_spans
    payload = json.loads(sys.stdin.read())
    result = apply_spans(
        payload["document"],
        treatment="replace",
        policy="ccia_l3",
        patient_key=payload["patient_key"],
        secret=payload["secret"],
    )
    out = result.to_dict()
    out.pop("provenance")
    sys.stdout.write(json.dumps(out, sort_keys=True))
    """
)


def test_determinism_across_processes() -> None:
    payload = json.dumps({"document": _doc(), "patient_key": KEY_A, "secret": SECRET})
    outputs = []
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        completed = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=300,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1]
    in_process = _replace(_doc()).to_dict()
    in_process.pop("provenance")
    assert json.loads(outputs[0]) == json.loads(json.dumps(in_process))


def test_no_state_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    before = dict(vars(apply_surrogates_module))
    first = _replace(_doc()).to_dict()
    second = _replace(_doc()).to_dict()
    assert first == second
    assert list(tmp_path.iterdir()) == []
    after = vars(apply_surrogates_module)
    assert set(after) == set(before)
    assert all(after[name] is value for name, value in before.items())


# ---------------------------------------------------------------------------
# Fix round 1: surrogates never restate another source or a name token (I1, I2)
# ---------------------------------------------------------------------------

_NAME_LABELS = {"PERSON", "FIRST_NAME", "MIDDLE_NAME", "LAST_NAME"}


def _name_tokens(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[^\W\d_]+", value)}


def _assert_no_source_restated(document, result) -> None:
    sources = [
        (segment["text"][span["start"] : span["end"]], span["label"])
        for segment in document
        for span in segment["spans"]
    ]
    source_keys = {" ".join(text.split()).casefold() for text, _ in sources}
    source_name_tokens = set().union(
        *(_name_tokens(text) for text, label in sources if label in _NAME_LABELS)
    )
    labels = {
        span["span_id"]: span["label"]
        for segment in document
        for span in segment["spans"]
    }
    for out in result.spans:
        if out["action"] != "replace":
            continue
        rendered = _sliced(result, out["span_id"])
        assert " ".join(rendered.split()).casefold() not in source_keys
        if labels[out["span_id"]] in _NAME_LABELS:
            assert not _name_tokens(rendered) & source_name_tokens


def _two_names_doc():
    text = "Oliver and James attended."
    return [
        _seg(
            "s",
            text,
            [
                _span("a", text, "Oliver", "FIRST_NAME"),
                _span("b", text, "James", "FIRST_NAME"),
            ],
        )
    ]


def _full_names_doc():
    text = "Mary Jones met David Wilson."
    return [
        _seg(
            "s",
            text,
            [
                _span("a", text, "Mary Jones", "PERSON"),
                _span("b", text, "David Wilson", "PERSON"),
            ],
        )
    ]


def test_surrogate_never_equals_another_source_regression() -> None:
    # Review I1: pk0/s rendered "James and Mary attended." before the fix.
    document = _two_names_doc()
    result = _replace(document, patient_key="pk0", secret="s")
    output = result.segments[0]["text"]
    assert "James" not in output
    assert "Oliver" not in output
    _assert_no_source_restated(document, result)


@pytest.mark.parametrize(
    ("name", "patient_key"), [("Mary Jones", "pk10"), ("David Wilson", "pk7")]
)
def test_person_surrogate_keeps_no_source_token(name, patient_key) -> None:
    # Review I2: "Mary Jones" -> "Shelly Jones", "David Wilson" -> "David Swanson".
    text = f"Seen: {name}."
    document = [_seg("s", text, [_span("p", text, name, "PERSON")])]
    result = _replace(document, patient_key=patient_key, secret="s")
    rendered = _sliced(result, "p")
    assert not _name_tokens(rendered) & _name_tokens(name)
    for token in name.split():
        assert token not in result.segments[0]["text"]


@pytest.mark.parametrize("make_doc", [_two_names_doc, _full_names_doc])
def test_property_no_surrogate_restates_any_source(make_doc) -> None:
    for n in range(200):
        document = make_doc()
        result = _replace(document, patient_key=f"pk{n}", secret="s")
        _assert_no_source_restated(document, result)


def test_reserved_sources_block_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: "James"
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    patients.reserve([("Oliver", "FIRST_NAME"), ("JAMES", "FIRST_NAME")])
    assert patients.surrogate("Oliver", "FIRST_NAME") is None


def test_name_token_overlap_blocks_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: "Shelly Jones"
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    patients.reserve([("Mary Jones", "PERSON")])
    assert patients.surrogate("Mary Jones", "PERSON") is None


# ---------------------------------------------------------------------------
# Fix round 1: a replacement that restates its source is a tag (M1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("replacement", ["Jane Citizen", "jane   CITIZEN"])
def test_replacement_restating_source_renders_mark(replacement) -> None:
    text = "Seen: Jane Citizen."
    document = [
        _seg(
            "s",
            text,
            [_span("p", text, "Jane Citizen", "PERSON", replacement=replacement)],
        )
    ]
    result = _replace(document)
    assert result.segments[0]["text"] == "Seen: [PERSON_1]."
    assert _out(result, "p")["action"] == "mark"


# ---------------------------------------------------------------------------
# Fix round 1: the anonymizer is imported only on the replace path (M5)
# ---------------------------------------------------------------------------


def test_apply_spans_import_does_not_import_faker() -> None:
    script = (
        "import sys\n"
        "import openmed.core.apply_spans\n"
        "from openmed.core.apply_spans import apply_spans\n"
        "doc = [{'segment_id': 's', 'text': 'Ari Volk', 'spans': [\n"
        "    {'span_id': 'a', 'start': 0, 'end': 8, 'label': 'PERSON'}]}]\n"
        "apply_spans(doc, treatment='mark', policy='ccia_l3')\n"
        "print(sorted(m for m in ('faker', 'openmed.core.anonymizer.engine')\n"
        "             if m in sys.modules))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    assert completed.stdout.strip() == "[]"


# ---------------------------------------------------------------------------
# Fix round 2: address words and accent-folded name words
# ---------------------------------------------------------------------------

_ADDRESS_GENERIC = {
    "street", "road", "avenue", "lane", "unit", "level", "suite", "flat",
    "nsw", "vic", "qld", "tas", "act",
}  # fmt: skip


def _address_words(value: str) -> set[str]:
    return {
        word
        for word in _name_tokens(value)
        if len(word) > 1 and word not in _ADDRESS_GENERIC
    }


def _render_address(source, label, patient_key):
    text = f"Lives at {source}."
    document = [_seg("s", text, [_span("a", text, source, label)])]
    result = _replace(document, patient_key=patient_key, secret="s")
    return result, _sliced(result, "a")


@pytest.mark.parametrize(
    ("source", "patient_key"),
    [("12 George Street", "pk263"), ("8 Smith Road, Richmond VIC 3121", "pk149")],
)
def test_address_surrogate_keeps_no_source_word(source, patient_key) -> None:
    # Before fix round 2: "Suite 899 8 George Circle", "Level 8 004 Smith Nook".
    result, rendered = _render_address(source, "STREET_ADDRESS", patient_key)
    assert _out(result, "a")["action"] in {"replace", "mark"}
    assert not _address_words(rendered) & _address_words(source)


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("12 George Street, Sydney NSW 2000", "STREET_ADDRESS"),
        ("Unit 4 9 George Road, Parramatta NSW 2150", "STREET_ADDRESS"),
        ("8 Smith Road, Richmond VIC 3121", "STREET_ADDRESS"),
        ("Richmond", "LOCATION"),
    ],
)
def test_property_address_surrogate_keeps_no_source_word(source, label) -> None:
    for n in range(200):
        result, rendered = _render_address(source, label, f"pk{n}")
        if _out(result, "a")["action"] == "replace":
            assert not _address_words(rendered) & _address_words(source), n


def test_address_word_overlap_blocks_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: "5 George Lane"
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    patients.reserve([("12 George Street, Sydney NSW 2000", "STREET_ADDRESS")])
    assert (
        patients.surrogate("12 George Street, Sydney NSW 2000", "STREET_ADDRESS")
        is None
    )
    assert patients.surrogate("Sydney", "LOCATION") is None


def test_address_generic_words_do_not_block(monkeypatch) -> None:
    candidate = "Unit 7 3 Ross Road, Hobart NSW 7000"
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: candidate
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    source = "Unit 4 9 George Road, Parramatta New South Wales 2150"
    patients.reserve([(source, "STREET_ADDRESS")])
    assert patients.surrogate(source, "STREET_ADDRESS") == candidate


def test_name_words_fold_accents(monkeypatch) -> None:
    monkeypatch.setattr(
        Anonymizer, "surrogate", lambda self, original, label, **kw: "Jose Brown"
    )
    patients = PatientSurrogates(patient_key=KEY_A, secret=SECRET)
    patients.reserve([("José Ruiz", "PERSON")])
    assert patients.surrogate("José Ruiz", "PERSON") is None
