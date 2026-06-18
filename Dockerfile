# Vigil API + worker — ONE shared image (Gate D1). Two compose services run off it:
#   api    -> uvicorn vigil.api.app:app            (:8000)
#   worker -> arq vigil.workers.settings.WorkerSettings
#
# Security model PRESERVED: this image carries NO secrets. At runtime the app reads its secrets
# (jwt signing key, the DB DSN) from Vault via VAULT_ADDR/VAULT_TOKEN env, and connects to Postgres
# as the NON-superuser vigil_app under RLS — exactly as the host flow does. The only difference in
# a container is the secret SOURCE host (a dev Vault) and service-name networking.
#
# The 636MB data/ tree (synthetic parquet, model artifacts, vendored embedder) is BIND-MOUNTED
# read-only at runtime (see docker-compose.dev.yml), never baked into the image.
FROM python:3.12-slim

# uv resolves + installs from the committed lockfile (uv.lock). Installed via pip to avoid a
# mutable external image tag.
RUN pip install --no-cache-dir uv

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 1) Dependency layer — cached unless pyproject/uv.lock change. Runtime deps only (--no-dev);
#    package=false so there is no project to install (--no-install-project is belt-and-suspenders).
#    --no-cache so the downloaded wheels are not ALSO persisted in the layer's uv cache (the .venv
#    copy is enough) — keeps the image as lean as the torch+ST runtime allows.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# 2) App code. data/ is excluded (.dockerignore) and mounted read-only by compose at runtime.
COPY vigil/ ./vigil/
COPY models/ ./models/
COPY ingestion/ ./ingestion/
COPY scripts/ ./scripts/
COPY alembic.ini ./

EXPOSE 8000

# Default = the API; the worker service overrides `command:` in compose.
CMD ["uvicorn", "vigil.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
