# Repository Guidelines

## Project Structure & Module Organization

Core Python package code lives in `openmed/`. Keep existing privacy and registry code in `openmed/core/`; add genuinely new capability in top-level packages already present or planned: `clinical/`, `eval/`, `multimodal/`, `structured/`, `risk/`, and `interop/`. Backend adapters live in `mlx/`, `coreml/`, and `torch/`; the REST surface is in `service/`; Swift code and demos are in `swift/`. Tests are under `tests/unit`, `tests/integration`, and `tests/fixtures`; docs are in `docs/`; examples are in `examples/`; release and maintenance scripts are in `scripts/`.

## Build, Test, and Development Commands

- `uv pip install -e ".[dev]"`: install editable package with test and lint dependencies.
- `uv pip install -e ".[dev,hf]"`: add Hugging Face dependencies for model-related work.
- `.venv/bin/python -m pytest tests/ -q`: run the full test suite before preparing a PR.
- `pytest --cov=openmed --cov-report=term-missing`: run local coverage.
- `make build`: build wheel and sdist via `python3 -m build`.
- `make docs-serve` / `make docs-build`: preview or strictly build MkDocs.
- `cd swift/OpenMedKit && swift test`: run Swift package tests.

## Linting and Formatting

Use the checked-in tooling as the single source of truth. Do not apply ad hoc editor formatting or alternate lint configurations.

Python linting and formatting are owned by Ruff through `pyproject.toml`, `.pre-commit-config.yaml`, and the CI lint job. Before opening a Python-touching PR, run `make format`, `make lint`, and `make format-check`. Use `pre-commit run --all-files` for broad validation, or `pre-commit run --files <paths>` when validating a tightly scoped change.

Swift package formatting is separate from Python. OpenMedKit Swift code uses Apple `swift-format` through `.swift-format`, `scripts/format_swift.sh`, `scripts/lint_swift.sh`, and the Swift package CI workflow. Before opening a PR that touches `Package.swift` or `swift/OpenMedKit`, run `make format-swift` and `make lint-swift`.

Keep `.editorconfig`, Ruff, pre-commit, Swift format scripts, Make targets, documentation, and CI in sync when changing lint policy. A lint policy change should explain the local command, the CI gate, and the expected developer workflow.

## Coding Style & Architecture Rules

Use Python 3.11+, 4-space indentation, and 88-character lines. Prefer `snake_case` for functions and modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Public APIs need Google-style docstrings.

Preserve local-first defaults: no mandatory network calls after model download, no telemetry by default, no raw PHI in logs, caches, temp files, or audit artifacts. Audit reports should use offsets, hashes, provenance, and risk scores rather than plaintext identifiers. Core dependencies must remain permissive-license compatible; GPL, source-available, proprietary, DUA-gated data, UMLS, SNOMED CT, CPT, MIMIC, i2b2, and n2c2 assets must not be bundled. Put restricted integrations behind optional user-supplied keys or out-of-process bridges.

## Testing and Release Gates

Pytest discovers `test_*.py` and `*_test.py`. Mark external or end-to-end cases with `@pytest.mark.integration`; mark expensive checks with `@pytest.mark.slow`. Pull requests run `-m "not slow and not fuzz"`; pushes to `master` and PRs labelled `full-ci` run everything. Keep bundled tests offline-friendly through mocks or synthetic fixtures. Do not add wall-clock thresholds, exact byte budgets or assertions on the versions currently in `uv.lock`; see `docs/testing.md`.

For privacy, aggregate F1 is not enough. Add tests or fixtures for direct-identifier recall, critical leakage, span integrity, deterministic safety sweeps, date shifting, surrogate consistency, multilingual IDs, and quantized-model recall deltas when touching those paths. Committed golden data must be synthetic; DUA datasets are eval-only and never committed.

## PyPI Release Publishing Guardrails

