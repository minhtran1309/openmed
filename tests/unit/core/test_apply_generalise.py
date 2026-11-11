"""Generalisers for the ``generalise`` treatment (mt.3 E10).

Every value here is synthetic. Phones come from the ACMA fictional ranges
(``0491 570 xxx`` mobiles and ``(0X) 5550 xxxx`` landlines are the published
fictional ranges; the brief's illustrative values are kept where the brief
gives them verbatim).
"""

from __future__ import annotations

import pytest

from openmed.core.apply_generalise import (
    AU_STATES,
    generalise_address,
    generalise_dob,
    generalise_organisation,
    generalise_phone,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0412 345 678", "04xx xxx xxx"),
        ("(02) 9876 5432", "(02) xxxx xxxx"),
        ("+61 412 345 678", "+61 4xx xxx xxx"),
        ("0491 570 156", "04xx xxx xxx"),
        ("03 5550 1234", "03 xxxx xxxx"),
        ("0412-345-678", "04xx-xxx-xxx"),
        ("+61 (0)2 5550 1234", "+61 (0)2 xxxx xxxx"),
        ("+61412345678", "+614xxxxxxxx"),
    ],
)
def test_generalise_phone(value: str, expected: str) -> None:
    assert generalise_phone(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "+1 415 555 0100",
        "1300 123 456",
        "13 11 14",
        "5550 1234",
        "not a phone",
        "",
    ],
)
def test_generalise_phone_rejects_non_au(value: str) -> None:
    """A bare local number has no area code to keep, so it falls back to a tag."""
    assert generalise_phone(value) is None


@pytest.mark.parametrize(
    ("value", "offset", "expected"),
    [
        ("04/07/2015", 30, "2015"),
        ("15/12/2015", 30, "2016"),
        ("10/01/2016", -30, "2015"),
        ("2015-07-04", 30, "2015"),
        ("4 July 2015", 30, "2015"),
        ("20th December 2015", 30, "2016"),
        ("July 4, 2015", 200, "2016"),
        ("04/07/1948", 10, "1948"),
    ],
)
def test_generalise_dob_uses_shifted_year(
    value: str, offset: int, expected: str
) -> None:
    assert generalise_dob(value, offset) == expected


