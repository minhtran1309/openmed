"""Tests for Australian phone/fax numbers, postcodes, states and addresses.

Every number, street and address here is synthetic: ``0412 345 678`` is the
consumer's published self-test value, the ``55x`` / ``5550`` ranges and
``Smith Street`` style names are fictional, and the well-known institutional
numbers (``13 11 14``) are public service numbers, not personal data.
"""

from __future__ import annotations

import re

import pytest

from openmed.core.au_contact import (
    AU_ADDRESS,
    AU_CONTACT_PII_PATTERNS,
    AU_PHONE,
    AU_PHONE_INSTITUTIONAL,
    AU_POSTCODE,
    AU_STATE,
    STATE_POSTCODE_RANGES,
    validate_australian_institutional_phone,
    validate_australian_phone,
)
from openmed.core.pii import extract_pii
from openmed.core.pii_i18n import LOCALE_PII_PATTERNS, get_patterns_for_language
from openmed.core.safety_sweep import safety_sweep
from openmed.processing.outputs import PredictionResult

_CONTACT_SUBTYPES = {
    AU_PHONE,
    AU_PHONE_INSTITUTIONAL,
    AU_POSTCODE,
    AU_STATE,
    AU_ADDRESS,
}


def _fake_analyze_text(inference_text, **kwargs):
    return PredictionResult(
        text=inference_text,
        entities=[],
        model_name=kwargs["model_name"],
        timestamp="2026-01-01T00:00:00",
    )


@pytest.fixture
def no_model(monkeypatch):
    import openmed

    monkeypatch.setattr(openmed, "analyze_text", _fake_analyze_text)


def _extract(text: str, *, locale: str | None = "en_AU"):
    return extract_pii(
        text, model_name="fixture-pii-model", lang="en", locale=locale
    ).entities


def _sweep(text: str, *, locale: str | None = "en_AU"):
    return safety_sweep(text, [], lang="en", locale=locale)


def _stub_detector(text, **kwargs):
    return PredictionResult(
        text=text, entities=[], model_name="stub", timestamp="2026-01-01T00:00:00"
    )


def _pipeline_spans(text: str):
    from openmed.core.pipeline import Pipeline

    result = Pipeline(policy="ccia_l3", model_detector=_stub_detector).run(text)
    return result


def _subtype(entity) -> str | None:
    return (entity.metadata or {}).get("subtype")


def _with_subtype(entities, subtype):
    return [entity for entity in entities if _subtype(entity) == subtype]


def _contact_findings(entities):
    return [entity for entity in entities if _subtype(entity) in _CONTACT_SUBTYPES]


def _single(entities, text, value, subtype, label):
    findings = _with_subtype(entities, subtype)
    assert len(findings) == 1, [(e.text, e.label, _subtype(e)) for e in entities]
    finding = findings[0]
    start = text.index(value)
    assert (finding.start, finding.end) == (start, start + len(value))
    assert text[finding.start : finding.end] == value
    assert finding.label == label
    return finding


# ---------------------------------------------------------------------------
# Validators and registration
# ---------------------------------------------------------------------------


class TestValidators:
    @pytest.mark.parametrize(
        "value",
        [
            "0412 345 678",
            "0412345678",
            "04 1234 5678",
            "(02) 9382 1111",
            "02 9382 1111",
            "03-9123-4567",
            "(07) 5550 1234",
            "08 5550 1234",
            "+61 412 345 678",
            "+61412345678",
            "+61 2 9382 1111",
            "+61 (0)2 9382 1111",
            "+61 (0) 412 345 678",
            "9382 1111",
            "93821111",
        ],
    )
    def test_phone_accepts(self, value):
        assert validate_australian_phone(value)

    @pytest.mark.parametrize(
        "value",
        [
            "0512 345 678",  # 05 is not accepted (no mobile allocations in use)
            "0612 345 678",  # 06 is not an Australian area code
            "0112 345 678",
            "(02) 0382 1111",  # local numbers never start with 0 or 1
            "(02) 1382 1111",
            "0382 1111",
            "+61 512 345 678",
            "+44 20 7946 0958",
            "041 234 567",  # too short
            "04123456789",  # too long
            "1800 123 456",  # institutional numbers have their own validator
            "(212) 555-0123",
        ],
    )
    def test_phone_rejects(self, value):
        assert not validate_australian_phone(value)

    @pytest.mark.parametrize(
        "value", ["13 11 14", "131114", "13 1450", "1300 555 123", "1800 123 456"]
    )
    def test_institutional_accepts(self, value):
        assert validate_australian_institutional_phone(value)

    @pytest.mark.parametrize(
        "value", ["14 11 14", "1900 555 123", "1300 555 12", "0412 345 678"]
    )
    def test_institutional_rejects(self, value):
        assert not validate_australian_institutional_phone(value)


