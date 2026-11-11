"""AU-shaped surrogates for the ``en_AU`` anonymizer locale.

Every source value here is synthetic: checksum identifiers come from the repo
generators or were found by searching random digits with the repo validators,
AHPRA and DVA values are made-up grammar examples, and phones are from the
ACMA fictional ranges.
"""

from __future__ import annotations

import random
import re
import warnings

import pytest

from openmed.core.anonymizer import Anonymizer
from openmed.core.anonymizer.providers.au_surrogates import (
    ACMA_FICTIONAL_MOBILES,
    AU_SURROGATE_GENERATORS,
    AU_SURROGATE_LOCALE,
    AustralianSurrogateDispatcher,
    generate_ahpra_number,
    generate_australian_abn,
    generate_australian_acn,
    generate_australian_ihi,
    generate_australian_phone,
    generate_dva_number,
    generate_medicare_provider_number,
)
from openmed.core.anonymizer.providers.clinical_ids import (
    generate_australian_medicare,
    generate_australian_tfn,
    validate_australian_medicare,
    validate_australian_tfn,
)
from openmed.core.anonymizer.registry import LABEL_GENERATORS
from openmed.core.au_clinician_ids import (
    _DVA_VALUE,
    validate_medicare_provider_number,
)
from openmed.core.au_contact import validate_australian_phone
from openmed.core.au_identifiers import (
    validate_australian_abn,
    validate_australian_acn,
    validate_australian_ihi,
)

_DVA_RE = re.compile(_DVA_VALUE)
_AHPRA_RE = re.compile(r"[A-Z]{3}\d{10}")


def _valid_ahpra(value: str) -> bool:
    return _AHPRA_RE.fullmatch(value) is not None


def _valid_dva(value: str) -> bool:
    return _DVA_RE.fullmatch(value) is not None


VALIDATORS = {
    "MEDICARE_NUMBER": validate_australian_medicare,
    "TFN": validate_australian_tfn,
    "IHI": validate_australian_ihi,
    "ABN": validate_australian_abn,
    "ACN": validate_australian_acn,
    "AHPRA_NUMBER": _valid_ahpra,
    "DVA_NUMBER": _valid_dva,
    "MEDICARE_PROVIDER_NUMBER": validate_medicare_provider_number,
}

SOURCES = {
    "MEDICARE_NUMBER": ["3664737863", "3309721582", "4109466846"],
    "TFN": ["587150211", "580648486", "410946685"],
    "IHI": ["8003606566658120", "8003605506493820", "8003607310060530"],
    "ABN": ["15062984812", "77516277657", "31479508970"],
    "ACN": ["824646924", "428734678", "899430141"],
    "AHPRA_NUMBER": ["MED0001234567", "NMW0007654321", "PHY0002468024"],
    "DVA_NUMBER": ["NX123456", "VSR12345", "Q123456"],
    "MEDICARE_PROVIDER_NUMBER": ["1756048Y", "2505634W", "9620086H"],
}

# One synthetic value per label, recorded against HEAD 55a05163b.
GOLDEN_SOURCES = {
    "MEDICARE_NUMBER": "5877893259",
    "TFN": "977781880",
    "IHI": "8003607294055761",
    "ABN": "95576523735",
    "ACN": "884472940",
    "AHPRA_NUMBER": "MED0001234567",
    "DVA_NUMBER": "NX123456",
    "MEDICARE_PROVIDER_NUMBER": "8698797T",
    "PHONE": "0491 570 156",
}

# Anonymizer(lang="en", locale=loc, consistent=True, seed=7).surrogate(v, label)
# captured at HEAD 55a05163b, before the AU dispatcher existed.
_US_GOLDEN = {
    "MEDICARE_NUMBER": "004-02-4508",
    "TFN": "754-36-5074",
    "IHI": "782-76-3028",
    "ABN": "366-85-4803",
    "ACN": "822-09-8089",
    "AHPRA_NUMBER": "155-05-6090",
    "DVA_NUMBER": "773-20-8855",
    "MEDICARE_PROVIDER_NUMBER": "660-90-9864",
    "PHONE": "7028 096 913",
}
NON_AU_GOLDEN = {
    None: _US_GOLDEN,
    "en_US": _US_GOLDEN,
    "en_GB": {
        "MEDICARE_NUMBER": "AA 64 32 87 C",
        "TFN": "LM 57 16 36 B",
        "IHI": "YH 70 20 08 C",
        "ABN": "OM 40 76 42 D",
        "ACN": "CW 28 55 75 C",
        "AHPRA_NUMBER": "OL 19 66 24 A",
        "DVA_NUMBER": "GY 88 76 32 D",
        "MEDICARE_PROVIDER_NUMBER": "ZA 24 16 38 C",
        "PHONE": "7028 096 913",
    },
}

_ACMA_LANDLINE_RE = re.compile(r"0([2378]) (5550|7010) \d{4}")
_ACMA_MOBILE_DIGITS = {re.sub(r"\D", "", m) for m in ACMA_FICTIONAL_MOBILES}


def _anonymizer(seed: int = 7) -> Anonymizer:
    return Anonymizer(lang="en", locale="en_AU", consistent=True, seed=seed)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _is_acma(value: str) -> bool:
    digits = _digits(value)
    if digits in _ACMA_MOBILE_DIGITS:
        return True
    return _ACMA_LANDLINE_RE.fullmatch(value) is not None


