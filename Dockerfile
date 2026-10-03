# PatchQuest: API + built UI in one image. Workers run the same image with a different command.
#
#   docker build -t patchquest .
#   docker run --rm -v pq-data:/data patchquest patchquest admin init --org Acme --workspace main --owner you
#   docker run -p 8000:8000 -v pq-data:/data -v "$PWD:/repos/app" patchquest
#
# Multi-stage: Node builds the UI, then only the wheel and the static files reach the runtime image.

FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS wheel
WORKDIR /src
RUN pip install --no-cache-dir build
COPY backend/ ./
RUN python -m build --wheel --outdir /dist

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/data/home \
    PATCHQUEST_DB=/data/patchquest.db \
    PATCHQUEST_STATIC_DIR=/app/static
RUN useradd --create-home --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin patchquest \
    && mkdir -p /data /repos && chown 10001:10001 /data /repos \
    && apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=wheel /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
COPY --from=ui /ui/dist /app/static
USER 10001
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status == 200 else 1)"
# 0.0.0.0 inside the container: the API refuses to start there without a token (admin init, or PATCHQUEST_API_TOKEN).
CMD ["patchquest", "serve", "--host", "0.0.0.0"]
