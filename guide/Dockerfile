# The isolated public Guide image (specs/infra.md — service `guide`, :8080).
# Contains ONLY the guide/ package + its OWN deps — none of the vigil/ app, no app DB driver, no
# Vault/Redis/queue client. Import-isolation is enforced by guide/Dockerfile.dockerignore (context
# is the guide/ package only) and proven statically in CI (tests/guide/).
FROM python:3.12-slim

WORKDIR /app

# Guide-only dependency set. Two installs so torch resolves to the CPU wheel (no CUDA bloat) BEFORE
# sentence-transformers pulls it in. The Guide's OWN offline embedder (all-MiniLM-L6-v2) needs
# sentence-transformers + torch; httpx is the Guide's OWN minimal LLM transport (live path only).
# Still NO app DB driver, NO Vault/Redis/queue client — the isolation surface is unchanged.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn>=0.30.0" \
    "pydantic-settings>=2.5.0" \
    "sqlalchemy>=2.0.36" \
    "httpx>=0.27.0" \
    "sentence-transformers>=3.0.0"

COPY guide/ ./guide/

EXPOSE 8080

# Default = serve the Guide. Compose overrides `command:` to build the file-backed approved-doc
# index from the bind-mounted weights BEFORE serving (the index is a Guide-owned artifact).
CMD ["uvicorn", "guide.app:app", "--host", "0.0.0.0", "--port", "8080"]
