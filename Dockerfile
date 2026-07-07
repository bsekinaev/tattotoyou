# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12.12
ARG UV_VERSION=0.10.0

FROM python:${PYTHON_VERSION}-slim AS runtime

ARG UV_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY pyproject.toml uv.lock README.md ./

RUN python -m pip install --upgrade pip \
    && python -m pip install "uv==${UV_VERSION}" \
    && uv export --default-index https://pypi.org/simple --locked --no-dev --extra rag --no-emit-project \
        --format requirements.txt --output-file /tmp/requirements.lock \
    && python -m pip install --requirement /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock \
    && python -m pip uninstall --yes uv

COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./

RUN python -m pip install --no-deps . \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
