# Lean Service

A small FastAPI service (`openmed.service.lean`) for detecting and redacting
identifiers in caller-supplied text segments. Every non-200 response has the
body `{"error_code": CODE}` only; request text, span text, patient keys and
secrets are never echoed or logged.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe, `{"status": "ok"}` |
| `GET` | `/v1/provenance` | Version, fork tag, models, policy fingerprints |
| `GET` | `/v1/policies/{name}/coverage` | Coverage proof summary for one policy |
| `POST` | `/v1/detect` | Detect identifiers in segments under a policy |
| `POST` | `/v1/apply` | Render caller-recorded spans (`mark` + date shift) |
| `POST` | `/v1/selftest` | Run a fixed synthetic probe through detect and apply |
| `GET` | `/v1/schemas/{name}` | JSON Schema of a request or response model |

Request and response shapes are published as JSON Schemas at
`/v1/schemas/{name}`: `detect_request`, `detect_response`, `apply_request`,
`apply_response`, `selftest_response`, `policy_coverage_response`,
`provenance_response`, `health_response` and `error_response`. Every model
rejects unknown fields.

## Errors

Error codes: `INVALID_REQUEST`, `INTERNAL_ERROR`, `NOT_FOUND`, `UNKNOWN_POLICY`,
`EMPTY_DOCUMENT`, `DUPLICATE_SEGMENT_ID`, `UNKNOWN_SEGMENT_ID`,
`SPAN_OUT_OF_RANGE`, `SPAN_INVERTED`, `UNKNOWN_LABEL`, `UNSUPPORTED_TREATMENT`,
`DATE_SHIFT_KEY_REQUIRED`, `DETECT_FAILED`, `APPLY_FAILED`.

`NOT_FOUND` and `UNKNOWN_POLICY` are 404; `INTERNAL_ERROR`, `DETECT_FAILED` and
`APPLY_FAILED` are 500; every other code is 400. Request validation failures
(bad JSON, missing or extra fields, wrong types) are `INVALID_REQUEST` with
status 400. Any other framework 4xx, for example 405 for a wrong method, is
also reported as `INVALID_REQUEST` but keeps its original status code.

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

`/v1/detect` has no `locale` field. The AU recognisers (Medicare, TFN, IHI,
ABN, ACN, AU phone, postcode and address) run only under policies whose
profile sets an AU `implied_locale`, which in mt.1 means the `ccia_l1`,
`ccia_l2` and `ccia_l3` policies. Under any other policy, AU identifiers are
found only if the PII model itself tags them.

## Apply

`POST /v1/apply` takes a `document` of segments, each with its `text` and the
caller-recorded `spans` (`span_id`, `start`, `end`, canonical `label`, optional
`subtype`), plus `treatment` and `policy`. It never detects. Labels must be
canonical (`MEDICARE_NUMBER`, not `medicare`); anything else is
`UNKNOWN_LABEL`.

In mt.1 only the `mark` treatment is implemented: masked spans become
`[LABEL_n]` tags. Name parts keep a `PERSON` prefix: `FIRST_NAME`,
`MIDDLE_NAME` and `LAST_NAME` render as `[PERSON_FN_n]`, `[PERSON_MN_n]` and
`[PERSON_LN_n]`, each numbered per part by value, so a shared surname keeps one
number. A whole-name `PERSON` span stays `[PERSON_n]`. `replace`, `generalise` and `keep` are accepted by the schema
and return `UNSUPPORTED_TREATMENT`. Spans whose policy action is `shift` are
shifted day-first dates; pass `offset_days`, or `patient_key` with `secret`, or
the request is `DATE_SHIFT_KEY_REQUIRED` when a shift is needed. A date that
cannot be parsed, or a non-date label with a `shift` action, is masked. The
response carries the rendered segments, per-span actions in output
coordinates, merges of overlapping spans and the policy fingerprint.

## Selftest

`POST /v1/selftest` (empty body) runs a fixed synthetic probe through detect
(`ccia_l3`) then apply (`mark`, `ccia_l1`, fixed offset) and returns
`checks`, a map of `medicare`, `abn`, `phone`, `date_shift`, `person_tag` and
`year_tag` to `pass` or `fail`. `person_tag` passes when the probe name renders
as `[PERSON_1]` or as `[PERSON_FN_1] [PERSON_LN_1]`. The probe text must stay identical to the
consumer's copy.

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
pip install --no-index --find-links /path/to/wheelhouse "openmed[service]"
```

Image via `docker save` / `docker load`:

```bash
docker save openmed-lean:v3.2.0-mt.1 -o openmed-lean.tar     # build host
docker load -i openmed-lean.tar                              # target host
docker run --rm -p 8080:8080 openmed-lean:v3.2.0-mt.1
curl localhost:8080/health
```

A local registry works the same way (`docker tag` then `docker push`).