def test_postcode_ranges_match_the_published_state_ranges():
    expected = {
        "NSW": [(1000, 2599), (2619, 2899), (2921, 2999)],
        "ACT": [(200, 299), (2600, 2618), (2900, 2920)],
        "VIC": [(3000, 3999), (8000, 8999)],
        "QLD": [(4000, 4999), (9000, 9999)],
        "SA": [(5000, 5999)],
        "WA": [(6000, 6999)],
        "TAS": [(7000, 7999)],
        "NT": [(800, 999)],
    }
    for state, ranges in expected.items():
        regex = re.compile(STATE_POSTCODE_RANGES[state])
        for number in range(10000):
            value = f"{number:04d}"
            inside = any(low <= number <= high for low, high in ranges)
            assert bool(regex.fullmatch(value)) is inside, (state, value)


def test_contact_patterns_registered_only_for_en_au():
    for pattern in AU_CONTACT_PII_PATTERNS:
        assert pattern in LOCALE_PII_PATTERNS["en_au"]
    subtypes = {pattern.subtype for pattern in AU_CONTACT_PII_PATTERNS}
    assert subtypes == _CONTACT_SUBTYPES
    for lang, locale in (("en", None), ("en", "en_US"), ("en", "en_GB"), ("fr", None)):
        patterns = get_patterns_for_language(lang, locale=locale)
        assert not any(pattern in patterns for pattern in AU_CONTACT_PII_PATTERNS)


def test_contact_patterns_keep_labels_outside_the_value_span():
    # A pattern that matches a field label must expose the value as a named
    # ``value`` group, so the label never becomes part of the span.
    for pattern in AU_CONTACT_PII_PATTERNS:
        compiled = re.compile(pattern.pattern, pattern.flags)
        if re.search(r"phone|fax|contact|mobile", pattern.pattern, re.I):
            assert "value" in compiled.groupindex, pattern.pattern


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------

_PHONE_TABLE = [
    # (template, value, subtype)
    ("Mobile: {v} (self-test).", "0412 345 678", AU_PHONE),
    ("Call {v} today.", "0412345678", AU_PHONE),
    ("Mob {v}.", "04 1234 5678", AU_PHONE),
    ("Daughter on {v} after hours.", "+61 412 345 678", AU_PHONE),
    ("Daughter on {v} after hours.", "+61412345678", AU_PHONE),
    ("Clinic {v} weekdays.", "+61 2 9382 1111", AU_PHONE),
    ("Clinic {v} weekdays.", "+61 (0)2 9382 1111", AU_PHONE),
    ("Ph {v} for results.", "(02) 9382 1111", AU_PHONE),
    ("Rooms on {v}.", "02 9382 1111", AU_PHONE),
    ("Rooms on {v}.", "(07) 5550 1234", AU_PHONE),
    ("Fax: {v}", "(03) 9123 4567", AU_PHONE),
    ("F: {v}", "03 9123 4567", AU_PHONE),
    ("Ph: {v}", "9382 1111", AU_PHONE),
    ("Tel {v} (reception)", "9382-1111", AU_PHONE),
    ("Fax {v}.", "5550 1234", AU_PHONE),
    ("F: {v}", "93821112", AU_PHONE),
    ("Contact: {v}", "8555 0123", AU_PHONE),
    ("Freecall {v}.", "1800 123 456", AU_PHONE_INSTITUTIONAL),
    ("Bookings {v}.", "1300 555 123", AU_PHONE_INSTITUTIONAL),
    ("Phone {v} for Centrelink.", "13 11 14", AU_PHONE_INSTITUTIONAL),
    ("Call {v}.", "131114", AU_PHONE_INSTITUTIONAL),
]