@pytest.mark.parametrize("locale", [None, "en_US", "en_GB"])
def test_non_au_locale_unchanged(locale):
    anonymizer = Anonymizer(lang="en", locale=locale, consistent=True, seed=7)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        got = {
            label: anonymizer.surrogate(value, label)
            for label, value in GOLDEN_SOURCES.items()
        }
    assert got == NON_AU_GOLDEN[locale]


def test_registry_routes_au_labels_through_dispatcher():
    assert set(AU_SURROGATE_GENERATORS) == set(VALIDATORS) | {"PHONE"}
    assert AU_SURROGATE_LOCALE == "en_AU"
    for label in AU_SURROGATE_GENERATORS:
        assert isinstance(LABEL_GENERATORS[label], AustralianSurrogateDispatcher)


def test_source_fixtures_are_valid():
    for label, values in SOURCES.items():
        for value in values:
            assert VALIDATORS[label](value), label


@pytest.mark.parametrize("label", sorted(VALIDATORS))
def test_au_id_surrogates_pass_validators(label):
    anonymizer = _anonymizer()
    for source in SOURCES[label]:
        surrogate = anonymizer.surrogate(source, label)
        assert VALIDATORS[label](surrogate), label
        assert surrogate != source
        assert _digits(surrogate) != _digits(source)


@pytest.mark.parametrize(
    ("label", "pattern"),
    [
        ("MEDICARE_NUMBER", r"\d{4} \d{5} \d"),
        ("IHI", r"\d{4} \d{4} \d{4} \d{4}"),
        ("ABN", r"\d{2} \d{3} \d{3} \d{3}"),
        ("ACN", r"\d{3} \d{3} \d{3}"),
        ("TFN", r"\d{3} \d{3} \d{3}"),
    ],
)
def test_au_id_surrogates_use_canonical_layout(label, pattern):
    anonymizer = _anonymizer()
    for source in SOURCES[label]:
        assert re.fullmatch(pattern, anonymizer.surrogate(source, label))


def test_au_phone_surrogate_shape():
    anonymizer = _anonymizer()
    for mobile in ("0491 570 156", "0491570157", "+61 491 570 158"):
        surrogate = anonymizer.surrogate(mobile, "PHONE")
        assert re.fullmatch(r"04\d{2} \d{3} \d{3}", surrogate)
        assert validate_australian_phone(surrogate)
        assert _is_acma(surrogate)
        assert _digits(surrogate) != _digits(mobile).replace("61", "0", 1)

    for landline, area in (
        ("(03) 5550 1234", "3"),
        ("02 7010 9876", "2"),
        ("+61 8 5550 4321", "8"),
    ):
        surrogate = anonymizer.surrogate(landline, "PHONE")
        match = _ACMA_LANDLINE_RE.fullmatch(surrogate)
        assert match is not None, surrogate
        assert match.group(1) == area
        assert validate_australian_phone(surrogate)
        assert surrogate != landline


def test_au_phone_surrogate_never_echoes_acma_source():
    for mobile in ACMA_FICTIONAL_MOBILES:
        for seed in range(5):
            assert _digits(
                generate_australian_phone(mobile, rng=random.Random(seed))
            ) != _digits(mobile)


@pytest.mark.parametrize(
    "generate",
    [
        generate_australian_ihi,
        generate_australian_abn,
        generate_australian_acn,
        generate_medicare_provider_number,
        generate_ahpra_number,
        generate_dva_number,
        generate_australian_medicare,
        generate_australian_tfn,
    ],
)
def test_generators_pass_validators_many_seeds(generate):
    validators = {
        generate_australian_ihi: validate_australian_ihi,
        generate_australian_abn: validate_australian_abn,
        generate_australian_acn: validate_australian_acn,
        generate_medicare_provider_number: validate_medicare_provider_number,
        generate_ahpra_number: _valid_ahpra,
        generate_dva_number: _valid_dva,
        generate_australian_medicare: validate_australian_medicare,
        generate_australian_tfn: validate_australian_tfn,
    }
    validate = validators[generate]
    for seed in range(500):
        value = generate(rng=random.Random(seed))
        assert validate(value), value


@pytest.mark.parametrize(
    "source", ["0491 570 156", "(03) 5550 1234", "+61 7 7010 0000", "5550 1234"]
)
def test_phone_generator_passes_validator_many_seeds(source):
    for seed in range(500):
        value = generate_australian_phone(source, rng=random.Random(seed))
        assert validate_australian_phone(value), value
        digits = _digits(value)
        assert digits in _ACMA_MOBILE_DIGITS or digits[-8:-4] in {"5550", "7010"}


def test_au_surrogate_deterministic():
    values = [
        ("MEDICARE_NUMBER", SOURCES["MEDICARE_NUMBER"][0]),
        ("IHI", SOURCES["IHI"][0]),
        ("PHONE", "(03) 5550 1234"),
    ]
    first = [_anonymizer(7).surrogate(v, label) for label, v in values]
    second = [_anonymizer(7).surrogate(v, label) for label, v in values]
    other = [_anonymizer(8).surrogate(v, label) for label, v in values]
    assert first == second
    assert first != other


def test_dispatcher_routes_only_en_au():
    calls = []

    def au(faker, original, *, locale):
        calls.append(("au", locale))
        return "au"

    def fallback(faker, original, *, locale):
        calls.append(("fallback", locale))
        return "fallback"

    dispatcher = AustralianSurrogateDispatcher(generator=au, fallback=fallback)
    assert dispatcher(None, "x", locale="en_AU") == "au"
    for locale in ("en_US", "en_GB", "en_NZ", "en"):
        assert dispatcher(None, "x", locale=locale) == "fallback"
