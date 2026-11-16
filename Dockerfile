FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system adapter && useradd --system --gid adapter --home-dir /app adapter

COPY pyproject.toml README.md ./
COPY adapter ./adapter

RUN pip install --no-cache-dir .

USER adapter

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=3).read()" || exit 1

CMD ["python", "-m", "adapter.server"]
