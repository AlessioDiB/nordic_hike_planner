# syntax=docker/dockerfile:1.6
#
# Multi-stage Dockerfile for the Nordic Hike Planner.
#
# Stage 1 (builder): installs the package and its dependencies into a venv.
# Stage 2 (runtime): copies the venv into a slim image with no build tools.
#
# Result: ~150MB image with no build artefacts, running as a non-root user.

ARG PYTHON_VERSION=3.11

# -----------------------------------------------------------------------------
# Stage 1: build the package and its dependencies
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Create an isolated venv so we can copy just /opt/venv into the runtime image
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only the metadata first to leverage Docker layer caching:
# changes to source code don't invalidate the dependency-install layer.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# -----------------------------------------------------------------------------
# Stage 2: minimal runtime image
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Create non-root user to run the app
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Copy the venv and the data file
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app data ./data

USER app

EXPOSE 8000

# Healthcheck: hit /health every 30s, fail after 3 missed responses
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; \
        sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status == 200 else 1)"

CMD ["uvicorn", "nordic_hike_planner.api:app", "--host", "0.0.0.0", "--port", "8000"]