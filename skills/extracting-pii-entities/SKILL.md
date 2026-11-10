---
name: extracting-pii-entities
description: "Detect PHI/PII spans in clinical text with OpenMed's extract_pii without altering the text. Use when the user wants to find names, dates, MRNs, phone numbers, addresses, US SSNs, Australian identifiers (Medicare, TFN, IHI, ABN, ACN, AHPRA, DVA, Medicare provider numbers), or other identifiers and get their offsets and labels (not redact them), inspect what would be removed before de-identifying, route spans to a custom redactor, normalize labels to a canonical taxonomy, or filter by confidence, language and locale. Covers extract_pii, the returned entity fields, CANONICAL_LABELS / normalize_label (SSN, MEDICARE_NUMBER, TFN, IHI, ABN, ACN, AHPRA_NUMBER, DVA_NUMBER, MEDICARE_PROVIDER_NUMBER, ID_NUM), and how it differs from deidentify. Pairs before reidentifying-text and deidentifying-clinical-text."
license: Apache-2.0
metadata:
  project: OpenMed
  category: openmed-core
  pairs: adjacent
  version: "1.1-mt.2"
---

# Extracting PII Entities

`openmed.extract_pii` finds PHI/PII spans and **returns them without changing the
text**. Use it when you need to *see* the identifiers — to audit, route to a
custom redactor, or decide a policy — rather than produce redacted output. It runs
on-device.

## When to use

- You want the **spans and labels** of identifiers, with the original text intact.
- You need a **preview** of what `deidentify` would act on before committing.
- You are feeding detected spans into a **downstream redactor** (your own,
  Presidio, or `deidentify`).
- You want to **normalize** model labels to a stable canonical taxonomy.
- You need **country-specific identifiers** such as a US `SSN` or the Australian
  Medicare, TFN, IHI, ABN and ACN numbers, or the AHPRA registration, DVA file
  and Medicare provider numbers.

If you instead want redacted/masked output directly, use
`deidentifying-clinical-text` (`openmed.deidentify`). If you need reversible
masking, see `reidentifying-text`.

## extract_pii vs deidentify

| | `extract_pii` | `deidentify` |
| --- | --- | --- |
| Changes the text? | **No** | Yes (mask/remove/replace/hash/shift) |
| Returns | `PredictionResult` (spans) | `DeidentificationResult` (redacted text) |
| Default threshold | `0.5` | `0.7` (safety-biased) |
| Use for | detection, audit, routing | producing safe output |

## Install

```bash
pip install "openmed[hf]"
```

## Quick start

```python
import openmed

note = "Patient John Doe (MRN 00481726), DOB 1970-01-15, phone 617-555-0142."

result = openmed.extract_pii(note, confidence_threshold=0.5)

for ent in result.entities:
    print(f"{ent.label:10} {ent.text!r:18} {ent.confidence:.2f} [{ent.start}:{ent.end}]")
```

`extract_pii(...)` returns a `PredictionResult`. Its `.entities` are
`EntityPrediction` objects (synthetic example fields shown):

```text
ent.text            # the identifier surface string, e.g. "617-555-0142"
ent.label           # detected (source) label, e.g. "phone" or "medicare_number"
ent.confidence      # score in [0, 1]   (NOTE: .confidence, not .score)
ent.start / ent.end # character offsets into the original note
ent.metadata        # dict or None; pattern hits may carry metadata["subtype"]
```

`ent.label` is the detector's own spelling. Map it to the canonical taxonomy with
`normalize_label(ent.label)` (next section). The text is unchanged —
`result.text` is your original input.

## Signature & key parameters

```python
openmed.extract_pii(
    text,
    model_name="OpenMed/OpenMed-PII-SuperClinical-Small-44M-v1",  # default EN model
    confidence_threshold=0.5,     # raise for precision, lower for recall
    use_smart_merging=True,       # merge fragmented spans into whole units
    lang="en",                    # en es pt fr de it nl hi te ar tr ja
    locale=None,                  # e.g. "en_AU" enables the Australian patterns
    loader=None,                  # reuse a ModelLoader across calls
)
```

- **`use_smart_merging=True`** (default) reassembles fragmented predictions into
  complete units (a full phone number, a full date) — keep it on.
- **`lang`** selects the language-appropriate default model and regex patterns.
  Pass the right language; do not run the English model on non-English text. Use
  `openmed.get_default_pii_model(lang)` to confirm coverage.
- **`locale`** adds country-specific patterns on top of the language. The
  Australian identifier patterns run only with `locale="en_AU"`; other locales
  never emit the AU labels below.

## Normalize labels to the canonical taxonomy

