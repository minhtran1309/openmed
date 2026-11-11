# openmed_au

**Australian PII de-identification for clinical text, built on [OpenMed](https://github.com/maziyarpanahi/openmed).**

`openmed_au` is a private fork of OpenMed 3.2.0. It adds Australian identifier
detection, the CCIA privacy profiles (`ccia_l1`–`ccia_l3`) and a small REST
service for detecting and redacting identifiers. The service can mark, replace,
generalise or keep each identifier, and it signs an audit of every render.
Everything runs locally after the model is downloaded. Request text is never
logged or echoed.

| | |
|---|---|
| **Base** | upstream OpenMed `3.2.0` (`29ffa7005`) |
| **Release tags** | `v3.2.0-mt.N` |
| **Working branch** | `feat/au-pii-support` |
| **Service docs** | [docs/service/lean-service.md](docs/service/lean-service.md) |

Everything in upstream OpenMed (clinical NER, multilingual PII, MLX/ONNX
backends, FHIR export) still works unchanged. See the
[upstream README](https://github.com/maziyarpanahi/openmed#readme) and the
[OpenMed docs](https://openmed.life/docs). This README covers only what the fork
adds.

---

## What the fork adds

### Australian identifiers

The AU recognisers run when a policy's profile implies the `en_AU` locale. All
three `ccia_l*` profiles do.

| Identifier | Canonical label | Subtype | How it is matched |
|---|---|---|---|
| Medicare number (with IRN) | `MEDICARE_NUMBER` | `AU_MEDICARE` | checksum |
| Tax File Number | `TFN` | `AU_TFN` | checksum |
| Individual Healthcare Identifier | `IHI` | `AU_IHI` | Luhn checksum |
| ABN / ACN | `ABN` / `ACN` | `AU_ABN` / `AU_ACN` | checksum |
| AHPRA registration | `AHPRA_NUMBER` | `AHPRA` | after a field label |
| DVA file number | `DVA_NUMBER` | `AU_DVA` | after a field label, strict form |
| Medicare provider number | `MEDICARE_PROVIDER_NUMBER` | `AU_PROVIDER` | after a field label, plus check character |
| UR / MRN / episode / accession numbers | `ID_NUM` | `AU_UR` | after a field label, or an EMR title line |
| Phone and fax | `PHONE` | `AU_PHONE`, `AU_PHONE_INSTITUTIONAL` | AU number plan |
| Street address, state, postcode | `STREET_ADDRESS`, `LOCATION`, `ZIPCODE` | `AU_ADDRESS`, `AU_STATE`, `AU_POSTCODE` | pattern and context |
| Report header values (Name, DOB, Address, NOK, GP…) | `PERSON`, `FIRST_NAME`, `LAST_NAME`, `DATE_OF_BIRTH`, `STREET_ADDRESS` | `AU_HEADER` | after a field label |
| Print footer user id | `USERNAME` | `AU_PRINT_FOOTER` | `Printed by … [USERID]` |
| Aboriginal and/or Torres Strait Islander status | `ETHNICITY` | `NRP` | deny list |

The recognisers also:

- **Read day-first dates:** `4.7.61`, `04-Jul-23`, `4th of July 2023`.
- **Redact labelled values that fail their checksum,** such as a typo or an OCR
  error. They are reported with their canonical label and no subtype.
- **Catch repeated record numbers and DOBs without their label,** including
  repeats in another segment of the same request.
- **Classify table columns:** send `table_header` and `cells` with a segment,
  and cells in identifier columns come back with `source: "table-column"`.

### CCIA privacy profiles

`ccia_l1`, `ccia_l2` and `ccia_l3` are cumulative profiles in
`openmed/core/policies/`.

- **Kept:** oncology and clinical concepts, age and gender.
- **Masked:** direct identifiers, at least.
- **Shifted:** dates, by a per-patient offset, through the `shift` action.
- **Level differences:**
  - `ccia_l1` keeps the NRP phrase. `ccia_l2` and `ccia_l3` mask it.
  - `ccia_l3` also masks `TIME`.

Each profile has a fingerprint, which changes whenever the profile changes.
`/v1/provenance` reports it.

### Lean REST service (`openmed.service.lean`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/v1/provenance` | Version, fork tag, models, policy fingerprints |
| `GET` | `/v1/policies/{name}/coverage` | Coverage summary for one policy |
| `POST` | `/v1/detect` | Find identifiers in text segments |
| `POST` | `/v1/apply` | Render caller-recorded spans with a treatment |
| `POST` | `/v1/selftest` | Run a fixed synthetic probe through detect and apply |
| `POST` | `/v1/verify/pdf` | Check a black-box redacted PDF against its original |
| `GET` | `/v1/schemas/{name}` | JSON Schema of a request or response |

Every error returns only `{"error_code": CODE}`. Tracebacks, request text and
secrets are kept out of the response and the logs.

### Treatments in `/v1/apply`

| Treatment | Identifiers become | Dates |
|---|---|---|
| `mark` | numbered tags such as `[PERSON_1]` or `[MEDICARE_NUMBER_2]`, with name parts as `[PERSON_FN_n]`, `[PERSON_MN_n]` and `[PERSON_LN_n]` | shifted; `DATE_OF_BIRTH` is tagged |
| `replace` | per-patient surrogates that look Australian, such as checksum-valid fake IDs, ACMA fictional phones and `en_AU` names | shifted, DOB included |
| `generalise` | coarser values: `04xx xxx xxx`, `Carlton VIC`, `hospital`, the year of the shifted DOB, and cancer one OncoTree level up | shifted |
| `keep` | tags, except policy-`keep` spans and quasi-identifiers the caller chooses to keep | shifted, DOB included |

How the treatments behave:

- **Surrogates are consistent without stored state.** The same `patient_key`
  and `secret` always give the same surrogate. A surrogate never equals any
  source value of the call. A name surrogate never shares a word with a source
  name.
- **Cancer generalisation** uses a bundled OncoTree snapshot
  (`oncotree_2025_10_03`, CC-BY-4.0, see [NOTICE](NOTICE)). For example,
  `Lung Adenocarcinoma` becomes `Non-Small Cell Lung Cancer`. A term it can't
  map becomes `[CANCER_n]`, never kept.
- **Fallbacks fail closed.** If a value can't be replaced or generalised safely,
  it is tagged and its action is reported as `mark`.
- **Every render is audited.** Each response carries an HMAC-signed `audit`,
  which stores offsets, hashes and provenance but never plaintext. It also
  carries a Safe Harbor `attestation`.

### Library additions

- **`analyze_text(..., protect_deid_tags=True)`:** runs clinical NER over
  already de-identified text. `[LABEL_n]` tags are blanked before inference, so
  the model never sees them and the offsets stay the same.
- **AU surrogate providers** in the anonymizer
  (`openmed/core/anonymizer/providers/au_surrogates.py`).
- **Generalisers and the OncoTree lookup:** `openmed/core/apply_generalise.py`
  and `openmed/core/oncotree.py`.
- **Span rendering:** `openmed/core/apply_spans.py` and
  `openmed/core/apply_surrogates.py` render spans outside the service too.

---

## Quick start

### Install

```bash
uv pip install -e ".[service,lean-pdf,hf]"     # service, PDF verification, model runtime
uv pip install -e ".[dev,service,lean-pdf,hf]" # plus test and lint tools
```

### Run the service

```bash
openssl rand -hex 32 | tr -d '\n' > openmed-audit-key   # no trailing newline
chmod 600 openmed-audit-key

OPENMED_AUDIT_KEY="$(cat openmed-audit-key)" \
  python -m uvicorn openmed.service.lean.app:app --port 8080

curl -X POST localhost:8080/v1/selftest     # expect six "pass" checks
```

The service won't start without `OPENMED_AUDIT_KEY` (`AUDIT_KEY_MISSING`). Keep
the key secret: the consumer needs the same bytes to verify audits.

| Variable | Meaning |
|---|---|
| `OPENMED_AUDIT_KEY` | **Required.** HMAC key that signs every `/v1/apply` audit |
| `OPENMED_LEAN_MODEL` | PII model to load. Defaults to the OpenMed English PII model |
| `OPENMED_FORK_TAG` | Fork tag reported by `/v1/provenance` |
| `HF_HUB_OFFLINE=1` | Load the model only from the local Hugging Face cache |

### Detect, then apply

The examples use synthetic data only.

```bash
curl -s localhost:8080/v1/detect -H 'content-type: application/json' -d '{
  "policy": "ccia_l1",
  "segments": [
    {"segment_id": "s1", "text": "Patient Jane Citizen, mobile 0491 570 156."}
  ]
}'
```

Each finding gives `segment_id`, `start`, `end`, a canonical `label`, a
`subtype`, a `score` and a `policy_class`. No span text is returned. The caller
records the spans and sends them back with a treatment:

```bash
curl -s localhost:8080/v1/apply -H 'content-type: application/json' -d '{
  "policy": "ccia_l1",
  "treatment": "mark",
  "offset_days": 42,
  "document": [{
    "segment_id": "s1",
    "text": "Patient Jane Citizen, mobile 0491 570 156.",
    "spans": [
      {"span_id": "p1", "start": 8,  "end": 20, "label": "PERSON"},
      {"span_id": "t1", "start": 29, "end": 41, "label": "PHONE"}
    ]
  }]
}'
# segments[0].text → "Patient [PERSON_1], mobile [PHONE_1]."
```

`replace` always needs `patient_key` and `secret`, never `offset_days`.
`generalise` and `keep` need either a key or `offset_days`, but only when a date
has to shift.

### Docker

```bash
docker build -f docker/lean-service.Dockerfile -t openmed-lean:dev .
docker run --rm -p 8080:8080 \
  -e OPENMED_AUDIT_KEY="$(cat openmed-audit-key)" \
  -e HF_HUB_OFFLINE=1 \
  -v /path/to/huggingface-cache:/root/.cache/huggingface:ro \
  openmed-lean:dev
```

The image contains the CPU model runtime but no model weights. On a PHI host,
fill a Hugging Face cache on a machine with network access first, then mount
it read-only. For offline installs from a wheelhouse or `docker save`, see
[docs/service/lean-service.md](docs/service/lean-service.md#offline-install-consumer).

---

## Releases

| Tag | Contents |
|---|---|
| `v3.2.0-mt.1` | Lean service, `ccia_l1`–`l3`, AU checksum IDs, phone/address, `mark` + date shift, selftest. Wheel only: its Docker image has no model runtime. |
| `v3.2.0-mt.2` | AHPRA / DVA / provider numbers, UR/MRN and header fields, day-first dates, table columns, signed audit and attestation, `/v1/verify/pdf`, an image that includes the model runtime, and offset and leak fixes. |
| mt.3 (on `feat/au-pii-support`, untagged) | `replace`, `generalise` and `keep` treatments, AU surrogates, OncoTree cancer generalisation, `protect_deid_tags`. |

To build a release, run this from a clean checkout of the tagged commit:

```bash
scripts/release_mt.sh v3.2.0-mt.N        # wheel in dist/, image openmed-lean:v3.2.0-mt.N
```

The script creates no tags and publishes nothing. GitHub release workflows
(`publish.yml` and the others) are upstream's and don't apply to this
repository.

### Known limitations

- **A record number inside a URL path can leak.** Core span arbitration keeps
  the shorter URL match.
- **Some forms aren't detected yet:**
  - DVA numbers with dropped zeros, and the `NSM123456` form;
  - unlabelled names in running headers;
  - hospital and organisation names;
  - rare diagnoses and combinations of quasi-identifiers.
- **Name and bare-year detection depend on the PII model.** The pattern layer
  alone misses a bare `2019`.
- **Some text is over-redacted on purpose,** so errors fail closed. Examples:
  title-case clinical text read as an address, `NSW 2019` read as a postcode,
  and a date equal to the DOB treated as the DOB.
- **Surrogates are stable within one release only.** A Faker upgrade can
  change them.

For the full list, see
[docs/service/lean-service.md](docs/service/lean-service.md).

---

## Working with upstream

```bash
git remote -v
# private   git@ssh.bitbucket.org:cciacb/openmed_au.git   (this repository)
# upstream  https://github.com/maziyarpanahi/openmed.git  (fetch only)

git fetch upstream
git merge upstream/master        # on a branch, then resolve and run the tests
```

To test and lint:

```bash
.venv/bin/python -m pytest tests/ -q -m "not slow and not fuzz"
make format && make lint && make format-check
```

All test fixtures and demo documents are synthetic. Never commit real patient
data, DUA-gated datasets or plan and ticket notes.

---

## Inherited from upstream OpenMed

The fork keeps everything upstream OpenMed 3.2.0 provides. These are its counts
and install lines. Upstream's catalog and release tooling still reads and
updates this section, so keep its wording.

**2,266 manifest entries** · **35 model-backed PII languages** · Apache-2.0 SDK

### Multilingual PII (42 supported routes; 35 model-backed)

Upstream extraction and de-identification support **42 supported PII language
codes**: `am`, `ar`, `as`, `bn`, `cs`, `da`, `de`, `el`, `en`, `es`, `fa`,
`fr`, `gu`, `he`, `hi`, `id`, `it`, `ja`, `kn`, `ko`, `ml`, `mr`, `nl`, `no`,
`or`, `pa`, `pl`, `pt`, `ro`, `ru`, `sv`, `sw`, `ta`, `te`, `th`, `tr`, `uk`,
`ur`, `vi`, `xh`, `zh`, and `zu`. The Australian recognisers in this fork sit on
top of the `en` route. See the [per-language guide](docs/languages.md).

### Apple, Android and ONNX

```swift
dependencies: [
    .package(url: "https://github.com/maziyarpanahi/openmed.git", from: "3.2.0"),
]
```

```kotlin
dependencies {
    implementation("com.github.maziyarpanahi:openmed:v3.2.0")
}
```

```python
from openmed import OnnxModel

model = OnnxModel.from_pretrained(
    "OpenMed/OpenMed-PII-ClinicalE5-Small-33M-v1-onnx-android"
)
```

```bash
python -m openmed.onnx.convert \
  --model dslim/bert-base-NER \
  --output dist/example-onnx \
  --include-transformersjs
```

These snippets install upstream OpenMed, which doesn't include the AU features.
For details, see the [upstream README](https://github.com/maziyarpanahi/openmed#readme).

---

## License and credits

The OpenMed SDK is released under the [Apache-2.0 License](LICENSE) by Maziyar
Panahi and contributors. Third-party notices, including the OncoTree snapshot
(CC-BY-4.0, cBioPortal / Memorial Sloan Kettering), are in [NOTICE](NOTICE).

If you use OpenMed in research, cite the upstream paper:

```bibtex
@misc{panahi2025openmedneropensourcedomainadapted,
      title={OpenMed NER: Open-Source, Domain-Adapted State-of-the-Art Transformers for Biomedical NER Across 12 Public Datasets},
      author={Maziyar Panahi},
      year={2025},
      eprint={2508.01630},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2508.01630},
}
```
