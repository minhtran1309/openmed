# Lean Service

A small FastAPI service (`openmed.service.lean`) for detecting and redacting
identifiers in caller-supplied text segments. Every non-200 response has the
body `{"error_code": CODE}` only; request text, span text, patient keys and
secrets are never echoed or logged.

The service needs `OPENMED_AUDIT_KEY`, the HMAC key that signs every
`/v1/apply` audit. It refuses to start when the variable is unset or blank:
startup fails with `AUDIT_KEY_MISSING` and uvicorn exits non-zero. Use at least
32 random bytes (for example `openssl rand -hex 32`). The key is used
verbatim, byte for byte, including any whitespace or trailing newline, and
the verifier must use exactly the same bytes. Store the key file without a
trailing newline and read it the same way on both sides:

```bash
openssl rand -hex 32 | tr -d '\n' > openmed-audit-key   # no trailing newline
chmod 600 openmed-audit-key
```

```python
key = Path("openmed-audit-key").read_text(encoding="utf-8")  # same bytes
```

If a file may carry a newline, strip it consistently in both places (the
service sees what the environment holds; `"$(cat file)"` in a shell drops
trailing newlines, while `open(file).read()` keeps them).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe, `{"status": "ok"}` |
| `GET` | `/v1/provenance` | Version, fork tag, models, policy fingerprints |
| `GET` | `/v1/policies/{name}/coverage` | Coverage proof summary for one policy |
| `POST` | `/v1/detect` | Detect identifiers in segments under a policy |
| `POST` | `/v1/apply` | Render caller-recorded spans (`mark` + date shift) |
| `POST` | `/v1/selftest` | Run a fixed synthetic probe through detect and apply |
| `POST` | `/v1/verify/pdf` | Verify a black-box redacted PDF against its original |
| `GET` | `/v1/schemas/{name}` | JSON Schema of a request or response model |

Request and response shapes are published as JSON Schemas at
`/v1/schemas/{name}`: `detect_request`, `detect_response`, `apply_request`,
`apply_response`, `selftest_response`, `verify_pdf_regions`,
`verify_pdf_response`, `policy_coverage_response`, `provenance_response`,
`health_response` and `error_response`. Every model
rejects unknown fields.

## Errors

Error codes: `INVALID_REQUEST`, `INTERNAL_ERROR`, `NOT_FOUND`, `UNKNOWN_POLICY`,
`EMPTY_DOCUMENT`, `DUPLICATE_SEGMENT_ID`, `UNKNOWN_SEGMENT_ID`,
`SPAN_OUT_OF_RANGE`, `SPAN_INVERTED`, `UNKNOWN_LABEL`, `UNSUPPORTED_TREATMENT`,
`DATE_SHIFT_KEY_REQUIRED`, `DETECT_FAILED`, `APPLY_FAILED`,
`AUDIT_KEY_MISSING`, `PDF_UNREADABLE`, `PAGE_COUNT_MISMATCH`,
`REGION_OUT_OF_BOUNDS`, `VERIFY_FAILED`, `SURROGATE_KEY_REQUIRED`.

`NOT_FOUND` and `UNKNOWN_POLICY` are 404; `INTERNAL_ERROR`, `DETECT_FAILED`,
`APPLY_FAILED`, `AUDIT_KEY_MISSING` and `VERIFY_FAILED` are 500; every other
code is 400. The body is `{"error_code": CODE}` only, except
`REGION_OUT_OF_BOUNDS`, which adds the 1-based `page` of the offending region.
`AUDIT_KEY_MISSING` is returned by `/v1/apply` only when an app was started
without its startup check (for example in tests) and the key is missing.
Request validation failures (bad JSON, missing or extra fields, wrong types)
are `INVALID_REQUEST` with status 400. Any other framework 4xx, for example
405 for a wrong method, is also reported as `INVALID_REQUEST` but keeps its
original status code.

## Provenance and coverage

`/v1/provenance` returns `openmed_version`, `fork_tag` (env `OPENMED_FORK_TAG`),
`models` (env `OPENMED_LEAN_MODEL`, default the English PII model; nothing is
loaded at startup) and `policies` (name to `sha256:` profile fingerprint).

