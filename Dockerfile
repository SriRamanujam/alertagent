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

# Stop pydantic from advertising itself
# The user has no home dir, so give uv a cache dir it can write to.
ENV PYDANTIC_API_NO_BANNER=1 UV_CACHE_DIR=/app/.cache/uv

# Install deps before source for layer reuse purposes:
# --no-install-project skips the project itself, so src/ isn't needed yet
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# Now that src/ is present, install the project (deps already satisfied)
COPY src/ src/
RUN uv sync --locked --no-dev

EXPOSE 8000
# --no-sync: the environment is complete at build time, so the runtime never writes into .venv.
CMD ["uv", "run", "--no-sync", "uvicorn", "alertagent.app:app", "--host", "0.0.0.0", "--port", "8000"]
