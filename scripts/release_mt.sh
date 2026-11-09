#!/usr/bin/env bash
# Build the wheel and the lean service image locally for a fork release tag.
#
# Usage: scripts/release_mt.sh v3.2.0-mt.N
#
# This script does not create git tags, push, or publish anything.
set -euo pipefail

TAG="${1:-}"
if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-mt\.[0-9A-Za-z.]+$ ]]; then
  echo "usage: $0 v<major>.<minor>.<patch>-mt.<N>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

rm -rf dist
python3 -m build --wheel
docker build \
  -f docker/lean-service.Dockerfile \
  --build-arg "FORK_TAG=${TAG}" \
  -t "openmed-lean:${TAG}" \
  .

echo "wheel: $(ls dist/*.whl)"
echo "image: openmed-lean:${TAG}"