`/v1/policies/{name}/coverage` returns `covered_labels` (labels with a verified
detector and a sufficient action), `coverage_percent` and the same
`fingerprint` as `/v1/provenance`. An unknown name is `UNKNOWN_POLICY`.

## Detect

`POST /v1/detect` takes `segments` (`segment_id`, `text`; ids must be unique),
a `policy` name and `preserve_whitespace` (default `true`). Each segment runs
through the full privacy `Pipeline` with the safety sweep on. Each finding has
`segment_id`, `start`, `end` (offsets into the exact text sent), the canonical
`label`, an optional `subtype` (for example `AU_MEDICARE`), `score` and
`policy_class` (`DIRECT_IDENTIFIER`, `QUASI_IDENTIFIER`, ...). No span text is
returned. A labelled value whose checksum fails (a likely typo or OCR error,
such as `Medicare number` followed by an invalid number) is still reported
with its canonical label and no `subtype`.

The PII model is `OPENMED_LEAN_MODEL` when set, otherwise the openmed default.
It is loaded on the first detect or selftest request and cached in one
process-wide loader. Inference on that shared model is serialised per process
(the model pipelines and fast tokenizers are not thread-safe), so concurrent
requests queue. Scale throughput with `uvicorn --workers N`; each worker loads
its own copy of the model.

`/v1/detect` has no `locale` field. The AU recognisers run only under
policies whose profile sets an AU `implied_locale`, which means the `ccia_l1`,
`ccia_l2` and `ccia_l3` policies. Under any other policy, AU identifiers are
found only if the PII model itself tags them. The AU recognisers are:

- the checksum identifiers from mt.1: Medicare, TFN, IHI, ABN and ACN, plus AU
  phone, postcode and address;
- record numbers after a field label (`UR`, `URN`, `MRN`, `Hospital No`,
  `Patient ID`, `Episode No`, `Visit No`, `Accession`/`Accession No`/
  `Accession Nr`, `Lab No`, `Specimen No`, `Participant ID`, `Subject ID`),
  including values with a check letter or an embedded letter (`0012345A`,
  `1234567-K`, `23S012345`), and the record number on an EMR running title
  line (`CITIZEN, Jane - 12345678-K`);
- report header values after their labels (`Name`, `Patient Name`, `Surname`,
  `Given Names`, `DOB`, `Address`, `NOK`, `Referring Doctor`, `GP`, ...);
- day-first dates (`4.7.61`, `4th July 1961`);
- the NRP phrase (Aboriginal and/or Torres Strait Islander status);
- AHPRA, DVA and Medicare provider numbers, each only directly after its own
  field label (`AHPRA`, `DVA No`, `Provider No`, `Prescriber No`, ...);
- the bracketed user id in an EMR print footer
  (`Printed by CITIZEN, JANE [CITIZENJ]`).

The `subtype` values to map are:

| Subtype | Label | Source |
| --- | --- | --- |
| `AU_MEDICARE`, `AU_TFN`, `AU_IHI`, `AU_ABN`, `AU_ACN` | the matching AU label | checksum identifiers |
| `AU_PHONE`, `AU_PHONE_INSTITUTIONAL` | `PHONE` | AU phone and fax numbers |
| `AU_ADDRESS`, `AU_STATE`, `AU_POSTCODE` | `STREET_ADDRESS`, `LOCATION`, `ZIPCODE` | AU street and postal addresses |
| `AU_UR` | `ID_NUM` | labelled record numbers, title-line record numbers, record-number table columns |
| `AU_HEADER` | `PERSON`, `FIRST_NAME`, `LAST_NAME`, `DATE_OF_BIRTH`, `STREET_ADDRESS` | report header values |
| `AU_PRINT_FOOTER` | `USERNAME` | print footer user id |
| `NRP` | `ETHNICITY` | Aboriginal and/or Torres Strait Islander status |
| `AHPRA` | `AHPRA_NUMBER` | AHPRA registration number |
| `AU_DVA` | `DVA_NUMBER` | DVA file number |
| `AU_PROVIDER` | `MEDICARE_PROVIDER_NUMBER` | Medicare provider number |