@pytest.mark.parametrize(
    "value",
    [
        "July 2015",
        "2015",
        "31/02/2015",
        "",
        "unknown",
        # Fewer than four year digits: the century is a guess (``48`` would
        # read as 2048, a DOB in the future), so fall back to a tag.
        "12/03/48",
        "04/07/49",
        "04/07/51",
        "04/07/15",
        "4 July 15",
        "04/07/0015",
    ],
)
def test_generalise_dob_unparseable(value: str) -> None:
    assert generalise_dob(value, 30) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12 Smith St, Carlton VIC 3053", "Carlton VIC"),
        ("12 Smith St, Fitzroy VIC 3065", "Fitzroy VIC"),
        ("Unit 4/12 Smith Street Fitzroy VIC 3065", "Fitzroy VIC"),
        ("12 Smith St Carlton VIC 3053", "Carlton VIC"),
        ("12 Smith St, Fitzroy, Victoria", "Fitzroy Victoria"),
        ("VIC", "VIC"),
        ("New South Wales", "New South Wales"),
        ("Unit 4, 12 Smith St, St Kilda East VIC 3183", "St Kilda East VIC"),
        ("12 Smith St\nSt Albans VIC", "St Albans VIC"),
        ("12 SMITH ST, CARLTON VIC 3053", "CARLTON VIC"),
        ("12 Smith St, Carlton  VIC 3053", "Carlton VIC"),
        ("12 Smith St, Carlton Vic 3053", "Carlton Vic"),
        ("12 Smith Rd Glenelg SA 5045", "Glenelg SA"),
        ("12 Smith St, Glenelg South Australia", "Glenelg South Australia"),
        ("12 Smith St, Mount Waverley VIC 3149", "Mount Waverley VIC"),
        ("4/12 Smith Street, Carlton, VIC, 3053", "Carlton VIC"),
        ("12 St Kilda Rd St Kilda VIC 3182", "St Kilda VIC"),
    ],
)
def test_generalise_address(value: str, expected: str) -> None:
    assert generalise_address(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "12 Smith St",
        "Smith Street Carlton VIC",
        # Round 2: a postcode alone does not identify the suburb.
        "Jane Doe VIC 3053",
        "Lane Cove NSW 2066",
        "Carlton VIC 3053",
        "CARLTON VIC 3053",
        "Mount Waverley VIC 3149",
        "12 Smith St, Rose Cottage, Carlton VIC 3053",
        "12 Smith St",
        "12 Smith St, VIC 3053",
        "Carlton 3053",
        "Carlton VIC 3053, Australia",
        "VIC 3053",
        "3053",
        "",
        # Review I1: street, property and person names must not survive.
        "Smith Track, VIC",
        "Lot Seven Smith Track, VIC",
        "Smith Mews, VIC",
        "Smith Alley, VIC",
        "Smith Loop, VIC",
        "Smith Walk, VIC",
        "Smith Row, VIC",
        "Smith Square, VIC",
        "Smith Gardens, VIC",
        "Smith Circle, VIC",
        "Smith Boulevarde, VIC",
        "Smith Carlton VIC",
        "Rose Cottage, VIC",
        "RMB Smith, VIC",
        "John Smith, VIC",
        "Jane Doe VIC",
        # A lone ``<words>, STATE`` has no postcode or street segment.
        "Fitzroy, Victoria",
        "Carlton VIC",
        "Glenelg South Australia",
        # The preceding segment must be a numbered street.
        "Rose Cottage, Carlton VIC",
        "John Smith, Rose Cottage, VIC",
        # Postal and rural prefixes.
        "PO Box 12, Box Hill, VIC, 3128",
        "P.O. Box 12, Carlton VIC 3053",
        "GPO Box 1, Carlton VIC 3053",
        "Locked Bag 4, Carlton VIC 3053",
        "Private Bag 4, Carlton VIC 3053",
        "RMB 12, Carlton VIC 3053",
        "Lot 7, Carlton VIC 3053",
        "Unit 4, Carlton VIC 3053",
        "Flat 2, Carlton VIC 3053",
        # A street type anywhere in the suburb, even after a postcode.
        "Smith Track VIC 3053",
        "12 Smith St, Smith Track, VIC",
    ],
)
def test_generalise_address_fails_closed(value: str) -> None:
    """Fail closed: return ``None`` unless the suburb is clearly identified.

    A suburb is only rendered after a street part (house number and
    street type); a postcode alone does not identify it.
    """
    assert generalise_address(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "12 Smith St, Box Hill VIC 3128",
        "12 Smith St, Lane Cove NSW 2066",
        "12 Smith St, Victoria Park WA 6100",
        "12 Smith St, Albert Park VIC 3206",
        "12 Smith St, Bondi Beach NSW 2026",
    ],
)
def test_generalise_address_suburbs_with_street_type_words(value: str) -> None:
    """Accepted over-redaction: suburbs whose later words are Australia Post
    street types (``Hill``, ``Cove``, ``Park``, ``Beach``) fall back to a tag.
    Without a gazetteer they cannot be told apart from a street name."""
    assert generalise_address(value) is None


def test_au_states() -> None:
    assert AU_STATES == frozenset({"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Royal Children's Hospital", "hospital"),
        ("Carlton Medical Centre", "medical centre"),
        ("Carlton Medical Center", "medical centre"),
        ("Northern Health Service", "health service"),
        ("Fitzroy Skin Clinic", "clinic"),
        ("Carlton General Practice", "general practice"),
        ("Smith Family Practice", "practice"),
        ("Acme Pathology", "pathology"),
        ("Carlton Imaging", "radiology"),
        ("Fitzroy Radiology", "radiology"),
        ("Acme Laboratory", "laboratory"),
        ("Carlton Pharmacy", "pharmacy"),
        ("Fitzroy Primary School", "school"),
        ("University of Carlton", "university"),
        ("ROYAL CARLTON HOSPITAL", "hospital"),
        ("Hospital Pharmacy", "hospital"),
    ],
)
def test_generalise_organisation(value: str, expected: str) -> None:
    assert generalise_organisation(value) == expected


@pytest.mark.parametrize("value", ["Acme Pty Ltd", "Hospitality Group", "", "Clinical"])
def test_generalise_organisation_unknown(value: str) -> None:
    """Keywords are word-bounded, so ``Hospitality`` is not a hospital."""
    assert generalise_organisation(value) is None
