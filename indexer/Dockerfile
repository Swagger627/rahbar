# RAHBAR indexer image: the Kubernetes Job (and the weekly CronJob) that builds the
# index and exits. It is the only writer to Chroma (section 4.2).
#
#   docker build -t rahbar-indexer:dev indexer
#   docker run --rm rahbar-indexer:dev --help
#   docker run --rm rahbar-indexer:dev --dummy        # three fake chunks, needs Chroma
#
# The same image serves both modes: the Job passes args: ["--dummy"] while the cluster is
# brought up, then no args for the real pipeline. The corpus is NOT baked in: third-party
# PDFs stay out of images and Git, and the Job mounts them at CORPUS_DIR (/corpus).
# Non-root, and writes only to /tmp, so readOnlyRootFilesystem: true works with an
# emptyDir at /tmp (OCR's temporary page images go there).

# Base pinned by tag and digest: Python 3.12 on Debian 13 (trixie), slim variant.
# ---- build stage: install the pinned dependencies into a self-contained virtualenv
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && /opt/venv/bin/python -m compileall -q /opt/venv

# ---- runtime stage: OCR system tools, the virtualenv and the pipeline code
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS runtime
# tesseract-ocr (with its English data) reads scanned pages; poppler-utils provides
# pdftoppm for pdf2image and pdffonts for detecting pages with no text layer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TMPDIR=/tmp \
    CORPUS_DIR=/corpus
RUN groupadd --system --gid 10001 rahbar \
 && useradd --system --uid 10001 --gid rahbar --no-create-home --shell /usr/sbin/nologin rahbar \
 && mkdir -p /corpus
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY pipeline ./pipeline
RUN python -m compileall -q /app/pipeline
USER 10001:10001
ENTRYPOINT ["python", "-m", "pipeline.run"]
CMD []