Different models may emit slightly different label spellings. Normalize them to
OpenMed's canonical set so downstream logic is stable:

```python
import openmed
from openmed import CANONICAL_LABELS, normalize_label

result = openmed.extract_pii("Email jane.roe@example.org; SSN 123-45-6789.")

for ent in result.entities:
    canon = normalize_label(ent.label)
    assert canon in CANONICAL_LABELS
    print(ent.text, "->", canon)      # jane.roe@example.org -> EMAIL
                                      # 123-45-6789 -> SSN
```

`CANONICAL_LABELS` is a frozenset of UPPER_SNAKE_CASE labels (e.g. `PERSON`,
`DATE`, `PHONE`, `EMAIL`, `ID_NUM`, `LOCATION`). `normalize_label(label)`
accepts messy inputs (`"FIRSTNAME"`, `"first_name"`, `"B-EMAIL"`) and maps unknown
labels to `OTHER` rather than raising.

Identifier labels:

| Canonical label | Identifier | Subtype (`metadata["subtype"]`) | Policy class |
| --- | --- | --- | --- |
| `SSN` | US Social Security number | — | `DIRECT_IDENTIFIER` |
| `MEDICARE_NUMBER` | Australian Medicare card number | `AU_MEDICARE` | `DIRECT_IDENTIFIER` |
| `TFN` | Australian Tax File Number | `AU_TFN` | `DIRECT_IDENTIFIER` |
| `IHI` | Australian Individual Healthcare Identifier | `AU_IHI` | `DIRECT_IDENTIFIER` |
| `ABN` | Australian Business Number | `AU_ABN` | `DIRECT_IDENTIFIER` |
| `ACN` | Australian Company Number | `AU_ACN` | `DIRECT_IDENTIFIER` |
| `AHPRA_NUMBER` | Australian AHPRA practitioner registration number | `AHPRA` | `DIRECT_IDENTIFIER` |
| `DVA_NUMBER` | Australian Department of Veterans' Affairs file number | `AU_DVA` | `DIRECT_IDENTIFIER` |
| `MEDICARE_PROVIDER_NUMBER` | Australian Medicare provider / prescriber number | `AU_PROVIDER` | `DIRECT_IDENTIFIER` |
| `ID_NUM` | Other identifiers (MRNs, national IDs, passports, NPI, ...) | — | `DIRECT_IDENTIFIER` |

The AU labels sit next to `SSN`, which stays the US label. Aliases are
case- and separator-insensitive: `normalize_label("medicare_no")`,
`"Medicare Number"` and `"MEDICARE_NUMBER"` all give `MEDICARE_NUMBER`;
`"tax_file_number"` gives `TFN`; `"individual_healthcare_identifier"` gives
`IHI`; `"australian_business_number"` / `"australian_company_number"` give
`ABN` / `ACN`; `"ahpra_registration_number"` gives `AHPRA_NUMBER`;
`"dva_file_number"` gives `DVA_NUMBER`; and `"provider_number"` /
`"prescriber_number"` give `MEDICARE_PROVIDER_NUMBER` (a Medicare *card* number
stays `MEDICARE_NUMBER`). Read the policy class with
`openmed.core.labels.policy_label_for(label)`.

## Australian identifiers (`locale="en_AU"`)

The AU patterns validate each number's checksum and match the value only, never
its field label. All values below are synthetic and checksum-valid:

```python
import openmed
from openmed import normalize_label

note = (
    "Medicare 2123 45670 1, TFN 123 456 782, IHI 8003 6012 3456 7894, "
    "ABN 11 345 678 919, ACN 123 456 780."
)

result = openmed.extract_pii(note, lang="en", locale="en_AU")

for ent in result.entities:
    subtype = (ent.metadata or {}).get("subtype")
    print(f"{normalize_label(ent.label):16} {subtype!s:12} [{ent.start}:{ent.end}]")
```

```text
MEDICARE_NUMBER  AU_MEDICARE  [9:21]
TFN              AU_TFN       [27:38]
IHI              AU_IHI       [44:63]
ABN              AU_ABN       [69:83]
ACN              AU_ACN       [89:100]
```

- `metadata["subtype"]` is set only when the checksum passes. A value that fails
  its checksum but is labelled by nearby context (`"Medicare number ..."`) is
  still returned with its AU label at a reduced score and **no** subtype, so a
  typo or OCR error is still redacted. Unlabelled checksum failures are dropped.
- Checksums are weak evidence for short numbers: a bare nine- or ten-digit
  number that happens to pass a check is tagged even without context. A
  nine-digit value valid as both ACN and TFN is `TFN` unless ACN context is
  nearby.