@pytest.mark.usefixtures("no_model")
class TestPhones:
    @pytest.mark.parametrize(("template", "value", "subtype"), _PHONE_TABLE)
    def test_extract_pii(self, template, value, subtype):
        text = template.format(v=value)
        _single(_extract(text), text, value, subtype, "phone_number")

    @pytest.mark.parametrize(("template", "value", "subtype"), _PHONE_TABLE)
    def test_safety_sweep(self, template, value, subtype):
        text = template.format(v=value)
        finding = _single(_sweep(text), text, value, subtype, "phone_number")
        assert finding.metadata["source"] == "safety_sweep"

    @pytest.mark.parametrize(("template", "value", "subtype"), _PHONE_TABLE)
    def test_pipeline_ccia_l3(self, template, value, subtype):
        text = template.format(v=value)
        result = _pipeline_spans(text)
        start = text.index(value)
        spans = [
            span
            for span in result.spans
            if (span.start, span.end) == (start, start + len(value))
        ]
        assert len(spans) == 1, [(s.start, s.end, s.metadata) for s in result.spans]
        assert spans[0].canonical_label == "PHONE"
        assert spans[0].metadata["subtype"] == subtype
        assert value not in result.redacted_text

    def test_self_test_value_scores_high_with_context(self):
        text = "Mobile: 0412 345 678"
        finding = _single(
            _extract(text), text, "0412 345 678", AU_PHONE, "phone_number"
        )
        assert finding.confidence >= 0.9

    @pytest.mark.parametrize(
        "text",
        [
            "Call (212) 555-0123 for the US office.",  # US format
            "Paracetamol 2500 mg daily, weight 82.5 kg.",
            "Seen on 04/12/2019 and 13-11-14.",
            "Results 13 11 14 were within range.",  # 13-number needs context
            "Reference 9382 1111 noted.",  # bare 8-digit needs a phone label
            "Ph 7.35, pCO2 4.12 kPa.",
            "Phone 0612 345 678 is not a valid number.",
            "Phone 0512 345 678 is not accepted.",
            "Ph: 0382 1111 is not a local number.",
            "Batch 104123456789 recorded.",  # phone not carved from a long run
            "Serial 9 0412 345 678 logged.",
            "Lot 0412 345 6789 logged.",
            "Bed 13 1450 hrs, phone call booked.",  # a time, not a 13 number
        ],
    )
    def test_negatives(self, text):
        assert _with_subtype(_extract(text), AU_PHONE) == []
        assert _with_subtype(_extract(text), AU_PHONE_INSTITUTIONAL) == []
        assert _with_subtype(_sweep(text), AU_PHONE) == []
        assert _with_subtype(_sweep(text), AU_PHONE_INSTITUTIONAL) == []

    @pytest.mark.parametrize(
        ("text", "value", "subtype"),
        [
            ("Medicare number 2123 45670 1 on card.", "2123 45670 1", "AU_MEDICARE"),
            ("Medicare number 2123456701 on card.", "2123456701", "AU_MEDICARE"),
            ("Practice ABN: 11 345 678 919.", "11 345 678 919", "AU_ABN"),
            ("Practice ABN: 11345678919.", "11345678919", "AU_ABN"),
            ("Tax file number 123 456 782 lodged.", "123 456 782", "AU_TFN"),
        ],
    )
    def test_checksum_identifiers_keep_their_subtype(self, text, value, subtype):
        for entities in (_extract(text), _sweep(text)):
            findings = [e for e in entities if _subtype(e) is not None]
            assert [(e.text, _subtype(e)) for e in findings] == [(value, subtype)]

    def test_us_phone_still_detected_without_au_subtype(self):
        text = "Call 212-555-0199 for the US office."
        for entities in (_extract(text), _sweep(text)):
            phones = [e for e in entities if e.label == "phone_number"]
            assert [(e.text, _subtype(e)) for e in phones] == [("212-555-0199", None)]


class TestOcrSplitPhones:
    @pytest.mark.parametrize(
        "value",
        [
            "0412.345.678",
            "04 12 34 56 78",
            "0·4·1·2·3·4·5·6·7·8",
            "0412  345  678",
            "+61.412.345.678",
            "(02).9382.1111",
        ],
    )
    def test_ocr_split_phones_are_caught_with_context(self, value):
        text = f"Mobile {value} after 5pm."
        finding = _single(_sweep(text), text, value, AU_PHONE, "phone_number")
        assert finding.confidence >= 0.9

    @pytest.mark.parametrize(
        "text",
        [
            "Reference 0412.345.678 noted.",  # no phone context
            "Seen 04.12.2019 10:30, mobile clinic.",  # date + time, not a phone
            "Mobile 0612.345.678 invalid.",
            "Mobile 9 0412.345.678 logged.",  # fragment of a longer run
        ],
    )
    def test_ocr_split_rejections(self, text):
        assert _with_subtype(_sweep(text), AU_PHONE) == []


