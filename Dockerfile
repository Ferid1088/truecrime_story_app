# syntax=docker/dockerfile:1.7
# API + worker image. Build:  docker build -t truecrime-api .
# Lean build without the voice-QA extras (torch, whisper):  --build-arg REQUIREMENTS=requirements.txt

ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS builder
ARG REQUIREMENTS=requirements-documentary.txt
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt requirements-documentary.txt ./
# torch from the CPU-only index (the default Linux wheel bundles ~2.5 GB of GPU libraries)
RUN if grep -q '^torch' "$REQUIREMENTS"; then pip install torch --index-url https://download.pytorch.org/whl/cpu; fi \
    && pip install -r "$REQUIREMENTS"

FROM python:${PYTHON_VERSION}-slim AS runtime
# ffmpeg: audio decode/trim/loudness and video rendering in the documentary pipeline
# postgresql-client: pg_dump for the in-app database backup
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app config ./config
COPY --chown=app:app prompts ./prompts
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app alembic.ini pyproject.toml ./
# Generated media and the SQLite fallback live here; mount a volume on /app/data.
RUN mkdir -p /app/data && chown -R app:app /app/data /app/config
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