The `openmed` PyPI release is tag-driven through `.github/workflows/publish.yml`.
Before changing that workflow, read `docs/release/trusted-publishing.md` and
run `tests/unit/test_publish_workflow_version.py` plus
`tests/unit/release/test_provenance_workflow.py`.

The `PYPI_API_TOKEN` secret is scoped to the GitHub `pypi` environment. Do not
remove the publish job's `environment: pypi` block unless the secret is moved
and the release tests/docs are updated in the same change. Do not switch back
to tokenless Trusted Publishing unless the PyPI `openmed` project is already
configured with a matching trusted publisher for owner `maziyarpanahi`,
repository `openmed`, workflow `publish.yml`, and environment `pypi`.

If `pypa/gh-action-pypi-publish` runs without a non-empty `password`, it falls
back to Trusted Publishing and fails with `invalid-publisher` when the PyPI
publisher is missing. Treat that as a release-blocking configuration regression,
not as a transient PyPI outage. GitHub OIDC/SLSA attestation outages are
separate: the release workflow may make provenance evidence best-effort, but
package build, version checks, `twine check`, artifact upload, and PyPI publish
must still pass before a release is considered done.

## Commit & Pull Request Guidelines

Recent history uses concise imperative commits, often with prefixes such as `fix:`. Keep commits and PRs focused on the requested change. Use the repo-owner Git identity. Do not include assistant/tool/vendor names, co-author trailers, generation footers, or worker attribution in issue text, branch names, commit messages, PR titles, PR bodies, or labels. In particular, never use `codex` or `claude` in branch names, PR titles, PR bodies, issue titles, issue bodies, labels, or other repository metadata. Do not assign issues or PRs unless explicitly asked.

Before pushing or merging, run `python scripts/release/check_repo_policy.py
--commits origin/master HEAD` (substitute the actual target branch when needed).
This checks every incoming commit, including merge side parents, for forbidden
author/committer identities and attribution footers. Checking only the PR opener,
primary author, latest commit, or first-parent log is insufficient. Preserve
human contributor identity when correcting metadata. Never bypass the required
`Commit attribution policy` status. Its trusted base-branch validator runs in the
Secret Scan workflow and posts the result on the exact PR head; it never executes
PR code. Keep the repository ruleset active, with no bypass actors, for `master`
and `release/**` branches. A missing or failed check must block the merge.

Pull requests should follow `.github/PULL_REQUEST_TEMPLATE.md`: description, change type, tests run, docs or changelog updates when applicable, dependency justification, linked issue, and screenshots or example output for UI/docs changes.

## Reviewing Existing PRs After Lint Changes

Do not start review on a stale branch when a repo-wide lint or formatting baseline has landed. First require the branch to update from latest `master` by rebasing or merging `master` into the PR branch.

After the branch is updated, run the canonical formatter on that branch and commit only files that belong to the PR's actual scope. Do not run broad formatting on a stale branch before updating from `master`; that creates avoidable churn and makes review harder.

Expect overlapping PRs to need small formatting follow-up commits only for files they already touch. They should not gain the full baseline diff. If updating a PR produces unrelated formatting-only changes outside its scope, stop and ask before reviewing or committing those changes.

Before approving or merging another contributor's PR, verify that its branch is current enough for CI, the relevant lint commands pass, and the diff does not contain unrelated formatting churn.

## Tracked Files and Commit Safety

Do not add untracked files to git unless the user explicitly asks for those files to be tracked. Never force-add or commit ignored files or ignored directories. Stage only files that are already tracked or files the user has explicitly approved for tracking.

When a file is untracked, ignored, private-looking, generated, or outside the requested scope, leave it alone. If there is any doubt about whether a file should be staged, committed, pushed, or included in a pull request, stop and ask the user before taking the git action.

## OpenMedKit and Multimodal Work

