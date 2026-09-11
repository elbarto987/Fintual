# syntax=docker/dockerfile:1

#
# ---- Stage 1: builder -------------------------------------------------
# Uses astral's official uv image (has uv preinstalled) to resolve and
# install dependencies into a self-contained virtualenv, so the runtime
# image never needs uv, pip, or build tooling.
#
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (no dev group: no pytest/ruff in the image),
# separately from app code, so this layer stays cached across code changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now copy the actual application code and finish the sync (installs the
# project itself, not just its dependencies).
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

#
# ---- Stage 1.5: test -----------------------------------------------------
# Extends the builder (which already has the venv + app code + uv itself),
# adding the dev dependency group (pytest, pytest-django, ruff) on top.
# Never used for the runtime image — only for `docker compose run test`.
#
FROM builder AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

CMD ["uv", "run", "pytest", "-v"]

#
# ---- Stage 2: runtime ---------------------------------------------------
# Minimal slim image: only the venv + app code, no compilers, no uv.
#
FROM python:3.14-slim-bookworm AS runtime

# psycopg[binary] bundles libpq, so no extra system packages are needed
# for the DB driver. libpq5 is included in the slim base already via
# psycopg's wheel; curl is added only for the container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app /app

# Force the executable bit regardless of the permissions the entrypoint
# script had on the host filesystem (e.g. lost when downloaded/copied),
# so a chmod mistake outside the image never breaks the container.
RUN chmod +x /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=core.settings

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD curl -f http://localhost:8000/api/docs || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
