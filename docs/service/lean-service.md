# Lean Service

A small FastAPI service (`openmed.service.lean`) with `GET /health`,
`GET /v1/provenance` and `GET /v1/schemas/{name}`. Every non-200 response has
the body `{"error_code": CODE}` only; request text is never echoed or logged.

Error codes: `INVALID_REQUEST`, `INTERNAL_ERROR`, `NOT_FOUND`, `UNKNOWN_POLICY`,
`EMPTY_DOCUMENT`, `DUPLICATE_SEGMENT_ID`, `UNKNOWN_SEGMENT_ID`,
`SPAN_OUT_OF_RANGE`, `SPAN_INVERTED`, `UNKNOWN_LABEL`, `UNSUPPORTED_TREATMENT`,
`DATE_SHIFT_KEY_REQUIRED`, `DETECT_FAILED`, `APPLY_FAILED`.

`/v1/provenance` returns `openmed_version`, `fork_tag` (env `OPENMED_FORK_TAG`),
`models` (env `OPENMED_LEAN_MODEL`, default the English PII model; nothing is
loaded at startup) and `policies` (name to `sha256:` profile fingerprint).

## Releasing (maintainer)

```bash
scripts/release_mt.sh v3.2.0-mt.1   # wheel in dist/, image openmed-lean:v3.2.0-mt.1
```

The script creates no git tags and publishes nothing.

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
