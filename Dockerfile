FROM python:3.13-slim AS build
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Pin the official binary used for development-only Collabora quick tunnels.
FROM cloudflare/cloudflared:2026.6.0 AS cloudflared

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app
RUN groupadd --system stockpile && useradd --system --gid stockpile --home /app stockpile
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY . .
RUN chown -R stockpile:stockpile /app
USER stockpile
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