Swift and Python surfaces should evolve together unless a plan explicitly scopes a feature to one platform. OpenMedKit priorities are on-device document intake, OCR, PII redaction, structured extraction, task packs, model catalog/download/cache UX, and later pose, audio, speech, and sensor pipelines. Any borderline or medical-device-style feature must surface disclaimers and must not auto-trigger clinical decisions. Apple Foundation Models paths must stay on-device only; reject any cloud fallback for PHI workflows.

## Fork: Australian De-identification Service (`v3.2.0-mt.N`)

This fork (branch `feat/au-pii-support`, base upstream `29ffa7005`) adds an Australian de-identification layer and a lean REST service for one consumer. It is released separately from upstream openmed: fork releases are tagged `v3.2.0-mt.N` and built with `scripts/release_mt.sh`, never through `.github/workflows/publish.yml`. Keep fork features isolated, so that upstream behaviour does not change.

### Scope and isolation rules

- **AU only, behind the locale.** Every AU recogniser runs only for the `en_AU` locale. The `ccia_*` policies imply `en_AU`. Output for `locale=None`, `en_US`, `en_GB` and every other locale, and for every non-`ccia_*` policy, must stay byte-identical to upstream. Each AU module has a `test_non_au_locale_unchanged` test; extend it when you add patterns.
- **Wire in through the hooks.** AU patterns are `PIIPattern` lists appended to `_AU_ENGLISH_PII_PATTERNS` in `openmed/core/pii_i18n.py`, one hook line per list. Do not edit the shared patterns for other locales.
- **Fail closed.** When unsure, over-redact rather than leak. Record accepted over-redaction in the test docstring.
- **No unlabelled number detection.** Record-number and clinician-ID patterns need a label directly before the value. Checksum identifiers (Medicare, IHI, TFN, ABN, ACN, provider number) must pass their validator.
- **Keep fork changes reviewable as a set.** Fork edits to shared modules (`pii.py`, `pipeline.py`, `audit.py`, `labels.py`, `safety_sweep.py`, `apply_spans.py`, `safe_harbor.py`, `verify_pdf.py`) must be minimal and covered by tests. Prefer new `au_*` modules or `openmed/service/lean/` over changing upstream logic.

### Where the fork code lives

| Area | Files |
|---|---|
| AU recognisers | `openmed/core/au_identifiers.py` (Medicare/IRN, IHI, TFN, ABN, ACN); `au_contact.py` (phone, postcode, address, state); `au_records.py` (UR/MRN `AU_UR`, header fields `AU_HEADER`, title-line MRN, print-footer USERNAME, labelled-value propagation, table header vocabulary); `au_dates.py` (day-first dates); `au_nrp.py` (ETHNICITY/NRP deny list); `au_clinician_ids.py` (AHPRA, DVA, Medicare provider number) |
| Labels | AU canonical labels in `openmed/core/labels.py`: `MEDICARE_NUMBER`, `IHI`, `TFN`, `ABN`, `ACN`, `AHPRA_NUMBER`, `DVA_NUMBER`, `MEDICARE_PROVIDER_NUMBER`. They are mirrored in the policy JSONs, `thresholds.json`, the schemas and the Android/Swift/JS SDKs. A new label needs the full sweep; use commit `f1630fe1a` as the checklist |
| Policies | `openmed/core/policies/ccia_l1.json`, `ccia_l2.json` and `ccia_l3.json`, plus the `shift` action. They keep clinical, oncology, age and gender concepts; shift dates; and mask every DIRECT_IDENTIFIER |
| Apply and audit | `openmed/core/apply_spans.py` (`mark`, date shift, `[LABEL_n]` and `[PERSON_FN/MN/LN_n]` tags); `openmed/core/apply_audit.py` (signed audit); `openmed/compliance/safe_harbor.py` (`any_policy` attestation) |
| Lean service | `openmed/service/lean/`. Endpoints: `/health`, `/v1/provenance`, `/v1/detect` (including `table_header`/`cells`), `/v1/apply`, `/v1/selftest`, `/v1/verify/pdf`, `/v1/policies/{name}/coverage`. The contract is documented in `docs/service/lean-service.md` |
| Image | `docker/lean-service.Dockerfile` installs `[service,lean-pdf,hf]` with CPU-only torch. No model weights are baked in |

