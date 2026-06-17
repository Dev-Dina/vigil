# The isolated public Guide image (specs/infra.md — service `guide`, :8080).
# Contains ONLY the guide/ package + its minimal deps — none of the vigil/ app. The image build
# and run are exercised in the Phase-8 deploy demo; CI proves isolation statically (tests/guide/).
FROM python:3.12-slim

WORKDIR /app

# Minimal, Guide-only dependency set — no app DB driver, no Vault/Redis/queue client.
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn>=0.30.0" \
    "pydantic-settings>=2.5.0" \
    "sqlalchemy>=2.0.36"

COPY guide/ ./guide/

EXPOSE 8080

CMD ["uvicorn", "guide.app:app", "--host", "0.0.0.0", "--port", "8080"]