### Labelled values repeated without their label

Under the AU policies, once a birth date is detected after its `DOB` label
(`AU_HEADER`) or a record number after its record label (`AU_UR`), every other
exact copy of the same text is that value too: a running header `CITIZEN, Jane
0012345 04/07/1961` gives `ID_NUM` (`AU_UR`) for `0012345` and `DATE_OF_BIRTH`
(`AU_HEADER`) for the date, where the date alone would be `DATE`. A copy is a
separate token: a letter or digit directly beside it, or joined by `-`, `/` or
`.`, makes it part of a longer value and it is not tagged; a space is a
separator. A record number needs at least six letters and digits, and one
labelled with a check letter (`1234567-K` or `1234567K`) also covers bare
copies of its digit stem (`1234567`). Only birth dates and record numbers
propagate; AHPRA, DVA and provider numbers do not. A value never labelled
anywhere in the request is not propagated.

Propagation spans the whole request: labelled values are collected from all
segments, and a value labelled in one segment also tags its copies in every
other segment (for example a `UR:` in the header block and its copy in a
page-2 running header or a table row sent as its own segment). Offsets stay
per segment. Send all segments of one document in one request to get this; a
segment that holds a copy of another segment's labelled value is run through
the pipeline a second time, so such requests cost more. A request whose
segments share no labelled value gives exactly the same response as before.
Values are compared in memory only and are never logged or returned.

### Table rows

A segment may be one table row. Send `table_header` (the column headers) and
`cells` (`[{start, end}, ...]`, the offsets of each cell in `text`, for
example in `"Jane CITIZEN | 04/07/1961 | 0012345"`). `cells[i]` belongs to
`table_header[i]`. Both fields are sent together, with equal lengths, and each
cell must satisfy `0 <= start <= end <= len(text)`; otherwise the request is
`INVALID_REQUEST`.

Each column is classified from its header alone, with the
`openmed.multimodal` column classifier extended with the AU header vocabulary
(`Name`, `D.O.B`, `UR`/`URN`, `MRN`, `Lab No`, `Medicare No`, `Surname`,
`Ph`/`Mobile`, `NOK`, ...). Cell values are never sampled. Every non-blank cell
in a direct- or quasi-identifier column gives one extra finding over the cell,
trimmed of surrounding whitespace, with `source: "table-column"`, the column's
canonical label (for example `PERSON`, `DATE_OF_BIRTH`, `ID_NUM`), `score`
1.0 and its `policy_class`. Only record-number columns carry a subtype
(`AU_UR`). These findings are in addition to the model and pattern findings
for the same text; they are not deduplicated, so merge overlaps on your side.
When a table-column `DATE_OF_BIRTH` and a pipeline `DATE` cover exactly the
same cell and both go to `/v1/apply`, the merged span currently keeps `DATE`:
the output tag is `[DATE_n]` and the audit `label` is `DATE`. Both are masked
and both map to the Safe Harbor date element, so nothing leaks; drop the
less specific finding before `/v1/apply` if you need the `DATE_OF_BIRTH` tag.
Unknown headers (such as `Exam`) add nothing, and a row whose cells all equal
their headers (the header row) adds nothing. Segments without `table_header`
give exactly the same response as before; only table-column findings carry
`source`.

## Apply

`POST /v1/apply` takes a `document` of segments, each with its `text` and the
caller-recorded `spans` (`span_id`, `start`, `end`, canonical `label`, optional
`subtype` and `replacement` (1 to 256 characters), and optional `source` and
`score` copied from the detect findings),
plus `treatment` and `policy`. It never detects. `source` is one of `model`,
`pattern`, `table-column` or `manual`, and `score` lies in `[0, 1]`; both only
feed the audit and never change the rendering. Labels must be
canonical (`MEDICARE_NUMBER`, not `medicare`); anything else is
`UNKNOWN_LABEL`.

The `mark` and `replace` treatments are implemented; `generalise` and `keep`
are accepted by the schema and return `UNSUPPORTED_TREATMENT`.