### Runtime configuration

- **`OPENMED_AUDIT_KEY`** is required. The service refuses to start without it (`AUDIT_KEY_MISSING`). It signs every `/v1/apply` audit and keys `input_hash` and `text_hash`. Never log, echo, bake into an image or commit it. Tests get it from `tests/unit/service/conftest.py`; do not set it globally, because the CLI reads the same variable.
- **`OPENMED_LEAN_MODEL`** selects the PII model; the default is the English PII model. The model loads on the first detect or selftest request. On PHI hosts, mount a pre-populated Hugging Face cache and set `HF_HUB_OFFLINE=1`.
- **`OPENMED_FORK_TAG`** is set by the image build and reported by `/v1/provenance`.
- **Extras:** the lean service needs `service`, `lean-pdf` (pdfplumber, pdfminer.six, pypdfium2) and `hf` (torch, transformers). PyMuPDF is AGPL and must never become a dependency. Its integration test is opt-in, so run it in a throwaway environment.

### Privacy invariants for fork code

- No raw text, original values, surrogate values, offset days or keys in any response, log, error, audit or attestation. Audits carry offsets, keyed hashes and provenance only.
- **Uploads:** `/v1/verify/pdf` parses uploads in memory with no temp files, runs in the threadpool, caps requests at 256 regions and 64 MiB, and uses a 25 Mpx render budget. It silences the `pdfminer`, `pdfplumber`, `pypdfium2` and `PIL` loggers, which would otherwise log page text.
- **Dates:** a shifted date is residual risk in the attestation, not removal.
- **Test data:** use only synthetic values, with Medicare, IHI and similar numbers produced by the repo validators and phones from the ACMA fictional ranges. Never copy values from real reports.

### Tests and checks for fork work

- **Model-free tests:** use `Pipeline(policy="ccia_l3", model_detector=stub)`, where the stub returns an empty `PredictionResult`, and `TestClient(create_app())` with `get_model_detector` overridden. See `tests/unit/core/test_au_dates.py` and `tests/unit/service/test_lean_detect.py`.
- **Real model:** stubbed tests miss offset and model-interplay leaks, so run the real-model `/v1/selftest` before a release: `HF_HUB_OFFLINE=1`, `OPENMED_AUDIT_KEY` set, `with TestClient(create_app())` so the lifespan runs. It must report six `pass` results.
- **Fast suite:** `-m "not slow and not fuzz"`. The known upstream failures are `test_deletion_verify` (windows ×2) and `test_grpc_generated_stubs_are_current`.
- **Repository and remotes:** fork work lives in the private Bitbucket repository `cciacb/openmed_au`, as git remote `private`, which is the push default. The public GitHub fork (`origin`) is frozen: never push fork work, branches or tags to it. `upstream` (maziyarpanahi/openmed) is for fetching only. The GitHub Actions workflows under `.github/` do not run on Bitbucket, so run the checks they cover locally before a release: the fast suite, `ruff`, `scripts/release/check_repo_policy.py`, and `mkdocs build --strict` when the docs change.
- **Releases:** build from a clean checkout of the tagged commit; `release_mt.sh` refuses a dirty tree. Tags are created and pushed by the repository owner only.

### Local planning material (never commit)

Fork plans, tickets, release notes and the synthetic demo set live in the untracked `openmed-fork-integration/` folder. Its `Data_to_learn/` subfolder holds **real patient reports**. Never stage, commit, upload, quote or copy from it, and never read it unless the user explicitly scopes the files. `API_USAGE.md` and `.superpowers/` are also local only.
