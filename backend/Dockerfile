# syntax=docker/dockerfile:1
# -- Stage 1: Build & Dependencies --
FROM python:3.12.15-slim AS builder

WORKDIR /build

# Install compiler & development headers for wheels
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install locked dependencies into separate prefix
COPY requirements.lock .
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock

# Copy backend source and install non-editably
COPY . .
RUN pip install --no-cache-dir --no-deps --prefix=/install .

# -- Stage 2: Production Minimal Runtime --
FROM python:3.12.15-slim AS runtime

ARG GIT_COMMIT=unknown
ARG BUILD_TIMESTAMP=unknown
ARG IMAGE_TAG=unknown

LABEL org.opencontainers.image.title="OMEGA Content Engine" \
      org.opencontainers.image.description="Autonomous Content Operating System" \
      org.opencontainers.image.source="https://github.com/nguyenvantuan301207-sudo/OmegaContentEngine" \
      org.opencontainers.image.revision="" \
      org.opencontainers.image.created="" \
      org.opencontainers.image.version=""

WORKDIR /app

# Install runtime-only system libraries (no compilers or dev headers)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy installed python packages from builder
COPY --from=builder /install /usr/local

# Install Playwright browsers into system location
RUN python -m playwright install --with-deps chromium

# Copy application source & runtime entrypoint
COPY . .
RUN chmod +x /app/entrypoint.sh

# Record immutable build provenance metadata for authenticated /ops/status (H5)
RUN python -c "import json; json.dump({'git_commit': '', 'image_tag': '', 'build_timestamp': ''}, open('/app/build_metadata.json', 'w'))"

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