Under `mark`, masked spans become `[LABEL_n]` tags. Name parts keep a `PERSON`
prefix: `FIRST_NAME`, `MIDDLE_NAME` and `LAST_NAME` render as
`[PERSON_FN_n]`, `[PERSON_MN_n]` and `[PERSON_LN_n]`, each numbered per part by
value, so a shared surname keeps one number. A whole-name `PERSON` span stays
`[PERSON_n]`. A span's `replacement` is ignored, but it is still validated:
since mt.3 an empty `replacement` is `INVALID_REQUEST` under every treatment,
`mark` included (mt.2 accepted it). Spans whose policy action is
`shift` are shifted day-first dates; pass `offset_days`, or `patient_key` with
`secret`, or the request is `DATE_SHIFT_KEY_REQUIRED` when a shift is needed.
A date that cannot be parsed, or a non-date label with a `shift` action, is
masked. A `DATE_OF_BIRTH` is tagged, never shifted. The response carries the
rendered segments, per-span actions in output coordinates, merges of
overlapping spans, the policy fingerprint, a signed `audit` and a Safe Harbor
`attestation`.

### Replace

`replace` renders per-patient surrogates and the caller's `replacement` text.
It always needs `patient_key` with `secret`, even when no span needs a
surrogate; without them, or with `offset_days` instead, the request is
`SURROGATE_KEY_REQUIRED` (400). `offset_days` together with `patient_key` stays
`INVALID_REQUEST`.

Each cluster of overlapping spans resolves by the first matching row:

| # | Condition | `mark` | `replace` |
|---|---|---|---|
| 1 | single-span cluster with `replacement` | as rows 2-7 (ignored) | `replacement` |
| 2 | policy action `keep` | `keep` | `keep` |
| 4 | all labels ⊆ {`DATE`} and policy `shift` | `shift` | `shift` |
| 5 | all labels ⊆ {`DATE_OF_BIRTH`, `DATE`} with ≥1 DOB and policy `shift` | tag | `shift` |
| 6 | all labels are surrogate labels | tag | surrogate (`replace`) |
| 7 | anything else | tag | tag |

Row 3 (cancer generalisation) belongs to `generalise` and is not yet
implemented. A date that cannot be shifted (year-only, month-year,
unparseable) renders `[DATE_n]`, or `[DATE_OF_BIRTH_n]` for a DOB, with action
`mark` under every treatment.

- **Surrogate labels.** `PERSON`, `FIRST_NAME`, `MIDDLE_NAME`, `LAST_NAME`,
  `EMAIL`, `PHONE`, `STREET_ADDRESS`, `LOCATION`, `ZIPCODE`, `ORGANIZATION`,
  `URL`, `USERNAME`, `ID_NUM`, `ACCOUNT_NUMBER` and the 8 AU identifier labels
  (`MEDICARE_NUMBER`, `TFN`, `IHI`, `ABN`, `ACN`, `AHPRA_NUMBER`,
  `DVA_NUMBER`, `MEDICARE_PROVIDER_NUMBER`). Any other label, `SSN` and
  `ETHNICITY` included, is tagged.
- **Source.** Surrogates come from openmed's existing anonymizer with the
  `en_AU` Faker locale. AU identifiers are checksum-valid (Medicare, TFN, IHI,
  ABN, ACN, provider number) or grammar-valid (AHPRA, DVA) fakes in the
  canonical layout, never copied from the source layout. Phones come from the
  ACMA fictional ranges: a mobile becomes one of the 30 fictional mobiles, a
  landline keeps its area code. **A checksum-valid fake can coincide with a
  real person's number.** It is never the source's own number: a surrogate
  never equals its source, nor any other source value of the same call (so
  a real value is never moved onto another person), and a name surrogate
  (`PERSON`, `FIRST_NAME`, `MIDDLE_NAME`, `LAST_NAME`) never shares a
  case-insensitive word with any source name of the call (`Mary Jones` never
  becomes `Shelly Jones`). A `STREET_ADDRESS` or `LOCATION` surrogate never
  shares a significant word (street name, suburb, property name) with any
  source address or location of the call (`12 George Street` never becomes
  `8 George Circle`); numbers, unit and level markers, street types, states
  and postcodes do not count. Words are compared accent-insensitively
  (`José` matches `Jose`); initials compare exactly.
