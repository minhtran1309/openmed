# Lean OpenMed service image: installs the locally built wheel with the
# `service` and `lean-pdf` extras (pdfplumber plus the pypdfium2 raster
# backend for /v1/verify/pdf; never PyMuPDF, which is AGPL) and serves the
# lean app with uvicorn. No model is baked in or loaded at startup. Build
# with scripts/release_mt.sh <tag>.
ARG PYTHON_IMAGE=python:3.11-slim@sha256:be1575ed968de893bd54f4c56315ff7c4736ce522c1bca08fd521731aafc0d76
FROM ${PYTHON_IMAGE}

ARG FORK_TAG=v3.2.0-mt.dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENMED_FORK_TAG=${FORK_TAG}

WORKDIR /app

# dist/ holds the wheel produced by `python -m build --wheel`.
COPY dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir "$(ls /tmp/wheels/*.whl)[service,lean-pdf]" \
    && rm -rf /tmp/wheels

EXPOSE 8080

# The service refuses to start without OPENMED_AUDIT_KEY (the HMAC key that
# signs every /v1/apply audit), so the health check below only passes when the
# container is run with it: docker run -e OPENMED_AUDIT_KEY=... . The key is
# never baked into the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3); sys.exit(0)"

CMD ["python", "-m", "uvicorn", "openmed.service.lean.app:app", "--host", "0.0.0.0", "--port", "8080"]
