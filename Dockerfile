# Agents image (docs/adr/0019-docker-local-deployment.md): built and checked in CI only,
# never pushed to a registry. No port, a non-root user, no held-out set (.dockerignore).
ARG PYTHON_IMAGE=python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# --- Build: locked runtime dependencies only (no dev group, no extra, no reranker).
FROM ${PYTHON_IMAGE} AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.11.25
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# --- Runtime: the virtual environment, the fictional corpora and the gold sets.
FROM ${PYTHON_IMAGE}
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    ALVERNO_INDEX_PATH=/var/lib/alverno/index/index.npz \
    ALVERNO_HR_INDEX_PATH=/var/lib/alverno/index/hr/index.npz \
    ALVERNO_ACCESS_LOG_DIR=/var/lib/alverno/logs
RUN groupadd --system --gid 10001 alverno \
    && useradd --system --uid 10001 --gid alverno --home-dir /app --shell /usr/sbin/nologin alverno \
    && mkdir -p /var/lib/alverno/index/hr /var/lib/alverno/logs \
    && chown -R alverno:alverno /var/lib/alverno
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY data ./data
COPY evals ./evals
USER 10001:10001
ENTRYPOINT ["alverno"]
CMD ["--help"]
