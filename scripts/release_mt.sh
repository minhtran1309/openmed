#!/usr/bin/env bash
# Build the wheel and the lean service image locally for a fork release tag.
#
# Usage: scripts/release_mt.sh [--allow-untagged] v3.2.0-mt.N
#
# The build must come from a clean working tree whose HEAD carries exactly the
# given tag, so the image's fork_tag always names the evaluated commit.
# ``--allow-untagged`` skips the tag check (still requires a clean tree) and is
# accepted only for release-candidate tags (``-rc`` suffix).
#
# This script does not create git tags, push, or publish anything.
set -euo pipefail

ALLOW_UNTAGGED=0
if [[ "${1:-}" == "--allow-untagged" ]]; then
  ALLOW_UNTAGGED=1
  shift
fi

TAG="${1:-}"
if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-mt\.[0-9A-Za-z.-]+$ ]]; then
  echo "usage: $0 [--allow-untagged] v<major>.<minor>.<patch>-mt.<N>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "refusing to build: working tree has uncommitted changes" >&2
  exit 3
fi
# Untracked files under the packaged paths would end up in the wheel. Local
# planning files elsewhere in the repo root deliberately do not block a build.
if [[ -n "$(git status --porcelain --untracked-files=all -- openmed pyproject.toml)" ]]; then
  echo "refusing to build: untracked or modified files under packaged paths" >&2
  exit 3
fi

if [[ "${ALLOW_UNTAGGED}" == 1 ]]; then
  if [[ "${TAG}" != *-rc* ]]; then
    echo "refusing to build: --allow-untagged is only for -rc tags" >&2
    exit 3
  fi
elif ! grep -Fxq -- "${TAG}" <<<"$(git tag --points-at HEAD)"; then
  echo "refusing to build: HEAD is not tagged ${TAG}" >&2
  exit 3
fi

REVISION="$(git rev-parse HEAD)"

rm -rf dist
python3 -m build --wheel

# The OncoTree snapshot is package data outside any Python package, so only the
# hatch file selection ships it. Refuse to release a wheel without it.
WHEELS=(dist/*.whl)
if [[ ${#WHEELS[@]} -ne 1 || ! -f "${WHEELS[0]}" ]]; then
  echo "refusing to release: expected exactly one wheel in dist/" >&2
  exit 4
fi
WHEEL_LISTING="$(python3 -m zipfile -l "${WHEELS[0]}")"
if ! grep -Eq '^openmed/data/oncotree/[^/[:space:]]+\.json[[:space:]]' \
  <<<"${WHEEL_LISTING}"; then
  echo "refusing to release: ${WHEELS[0]} has no openmed/data/oncotree/*.json" >&2
  exit 4
fi

docker build \
  -f docker/lean-service.Dockerfile \
  --build-arg "FORK_TAG=${TAG}" \
  --label "org.opencontainers.image.revision=${REVISION}" \
  -t "openmed-lean:${TAG}" \
  .

echo "wheel: $(ls dist/*.whl)"
echo "image: openmed-lean:${TAG} (revision ${REVISION})"
