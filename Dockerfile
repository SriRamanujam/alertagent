FROM python:3.14-slim

RUN pip install uv

WORKDIR /app

# Stop pydantic from advertising itself
ENV PYDANTIC_API_NO_BANNER=1

# Install deps before source for layer reuse purposes
COPY pyproject.toml uv.lock README.md LICENSE ./

COPY src/ src/
RUN uv sync --frozen --no-dev

# run as non-root
RUN useradd --no-create-home --uid 1000 alertagent && chown -R alertagent /app
USER alertagent
# The user has no home dir, so give uv a cache dir it can write to.
ENV UV_CACHE_DIR=/app/.cache/uv

EXPOSE 8000
# --no-sync: the environment is complete at build time, so the runtime never writes into .venv.
CMD ["uv", "run", "--no-sync", "uvicorn", "alertagent.app:app", "--host", "0.0.0.0", "--port", "8000"]
