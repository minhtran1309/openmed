"""Synthetic Australian pathology/radiology report header.

Every name, date, address and number is invented. The Medicare value is
computed with the Medicare validator, not copied from a card. Shared by the
``AU_HEADER`` pattern tests and the clinical header-field detector tests.
"""

from __future__ import annotations

from openmed.core.anonymizer.providers.clinical_ids import (
    validate_australian_medicare,
)


def _medicare() -> str:
    for candidate in range(2_987_654_310, 2_987_655_310):
        digits = str(candidate)
        if validate_australian_medicare(digits):
            return f"{digits[:4]} {digits[4:9]} {digits[9]}"
    raise AssertionError("no synthetic value found")


MEDICARE = _medicare()

# A synthetic pathology/radiology report header. Every story 16 label appears,
# in ``Label: value``, tab-separated and two-space forms, with both
# ``CITIZEN, Jane`` and ``Jane CITIZEN`` name orders.
HEADER = (
    "NORTHGATE PATHOLOGY  CUMULATIVE REPORT\n"
    "Patient Name:\tCITIZEN, Jane\n"
    "Name: Jane CITIZEN\n"
    "Surname:\tQUANDT\n"
    "Given Name(s): Zelphira Mae\n"
    "Given Names:  Orla\n"
    "D.O.B: 04/07/1961\tSex: F\n"
    "DOB\t12-Mar-58\n"
    "Date of Birth: 3 July 1961\n"
    "Address: Unit 3, 12 Smith Street, Randwick NSW 2031\n"
    "UR: 0012345\tURN: A123456\n"
    f"Medicare No: {MEDICARE}\n"
    "Ph: 0412 345 678\tMob: 0413 222 333\n"
    "Phone: (02) 9876 5432\tFax: 02 9876 5433\n"
    "Mobile: 0414 555 666\n"
    "NOK: Orlan QUANDT\tPh: 0415 777 888\n"
    "Next of Kin:  Bede Harrow-Lyle\n"
    "Referring Doctor: Dr Tamsin VELLACOTT\n"
    "Referred by:\tDr. Imelda O'Fenwick\n"
    "Reported by: Prof Corwin Ashgrove\n"
    "Requesting Clinician:  Dr Odile McTavish\n"
    "GP: Dr Wren Pellow\n"
)

HEADER_VALUES = {
    ("CITIZEN, Jane", "PERSON"),
    ("Jane CITIZEN", "PERSON"),
    ("QUANDT", "LAST_NAME"),
    ("Zelphira Mae", "FIRST_NAME"),
    ("Orla", "FIRST_NAME"),
    ("04/07/1961", "DATE_OF_BIRTH"),
    ("12-Mar-58", "DATE_OF_BIRTH"),
    ("3 July 1961", "DATE_OF_BIRTH"),
    ("Unit 3, 12 Smith Street, Randwick NSW 2031", "STREET_ADDRESS"),
    ("Orlan QUANDT", "PERSON"),
    ("Bede Harrow-Lyle", "PERSON"),
    ("Tamsin VELLACOTT", "PERSON"),
    ("Imelda O'Fenwick", "PERSON"),
    ("Corwin Ashgrove", "PERSON"),
    ("Odile McTavish", "PERSON"),
    ("Wren Pellow", "PERSON"),
}
# Values covered by Task 1 (UR/URN) and the existing Medicare and phone
# patterns; the fixture proves they still work in the header layout.
EXISTING_VALUES = {
    ("0012345", "ID_NUM"),
    ("A123456", "ID_NUM"),
    (MEDICARE, "MEDICARE_NUMBER"),
    ("0412 345 678", "PHONE"),
    ("0413 222 333", "PHONE"),
    ("(02) 9876 5432", "PHONE"),
    ("02 9876 5433", "PHONE"),
    ("0414 555 666", "PHONE"),
    ("0415 777 888", "PHONE"),
}
EXISTING_SUBTYPES = {"AU_UR", "AU_MEDICARE", "AU_PHONE"}
