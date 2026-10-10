# Enrollment resolves the locally built artifact to an immutable sha256 image ID.
# Build from repository root. No provider keys enter this image or container.
FROM python:3.14-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir uv==0.11.14
WORKDIR /opt/termx
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --frozen --no-dev --no-editable --compile-bytecode
ENV PATH="/opt/termx/.venv/bin:$PATH"
WORKDIR /workspace
USER 65534:65534
ENTRYPOINT ["python", "-I", "-m", "termx.runners.worker"]
