FROM ghcr.io/astral-sh/uv:0.13.8 AS uv

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN groupadd --system adapter && useradd --system --gid adapter --home-dir /app adapter

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY adapter ./adapter

RUN uv sync --locked --no-dev --no-editable && rm -f /bin/uv /bin/uvx

ENV PATH="/app/.venv/bin:$PATH"

USER adapter

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=3).read()" || exit 1

CMD ["python", "-m", "adapter.server"]
