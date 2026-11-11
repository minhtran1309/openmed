"""Australian surrogate generators for the ``en_AU`` anonymizer locale.

Before this module, ``Anonymizer(locale="en_AU")`` sent every AU identifier
label to the generic ID surrogate, which is format-shaped but fails its
checksum, and sent ``PHONE`` to a generic shape that is not an Australian
number. The generators here produce values that pass the repo validators:

* IHI: prefix ``800360`` and a Luhn check digit
  (:func:`openmed.core.au_identifiers.validate_australian_ihi`).
* ABN: mod-89 check pair (``validate_australian_abn``).
* ACN: weighted check digit (``validate_australian_acn``).
* Medicare provider number: mod-11 check character
  (:func:`openmed.core.au_clinician_ids.validate_medicare_provider_number`).
* AHPRA registration number: ``MED`` and ten digits. AHPRA publishes no
  checksum, so only the grammar is enforced.
* DVA file number: the strict METeOR 339127 grammar used by the DVA
  recogniser, with no dependant segment letter.
* Medicare and TFN reuse :func:`generate_australian_medicare` and
  :func:`generate_australian_tfn` from :mod:`.clinical_ids`.
* Phone: numbers only from the ACMA ranges reserved for fiction. A mobile
  source gets a fictional mobile, a geographic source gets a fictional
  landline with the same area code, a bare local number gets a fictional
  local number, and a ``1800``/``1300`` source gets a fictional number of the
  same kind. Any other source gets a fictional mobile. The ranges were
  checked against ACMA, "Phone numbers for use in TV shows, films and
  creative works",
  https://www.acma.gov.au/phone-numbers-use-tv-shows-films-and-creative-works
  (page last updated 3 September 2026, read 11 November 2026).

Surrogates use the canonical display layout (Medicare ``#### ##### #``, IHI
``#### #### #### ####``, ABN ``## ### ### ###``, ACN and TFN
``### ### ###``, mobile ``04## ### ###``, landline ``0# #### ####``). The
source layout is never copied, so the surrogate depends only on the seed.
A surrogate whose characters equal the source is redrawn.

:class:`AustralianSurrogateDispatcher` routes only the ``en_AU`` locale to
these generators. Every other locale reaches the previously registered
generator with the same Faker state, so its output is unchanged.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable, Final, Mapping, Sequence

from openmed.core import labels as L
from openmed.core.au_clinician_ids import _CHECK_CHARS, _PLV, _STEM_WEIGHTS

from .clinical_ids import (
    _luhn_check_digit,
    generate_australian_medicare,
    generate_australian_tfn,
)

AU_SURROGATE_LOCALE: Final[str] = "en_AU"
"""The only locale whose surrogates come from this module."""

ACMA_FICTIONAL_MOBILES: Final[tuple[str, ...]] = (
    "0491 570 006",
    "0491 570 156",
    "0491 570 157",
    "0491 570 158",
    "0491 570 159",
    "0491 570 110",
    "0491 570 313",
    "0491 570 737",
    "0491 571 266",
    "0491 571 491",
    "0491 571 804",
    "0491 572 549",
    "0491 572 665",
    "0491 572 983",
    "0491 573 770",
    "0491 573 087",
    "0491 574 118",
    "0491 574 632",
    "0491 575 254",
    "0491 575 789",
    "0491 576 398",
    "0491 576 801",
    "0491 577 426",
    "0491 577 644",
    "0491 578 957",
    "0491 578 148",
    "0491 578 888",
    "0491 579 212",
    "0491 579 760",
    "0491 579 455",
)
"""The 30 mobile numbers ACMA reserves for fiction."""

ACMA_FICTIONAL_LANDLINE_PREFIXES: Final[tuple[str, ...]] = ("5550", "7010")
"""Local-number prefixes ACMA reserves for fiction in areas 02, 03, 07, 08."""

ACMA_FICTIONAL_FREEPHONES: Final[tuple[str, ...]] = (
    "1800 160 401",
    "1800 975 707",
    "1800 975 708",
    "1800 975 709",
    "1800 975 710",
    "1800 975 711",
)
"""The ``1800`` numbers ACMA reserves for fiction."""

ACMA_FICTIONAL_LOCAL_RATE: Final[tuple[str, ...]] = (
    "1300 975 707",
    "1300 975 708",
    "1300 975 709",
    "1300 975 710",
    "1300 975 711",
)
"""The ``1300`` numbers ACMA reserves for fiction."""

_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
_ACN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)
_IHI_PREFIX = "800360"
_PLV_CHARS = "".join(_PLV)
_DVA_STATES = "NVQWST"
# Letters for DVA war codes; ``I`` and ``O`` are left out to avoid 1/0 lookalikes.
_DVA_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_GEOGRAPHIC_AREAS = frozenset("2378")
_MAX_ATTEMPTS = 20
_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]")
_NON_DIGIT_RE = re.compile(r"\D")


def _rng(rng: random.Random | None) -> random.Random:
    return rng if rng is not None else random.Random()


def _digits(source: random.Random, count: int) -> list[int]:
    return [source.randint(0, 9) for _ in range(count)]


def _join(values: Sequence[int]) -> str:
    return "".join(str(value) for value in values)


def _group(value: str, sizes: Sequence[int]) -> str:
    parts = []
    start = 0
    for size in sizes:
        parts.append(value[start : start + size])
        start += size
    return " ".join(parts)


def generate_australian_ihi(*, rng: random.Random | None = None) -> str:
    """Generate an IHI accepted by ``validate_australian_ihi``.

    Args:
        rng: Random source; a fresh unseeded one when omitted.

    Returns:
        Sixteen digits: ``800360``, nine random digits and a Luhn check digit.
    """
    source = _rng(rng)
    body = [int(char) for char in _IHI_PREFIX] + _digits(source, 9)
    return _join(body) + str(_luhn_check_digit(body))


def generate_australian_abn(*, rng: random.Random | None = None) -> str:
    """Generate an ABN accepted by ``validate_australian_abn``.

    The nine trailing digits are random. The leading check pair is the only
    value in ``10..99`` that makes the weighted sum divisible by 89.

    Args:
        rng: Random source; a fresh unseeded one when omitted.

    Returns:
        Eleven digits with no separators.
    """
    source = _rng(rng)
    tail = _digits(source, 9)
    remainder = sum(w * v for w, v in zip(_ABN_WEIGHTS[2:], tail)) % 89
    pair = (-remainder) % 89
    first, second = pair // 10 + 1, pair % 10
    return _join([first, second, *tail])


def generate_australian_acn(*, rng: random.Random | None = None) -> str:
    """Generate an ACN accepted by ``validate_australian_acn``.

    Args:
        rng: Random source; a fresh unseeded one when omitted.

    Returns:
        Nine digits with no separators; the last is the check digit.
    """
    source = _rng(rng)
    body = _digits(source, 8)
    while not any(body):
        body = _digits(source, 8)
    check = (10 - sum(w * v for w, v in zip(_ACN_WEIGHTS, body)) % 10) % 10
    return _join([*body, check])


def generate_medicare_provider_number(*, rng: random.Random | None = None) -> str:
    """Generate a provider number accepted by ``validate_medicare_provider_number``.

    Args:
        rng: Random source; a fresh unseeded one when omitted.

    Returns:
        A six-digit stem, a practice location character and the check
        character, with no separators.
    """
    source = _rng(rng)
    stem = _digits(source, 6)
    while not any(stem):
        stem = _digits(source, 6)
    location = source.choice(_PLV_CHARS)
    total = sum(w * d for w, d in zip(_STEM_WEIGHTS, stem)) + 6 * _PLV[location]
    return _join(stem) + location + _CHECK_CHARS[total % 11]


def generate_ahpra_number(*, rng: random.Random | None = None) -> str:
    """Generate an AHPRA registration number: ``MED`` and ten digits.

    Args:
        rng: Random source; a fresh unseeded one when omitted.

    Returns:
        A value matching ``[A-Z]{3}\\d{10}``. There is no checksum.
    """
    return "MED" + _join(_digits(_rng(rng), 10))


def generate_dva_number(*, rng: random.Random | None = None) -> str:
    """Generate a DVA file number in the strict METeOR 339127 grammar.

    A state letter (``N V Q W S T``) is followed by one war-code letter and
    six digits, two letters and five digits, three letters and four digits,
    or six digits with no war code. No dependant segment letter is added.

    Args:
        rng: Random source; a fresh unseeded one when omitted.

    Returns:
        A value accepted by the DVA recogniser's value grammar.
    """
    source = _rng(rng)
    letters = source.randint(0, 3)
    war_code = "".join(source.choice(_DVA_LETTERS) for _ in range(letters))
    digit_count = 6 if letters == 0 else 7 - letters
    return source.choice(_DVA_STATES) + war_code + _join(_digits(source, digit_count))


def _national_digits(original: str) -> str:
    digits = _NON_DIGIT_RE.sub("", original)
    if original.strip().startswith("+61") and digits.startswith("61"):
        rest = digits[2:]
        return rest if rest.startswith("0") else "0" + rest
    return digits


def _fictional_landline(source: random.Random, area: str | None) -> str:
    prefix = source.choice(ACMA_FICTIONAL_LANDLINE_PREFIXES)
    line = _join(_digits(source, 4))
    if area is None:
        return f"{prefix} {line}"
    return f"0{area} {prefix} {line}"


def _draw_phone(national: str, source: random.Random) -> str:
    if len(national) == 10 and national.startswith("04"):
        return source.choice(ACMA_FICTIONAL_MOBILES)
    if len(national) == 10 and national[0] == "0" and national[1] in _GEOGRAPHIC_AREAS:
        return _fictional_landline(source, national[1])
    if len(national) == 8 and national[0] in "23456789":
        return _fictional_landline(source, None)
    if len(national) == 10 and national.startswith("1800"):
        return source.choice(ACMA_FICTIONAL_FREEPHONES)
    if len(national) == 10 and national.startswith("1300"):
        return source.choice(ACMA_FICTIONAL_LOCAL_RATE)
    return source.choice(ACMA_FICTIONAL_MOBILES)


def generate_australian_phone(
    original: str, *, rng: random.Random | None = None
) -> str:
    """Generate a fictional Australian phone number shaped like ``original``.

    Only ACMA's fictional ranges are used (see the module docstring). A
    mobile source gives a mobile, a geographic source gives a landline with
    the same area code, a bare eight-digit local number gives a local number,
    and a ``1800``/``1300`` source gives a number of that kind. Anything else
    gives a mobile. ``+61`` sources are read as national numbers and the
    surrogate uses the national layout. A draw equal to the source is
    redrawn.

    Args:
        original: The source number as printed.
        rng: Random source; a fresh unseeded one when omitted.

    Returns:
        A fictional number in the canonical layout.

    Raises:
        RuntimeError: If no draw differs from the source.
    """
    source = _rng(rng)
    national = _national_digits(original)
    for _ in range(_MAX_ATTEMPTS):
        candidate = _draw_phone(national, source)
        if _NON_DIGIT_RE.sub("", candidate) != national:
            return candidate
    raise RuntimeError("could not generate a phone surrogate distinct from source")


def _compact(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value).upper()


@dataclass(frozen=True)
class _IdentifierSurrogate:
    """Adapt an ``rng``-only generator to the registry generator signature."""

    generate: Callable[..., str]
    layout: tuple[int, ...] = ()

    def __call__(self, faker, original: str, *, locale: str) -> str:
        source = _compact(original)
        for _ in range(_MAX_ATTEMPTS):
            value = self.generate(rng=faker.random)
            # A Medicare source can carry a trailing IRN, so a surrogate that
            # is a prefix of the source would still restate the card number.
            if value and not source.startswith(value.upper()):
                return _group(value, self.layout) if self.layout else value
        raise RuntimeError("could not generate a surrogate distinct from source")


def _phone_surrogate(faker, original: str, *, locale: str) -> str:
    return generate_australian_phone(original, rng=faker.random)


AU_SURROGATE_GENERATORS: Final[Mapping[str, Callable[..., str]]] = {
    L.MEDICARE_NUMBER: _IdentifierSurrogate(generate_australian_medicare, (4, 5, 1)),
    L.TFN: _IdentifierSurrogate(generate_australian_tfn, (3, 3, 3)),
    L.IHI: _IdentifierSurrogate(generate_australian_ihi, (4, 4, 4, 4)),
    L.ABN: _IdentifierSurrogate(generate_australian_abn, (2, 3, 3, 3)),
    L.ACN: _IdentifierSurrogate(generate_australian_acn, (3, 3, 3)),
    L.AHPRA_NUMBER: _IdentifierSurrogate(generate_ahpra_number),
    L.DVA_NUMBER: _IdentifierSurrogate(generate_dva_number),
    L.MEDICARE_PROVIDER_NUMBER: _IdentifierSurrogate(generate_medicare_provider_number),
    L.PHONE: _phone_surrogate,
}
"""``en_AU`` generators per canonical label, in the registry signature."""


@dataclass(frozen=True)
class AustralianSurrogateDispatcher:
    """Route the ``en_AU`` locale to an Australian surrogate generator.

    Every other locale reaches ``fallback`` unchanged, with the same Faker
    state, so non-AU surrogates stay byte-for-byte identical.

    Attributes:
        generator: Australian generator for :data:`AU_SURROGATE_LOCALE`.
        fallback: Generator previously registered for the label.
    """

    generator: Callable[..., str]
    fallback: Callable[..., str]

    def __call__(self, faker, original: str, *, locale: str) -> str:
        """Return a surrogate from the Australian or fallback generator."""

        if locale == AU_SURROGATE_LOCALE:
            return self.generator(faker, original, locale=locale)
        return self.fallback(faker, original, locale=locale)


__all__ = [
    "ACMA_FICTIONAL_FREEPHONES",
    "ACMA_FICTIONAL_LANDLINE_PREFIXES",
    "ACMA_FICTIONAL_LOCAL_RATE",
    "ACMA_FICTIONAL_MOBILES",
    "AU_SURROGATE_GENERATORS",
    "AU_SURROGATE_LOCALE",
    "AustralianSurrogateDispatcher",
    "generate_ahpra_number",
    "generate_australian_abn",
    "generate_australian_acn",
    "generate_australian_ihi",
    "generate_australian_phone",
    "generate_dva_number",
    "generate_medicare_provider_number",
]