# ---------------------------------------------------------------------------
# Postcodes and states
# ---------------------------------------------------------------------------

_POSTCODE_TABLE = [
    # (text, state surface, postcode)
    ("Randwick NSW 2031", "NSW", "2031"),
    ("Parkville Vic. 3052", "Vic.", "3052"),
    ("Parkville VIC 3052", "VIC", "3052"),
    ("Parkville, Victoria 3052", "Victoria", "3052"),
    ("Randwick, New South Wales 2031", "New South Wales", "2031"),
    ("Brisbane QLD 4000", "QLD", "4000"),
    ("Adelaide SA 5000", "SA", "5000"),
    ("Perth WA 6000", "WA", "6000"),
    ("Hobart Tas 7000", "Tas", "7000"),
    ("Darwin NT 0800", "NT", "0800"),
    ("Canberra ACT 2600", "ACT", "2600"),
    ("Sydney N.S.W. 2000", "N.S.W.", "2000"),
]


@pytest.mark.usefixtures("no_model")
class TestPostcodes:
    @pytest.mark.parametrize(("text", "state", "postcode"), _POSTCODE_TABLE)
    def test_extract_and_sweep(self, text, state, postcode):
        line = f"Address: {text}."
        for entities in (_extract(line), _sweep(line)):
            _single(entities, line, postcode, AU_POSTCODE, "postcode")
            location = _single(entities, line, state, AU_STATE, "location")
            # State and postcode are adjacent, separate spans.
            assert location.end <= line.index(postcode, location.start)

    @pytest.mark.parametrize(("text", "state", "postcode"), _POSTCODE_TABLE)
    def test_pipeline_ccia_l3(self, text, state, postcode):
        line = f"Address: {text}."
        result = _pipeline_spans(line)
        by_label = {
            span.canonical_label: line[span.start : span.end]
            for span in result.spans
            if span.metadata.get("subtype") in {AU_POSTCODE, AU_STATE}
        }
        assert by_label == {"ZIPCODE": postcode, "LOCATION": state}
        assert postcode not in result.redacted_text

    def test_postcode_after_suburb_in_street_address(self):
        text = "Lives at 12 Smith St, Randwick 2031 with family."
        for entities in (_extract(text), _sweep(text)):
            _single(entities, text, "2031", AU_POSTCODE, "postcode")
            _single(
                entities, text, "12 Smith St, Randwick", AU_ADDRESS, "street_address"
            )

    @pytest.mark.parametrize(
        "text",
        [
            "Diagnosed in 2019 and reviewed in 2020.",
            "Metformin 2500 mg daily.",
            "Patient 3052 was reviewed.",
            "Bed 2031, ward 4.",
            "ID 3052",
            "NSW 3052 is not a NSW postcode.",  # state/postcode mismatch
            "QLD 2019 floods.",  # year after a state, outside QLD ranges
            "ACT 1200 seconds.",  # activated clotting time, not a postcode
            "SA node 5000 ms.",
            "Moved from Western Australia 6000 km away.",
            "SA 5000 ms pause on Holter.",
        ],
    )
    def test_negatives(self, text):
        for entities in (_extract(text), _sweep(text)):
            assert _with_subtype(entities, AU_POSTCODE) == []
            assert _with_subtype(entities, AU_STATE) == []

    def test_bare_state_name_in_prose_is_not_tagged(self):
        text = "Moved from Victoria to the NSW coast last year."
        for entities in (_extract(text), _sweep(text)):
            assert _contact_findings(entities) == []


# ---------------------------------------------------------------------------
# Street addresses
# ---------------------------------------------------------------------------

_ADDRESS_TABLE = [
    "3/12 Smith St",
    "Unit 3, 12 Smith Street",
    "Unit 3 12 Smith Street",
    "Apt 4B, 27 Wattle Avenue",
    "12A Smith Rd",
    "14-16 Example Ave",
    "5 Banksia Cres",
    "88 Ocean Pde",
    "1200 Pacific Hwy",
    "7 Jacaranda Ct",
    "9 Waratah Pl",
    "21 Federation Tce",
    "40 Bottlebrush Dr",
    "300 Example Blvd",
    "6 Lyrebird Cct",
    "2 Marine Esp",
    "11 Myrtle Gr",
    "3 Kookaburra La",
    "15 Lorikeet Way",
    "12 La Trobe St",
    "45 St Kilda Rd",
    "101 Mount Pleasant Road",
    "7 Example Crescent",
    "19 Example Parade",
    "8 Example Highway",
    "2 Example Court",
    "3 Example Place",
    "4 Example Terrace",
    "5 Example Drive",
    "6 Example Boulevard",
    "7 Example Circuit",
    "8 Example Esplanade",
    "9 Example Grove",
    "10 Example Lane",
    "12 O'Connell Street",
    "Lot 12 Smith Rd",
    "PO Box 123",
    "P.O. Box 4567",
    "GPO Box 1234",
    "Locked Bag 4",
    "RMB 123",
]