- **Dates** shift exactly as under `mark`, by
  `stable_offset_for(patient_key, 365, secret)`, and `DATE_OF_BIRTH` shifts
  too. Dates are never replaced with a random date.
- **Caller `replacement`.** On a single (unmerged) span the text is rendered
  verbatim, ahead of any built-in rendering (surrogate, policy `keep`, date
  shift), and reported as `replacement`. A merged cluster ignores every
  member's `replacement` and resolves by rows 2-7. A `replacement` that
  normalises to its own source (ignoring case and whitespace, or layout for
  phones and identifiers) removes nothing, so it renders the tag with action
  `mark` and is audited as `mask`. Beyond that, the content of a
  `replacement` is the caller's responsibility: openmed does not check it for
  other identifiers.
- **Reported action.** `spans[].action` is the rendering actually applied:
  `mark`, `shift`, `keep`, `replace` (an openmed surrogate) or `replacement`
  (the caller's text). A fallback reports `mark`, never the requested
  treatment.
- **Consistency.** The same value gets the same surrogate for the same
  `patient_key` and `secret` in every call and every process; a different
  `patient_key` gives a different surrogate. Matching ignores case and
  whitespace for names and other text labels, and layout for phones and
  identifiers (`+61 491 570 156` and `0491 570 156` are one phone; `NX123456`
  and `QX123456` stay two DVA numbers).
- **Collisions.** Two different values in one call never share a surrogate,
  across all labels. A candidate that collides, equals any source value of
  the call, or (for a name) shares a word with a source name is retried; when retries run out the span falls back to its tag with action
  `mark`. The 30-number fictional mobile pool is the realistic case: a call
  with more than 30 distinct mobiles tags the excess, and fewer distinct
  mobiles can still collide and fall back.

**No state.** The service keeps no state. Cross-document consistency is
derived from `secret` and `patient_key` on every call. Each call builds a
request-local in-memory vault, keyed by
`HMAC-SHA256(secret, "openmed.apply.surrogate.v1\0" + patient_key)` and
dropped when the call returns. It keys entries by an HMAC of the source, so it
never holds an original value in clear; nothing is written to disk, cached
across requests or held in module state. The raw `patient_key` is only ever an
HMAC input, and the caller's `secret` is used, not `OPENMED_AUDIT_KEY`, so
rotating the audit key does not change surrogates.

**Determinism limits.** Surrogates are stable per openmed release. A Faker
upgrade or a generator change can change them, so release notes flag any
Faker bump in `uv.lock`. A collision retry depends on the other values in the
same call (another surrogate or any source value of the call), so in that
rare case a value can get a different surrogate in another document, and the no-shared-surrogate guarantee holds within one call
only (two different mobiles of one patient may share a fictional mobile across
calls). `PERSON "Jane Citizen"` and `FIRST_NAME "Jane"` get independent
surrogates: name-part linkage is out of scope.

### Audit

`audit` is an openmed `AuditReport` for this render, signed with
`OPENMED_AUDIT_KEY` (`signature.key_id` is `openmed-lean`, `algorithm`
`HMAC-SHA256`). Store it beside the rendered output.

- **Join rule.** Segments are joined with a single `"\n"` in document order.
  `input_hash` is `hmac-sha256:` plus the lowercase HMAC-SHA256 hex, under the
  audit key, of the UTF-8 joined request texts. It is keyed because the audit
  is released beside the output: an unkeyed hash of the input would let anyone
  holding both brute-force masked names and the date-shift offset.
  `deidentified_text_hash` is `sha256:` plus the plain SHA-256 hex of the
  joined response `segments[].text`, so anyone can check the output against
  it. `document_length` is the joined output length in code points. Because
  a segment may itself contain `"\n"`,
  `resolved_profile.segments` records `segment_id`, `input_length` and
  `output_length` for every segment.
- **Spans.** One entry per rendered span (a merged overlap is one entry).
  `start`/`end` are code-point offsets into the joined output. `action` is
  `mask` (apply's `mark`), `shift`, `keep` or `replace` (apply's `replace`
  and `replacement`); `evidence` holds `span_id`,
  `segment_id`, `apply_action` and `merged_span_ids`. `sources` is the
  request `source` (or `caller`), `confidence` the request `score` (or `1.0`),
  and `threshold` is `0.0`. `text_hash` is `hmac-sha256:` plus the
  HMAC-SHA256 hex, under the audit key, of the source text covered by the span
  and its merged spans; it is keyed so short identifiers and source dates
  cannot be brute-forced. `surrogate` is `null` except for `replace` and
  `replacement` spans, where it is `hmac-sha256:` plus the HMAC-SHA256 hex,
  under the audit key, of the rendered value, never the value itself.
  `context` is `{}`.
- **Profile and provenance.** `policy` is the request policy;
  `resolved_profile` is the policy profile plus `treatment`,
  `policy_fingerprint` and `segments`. `detectors` lists one entry per span
  source, `safety_sweep` is `{"enabled": false}`, and `residual_risk` holds
  `keep_span_count`, `shift_span_count`, `replace_span_count`,
  `replacement_span_count` and `generalise_span_count` (the last three count
  apply actions from `evidence.apply_action`).
- **Never recorded.** Span text, surrogates and replacement texts in clear,
  context, the patient key, the secret, the offset days and which shift mode
  was used. A keyed and an
  explicit-offset render with the same offset give identical audits.
- **Caller ids.** `span_id` and `segment_id` are copied into `evidence` and
  `resolved_profile.segments`, so they must be opaque and must never carry
  PHI. The audit sanitiser may replace an id that looks like a long numeric
  identifier (for example a UUID whose last group is 12 digits) with
  `redacted:sha256:…`, while `body.spans` keeps it raw. Map audit spans to
  response spans by offsets (audit `start`/`end` against the joined output,
  or `body.spans[].out_start`/`out_end` plus the segment's base offset), not
  by echoing `span_id`.

### Canonical serialisation

A verifier needs only the key and a standard JSON library. Let `C(x)` be

```python
json.dumps(x, sort_keys=True, separators=(",", ":"),
           ensure_ascii=True, allow_nan=False).encode("utf-8")
```

Then, over the `audit` object exactly as received:

- `signature.value` is the lowercase hex of
  `HMAC-SHA256(key, C(audit without "signature"))`;
- `repro_hash` is `"sha256:"` plus the lowercase SHA-256 hex of
  `C(audit without "signature" and "repro_hash")`;
- `input_hash` is `"hmac-sha256:"` plus the lowercase hex of
  `HMAC-SHA256(key, "\n".join(request segment texts).encode("utf-8"))`, so
  recompute it with the key, not with plain SHA-256;
- `deidentified_text_hash` is `"sha256:"` plus the lowercase SHA-256 hex of
  `"\n".join(response segment texts).encode("utf-8")`.

`signature.key_id` and `signature.algorithm` are not covered by the HMAC. A
verifier must itself check that `signature.algorithm == "HMAC-SHA256"` (and,
if it keeps several keys, pick the key by `key_id`) before trusting the value.

Floats use Python's `json` form (`1.0`, not `1`), and that form is part of the
contract. A verifier in another language must parse the numbers back into
the same textual form. In Python, `AuditReport.from_dict(audit).verify(key,
deidentified_text="\n".join(texts))` checks the signature and the output hash
together. Do not pass `original_text=`: that compares a plain SHA-256 with the
keyed `input_hash` and always fails.

Verify with openmed 3.2.0-mt.2 or later, or with the stdlib routine above.
mt.2 stopped the audit sanitiser from redacting digest values (`sha256:…`,
`hmac-sha256:…`) that happen to contain a long run of digits. Earlier openmed
releases still redact them when re-reading a report, so they reject new audits
holding such a digest, which includes every `ccia_l1` audit (its policy
fingerprint has one). Reports signed by earlier releases still verify under
mt.2.

### Attestation

`attestation` comes from
`generate_safe_harbor_attestation(audit, any_policy=True)`. It lists all 18
HIPAA Safe Harbor categories in regulatory order, each with
`detection_count`, `applied_action_counts`, the request policy's actions for
the mapped labels and `residual_risk`. Spans whose label has no Safe Harbor
category (clinical labels such as `DISEASE`) are in the audit but not counted.
Any `keep` or `shift` action makes its category a residual-risk item: date
shifting is not Safe Harbor removal. `source_report_hash` equals the audit's
`repro_hash`, so the attestation cannot be paired with another render. The
attestation is evidence from one run, not a certification.

## Selftest

`POST /v1/selftest` (empty body) runs a fixed synthetic probe through detect
(`ccia_l3`) then apply (`mark`, `ccia_l1`, fixed offset) and returns
`checks`, a map of `medicare`, `abn`, `phone`, `date_shift`, `person_tag` and
`year_tag` to `pass` or `fail`. The selftest builds no audit, but the service
it runs in still needs `OPENMED_AUDIT_KEY` to start.
`person_tag` passes when the probe name renders
as `[PERSON_1]` or as `[PERSON_FN_1] [PERSON_LN_1]`. The probe text must stay identical to the
consumer's copy.

## Verify PDF

`POST /v1/verify/pdf` checks that a black-box redaction really removed the
text it covers. It needs the `lean-pdf` extra (`pip install
"openmed[service,lean-pdf]"`), which brings pdfplumber (MIT), pdfminer.six
(MIT), pypdfium2 (Apache-2.0/BSD-3, the raster backend) and Pillow (MIT-CMU).
The Docker image installs it. PyMuPDF (AGPL) is never a dependency.

The body is `multipart/form-data` with three parts:

| Part | Content |
|---|---|
| `original` | The source PDF |
| `redacted` | The redacted PDF |
| `regions` | JSON `[{"page": 1, "bbox": [x0, top, x1, bottom]}, ...]` |

`page` is 1-based. `bbox` is in PDF points with a top-left origin
(pdfplumber's raw page coordinates), with `x0 < x1` and `top < bottom`. Each
region may overhang its page by at most 0.5 pt. A request carries 1 to 256
regions; more is `INVALID_REQUEST`. The body is capped at 64 MiB and is read
and parsed in memory, off the event loop: uploads are never written to disk.

Pages whose MediaBox or CropBox origin is not `(0, 0)` are not normalised:
bounds and pixel crops use the raw page coordinates, so such pages may give
imprecise bounds checks and verify as `no_box` or `unverifiable`. This fails
closed; it never yields a false `passed`.

For every region the service checks that no selected source word remains
extractable anywhere in the redacted PDF (moved, split and duplicated copies
count), that no selectable word lies under the region, and that an opaque box
covers it. With the
raster backend installed, the region is also rendered in both PDFs and must
differ. Each page of each PDF is rendered at most once per request, at the
lower of 150 dpi and the resolution that keeps the render within 25
megapixels. When that resolution would fall below 36 dpi (a very large page),
the page is not rendered and its regions are `unverifiable` unless they leak
text. Identical regions (same page and bbox) are checked once and counted once
in `text_removal`, but each is still listed in `regions`.

```json
{
  "passed": true,
  "regions": [
    {"page": 1, "bbox": [112.7, 62.5, 164.1, 74.5], "status": "passed",
     "residual_word_count": 0}
  ],
  "text_removal": {"selected": 2, "residual": 0},
  "report_sha256": "<64 hex digits>"
}
```

`status` is decided in this order:

1. `unverifiable`: the region has no extractable source words, for example on
   a scanned page or over an image. **Native pages only**: such a region is
   never `passed`; apply your own scanned-page rule to it.
2. `text_leak`: a selected word is still extractable, under the region or
   anywhere else.
3. `unverifiable` again when the page is too large to render within the pixel
   budget (see above).
4. `no_box`: no opaque box covers the region (or, with the raster backend, its
   pixels did not change).
5. `passed`.

`residual_word_count` is the larger of the under-region and whole-document
counts. `text_removal` sums the selected source words and those still
extractable. The top-level `passed` is true only when every region passed, so
one `unverifiable` region makes it false. `report_sha256` is the SHA-256 hex
digest of the canonical JSON (as in [Canonical
serialisation](#canonical-serialisation)) of the body without
`report_sha256`. The response carries counts, boxes and that hash only, and
nothing from the PDFs is logged.

pdfminer logs page text at DEBUG. The service silences the `pdfminer`,
`pdfplumber`, `pypdfium2` and `PIL` loggers, and their existing child loggers,
when the app is created. Operators must not re-enable these loggers (for
example through a logging `dictConfig`): they would write page text, and so
PHI, to the logs.

Errors are checked in this order:

1. `INVALID_REQUEST`: a missing, repeated or unknown part, a body over the
   cap, `regions` that is not JSON, empty, longer than 256, has a page below 1
   or an inverted bbox.
2. `PDF_UNREADABLE`: either PDF cannot be opened or has no pages.
3. `PAGE_COUNT_MISMATCH`: the two PDFs have different page counts.
4. `REGION_OUT_OF_BOUNDS`: a region's page is beyond the page count, or its
   bbox lies outside the page by more than 0.5 pt. The body carries `page`.
5. `VERIFY_FAILED` (500): any other failure. Only the exception type is
   logged.

## Known gaps in mt.1

- `person_tag` and `year_tag` depend on the PII model. `date_shift` does too:
  if the model labels the full date as a date of birth, apply tags it instead
  of shifting it and the check fails.
- `shift` is rendered only by `/v1/apply`. `Pipeline.redacted_text` and
  `deidentify` mask a `shift` span instead, so a date never passes through.
- The header-field detector does not yet read the AU locale patterns.

## Releasing (maintainer)

```bash
scripts/release_mt.sh v3.2.0-mt.1   # wheel in dist/, image openmed-lean:v3.2.0-mt.1
```

The script refuses to build unless the working tree is clean and `HEAD` carries
exactly the given tag, and it labels the image with
`org.opencontainers.image.revision`. For a release candidate,
`scripts/release_mt.sh --allow-untagged v3.2.0-mt.1-rc1` skips the tag check
(the tree must still be clean). The script creates no git tags and publishes
nothing.

## Offline install (consumer)

No github.com access is needed. Copy the artifacts over any local channel.

Wheel folder:

```bash
pip install --no-index --find-links /path/to/wheelhouse "openmed[service,lean-pdf,hf]"
```

Image via `docker save` / `docker load`:

```bash
docker save openmed-lean:v3.2.0-mt.2 -o openmed-lean.tar     # build host
docker load -i openmed-lean.tar                              # target host
docker run --rm -p 8080:8080 \
  -e OPENMED_AUDIT_KEY="$(cat /path/to/openmed-audit-key)" \
  -e HF_HUB_OFFLINE=1 \
  -v /path/to/huggingface-cache:/root/.cache/huggingface:ro \
  openmed-lean:v3.2.0-mt.2
curl localhost:8080/health
curl -X POST localhost:8080/v1/selftest     # six "pass" results
```

The image carries the model runtime (the `hf` extra, with CPU-only torch) but
no model weights. The PII model (`OPENMED_LEAN_MODEL`, default the English PII
model) loads on the first `/v1/detect` or `/v1/selftest` request from the
Hugging Face cache. On a PHI host, pre-populate a cache directory on a machine
with network access (for example, run one selftest there, or
`hf download <model>`), copy it over with the image, mount it read-only, and
set `HF_HUB_OFFLINE=1` so the container never reaches the network. Without a
mounted cache, the first request downloads the model and so needs network
access. `/health` passes before the model loads, so use `/v1/selftest` to
confirm detection works.

The image will not start without `-e OPENMED_AUDIT_KEY`: the container exits
with `AUDIT_KEY_MISSING`, so the health check never passes. The consumer needs
the same key to verify each audit; keep it as a third root-only secret.

A local registry works the same way (`docker tag` then `docker push`).