- Every shipped policy profile treats the AU labels exactly like `SSN`; the
  `ccia_l1` / `ccia_l2` / `ccia_l3` profiles map all eight to `mask`.

### Clinician and veteran identifiers

Three more AU labels cover identifiers that appear in referrals, scripts and
report headers. Each is tagged only directly after one of its field labels
(`AHPRA`, `DVA No`, `Provider No`, `Prescriber`, ...); a label elsewhere in
the sentence (`AHPRA registration for Dr Lee is MED0001234567`) is not enough.
AHPRA and DVA numbers have no check digit, and a Medicare provider number must
also pass its mod-11 check character. These numbers are not propagated to
unlabelled copies.
Synthetic examples:

| Label | Subtype | Example value | Format |
| --- | --- | --- | --- |
| `AHPRA_NUMBER` | `AHPRA` | `MED0001234567` | three-letter profession prefix + 10 digits |
| `DVA_NUMBER` | `AU_DVA` | `QAB12345` | METeOR file number: state letter, war code, digits, optional dependant letter |
| `MEDICARE_PROVIDER_NUMBER` | `AU_PROVIDER` | `123456AF` | six-digit stem, practice location character, mod-11 check letter |

```text
Referred by Dr A Example, AHPRA MED0001234567, Provider No 123456AF.
Veteran, DVA file number QAB12345.
```

Under `ccia_l1`–`ccia_l3` all three are `mask`, like the other AU identifiers.

## Feed spans to a downstream redactor

`extract_pii` gives you offsets; you decide the action. A simple offset-based
redactor (replace highest-offset first so positions stay valid):

```python
import openmed
from openmed import normalize_label

note = "Patient John Doe, MRN 00481726, seen 2024-03-02."
result = openmed.extract_pii(note, confidence_threshold=0.6)

redacted = note
for ent in sorted(result.entities, key=lambda e: e.start, reverse=True):
    tag = normalize_label(ent.label)
    redacted = redacted[:ent.start] + f"[{tag}]" + redacted[ent.end:]

print(redacted)   # Patient [PERSON], MRN [ID_NUM], seen [DATE].
```

With `locale="en_AU"`, the same loop tags `Medicare 2123 45670 1` as
`Medicare [MEDICARE_NUMBER]`.

For production redaction, masking strategies, and policy profiles, hand the work
to `openmed.deidentify` instead of hand-rolling — it adds a safety sweep and
date-shifting (see `deidentifying-clinical-text`).

## Hand-off to / from OpenMed

- **To `deidentifying-clinical-text`:** once you have reviewed the spans, call
  `openmed.deidentify(note, method="mask", policy="hipaa_safe_harbor")` to produce
  safe output — it re-detects with a higher default threshold for safety.
- **To `reidentifying-text`:** if you need reversibility, use
  `openmed.deidentify(..., keep_mapping=True)` and store the mapping securely.
- **To Presidio / custom anonymizers:** `extract_pii` spans (`label`, `start`,
  `end`) translate cleanly into other recognizers' result formats; OpenMed also
  ships an `Anonymizer` (`openmed.Anonymizer`) for richer surrogate generation.
  The Presidio adapter maps `US_SSN` ↔ `SSN` and `AU_MEDICARE` / `AU_TFN` /
  `AU_ABN` / `AU_ACN` ↔ `MEDICARE_NUMBER` / `TFN` / `ABN` / `ACN`.

## Edge cases & gotchas

- **Attribute is `.confidence`, not `.score`.**
- **`ent.label` is not canonical.** Pattern and model labels keep their source
  spelling (`"ssn"`, `"medicare_number"`); call `normalize_label` before
  comparing against `CANONICAL_LABELS`.
- **Detection is not redaction.** `extract_pii` never changes text — if a caller
  expected redacted output, they want `deidentify`.
- **Threshold trade-off:** `0.5` favors recall (good for *finding* PHI to review).
  For *removing* PHI, prefer `deidentify`'s safety-biased `0.7` default.
- **Language and locale matter:** wrong `lang` silently lowers recall, and the AU
  identifier patterns need `locale="en_AU"`. Verify with
  `get_default_pii_model(lang)`.
- **No raw PHI in logs/audit.** Record offsets, labels, subtypes, and hashes —
  never the identifier text. Use synthetic data in examples and tests.
- **Local-first.** No cloud calls in PHI workflows; models run on-device after a
  one-time download.

## Standards & references

- HIPAA Safe Harbor 18 identifiers: 45 CFR §164.514(b)(2) —
  https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/
- OpenMed canonical labels: `openmed.CANONICAL_LABELS` / `openmed.normalize_label`.
- OpenMed PII models: https://huggingface.co/OpenMed