@pytest.mark.usefixtures("no_model")
class TestAddresses:
    @pytest.mark.parametrize("value", _ADDRESS_TABLE)
    def test_extract_and_sweep_single_span(self, value):
        text = f"Address: {value}. Next of kin notified."
        for entities in (_extract(text), _sweep(text)):
            _single(entities, text, value, AU_ADDRESS, "street_address")
            covering = [
                e
                for e in entities
                if e.start < text.index(value) + len(value)
                and e.end > text.index(value)
            ]
            assert len(covering) == 1, [(e.text, e.label) for e in covering]

    @pytest.mark.parametrize("value", _ADDRESS_TABLE)
    def test_pipeline_ccia_l3(self, value):
        text = f"Address: {value}. Next of kin notified."
        result = _pipeline_spans(text)
        start = text.index(value)
        spans = [
            span
            for span in result.spans
            if (span.start, span.end) == (start, start + len(value))
        ]
        assert len(spans) == 1, [(s.start, s.end, s.metadata) for s in result.spans]
        assert spans[0].canonical_label == "STREET_ADDRESS"
        assert spans[0].metadata["subtype"] == AU_ADDRESS
        assert value not in result.redacted_text

    def test_full_address_line_is_covered_whole(self):
        text = "Home: Unit 3, 12 Smith Street, Randwick NSW 2031. Ph 0412 345 678"
        expected = [
            ("Unit 3, 12 Smith Street, Randwick", AU_ADDRESS, "street_address"),
            ("NSW", AU_STATE, "location"),
            ("2031", AU_POSTCODE, "postcode"),
            ("0412 345 678", AU_PHONE, "phone_number"),
        ]
        for entities in (_extract(text), _sweep(text)):
            got = [(e.text, _subtype(e), e.label) for e in _contact_findings(entities)]
            assert got == expected

    def test_po_box_with_suburb_state_and_postcode(self):
        text = "Mail: PO Box 123, Randwick NSW 2031"
        for entities in (_extract(text), _sweep(text)):
            got = [(e.text, _subtype(e)) for e in _contact_findings(entities)]
            assert got == [
                ("PO Box 123, Randwick", AU_ADDRESS),
                ("NSW", AU_STATE),
                ("2031", AU_POSTCODE),
            ]

    def test_state_after_address_without_postcode(self):
        text = "Lives at 12 Smith St, Randwick NSW now."
        for entities in (_extract(text), _sweep(text)):
            _single(entities, text, "NSW", AU_STATE, "location")

    @pytest.mark.parametrize(
        "text",
        [
            "CT chest showed no change.",
            "CT 2 chest repeated.",
            "Dr Jones reviewed the patient.",
            "Admitted to St Vincent's Hospital.",
            "Transferred to St Vincent's Hospital for review.",
            "By the way, no pain today.",
            "Gr 2 lesion noted.",
            "La Trobe study cohort enrolled.",
            "2 HEAD CT scans this week.",
            "Given 2 Panadol Tabs.",
            "Took 3 Way Bridge to work.",
            "PO paracetamol 1 g.",
        ],
    )
    def test_negatives(self, text):
        for entities in (_extract(text), _sweep(text)):
            assert _with_subtype(entities, AU_ADDRESS) == []
            assert _contact_findings(entities) == []


# ---------------------------------------------------------------------------
# Non-AU locales are unchanged
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("no_model")
@pytest.mark.parametrize("locale", [None, "en_US", "en_GB"])
@pytest.mark.parametrize(
    "text",
    [
        "Mobile: 0412 345 678, Ph (02) 9382 1111, Fax: (03) 9123 4567",
        "1800 123 456, 1300 555 123, Phone 13 11 14, Ph: 9382 1111",
        "Unit 3, 12 Smith Street, Randwick NSW 2031. PO Box 123. Vic. 3052",
    ],
)
def test_non_au_locales_emit_no_au_contact_findings(locale, text):
    assert _contact_findings(_extract(text, locale=locale)) == []
    assert _contact_findings(_sweep(text, locale=locale)) == []
