# PatchQuest sandbox: where model-chosen commands run when --runtime docker is used.
#
# It is started with no network, no capabilities, a read-only root filesystem and only the
# workspace mounted (see backend/patchquest/runtime/docker_runtime.py). It contains no curl/wget
# and no credentials. Nothing here is fetched with `curl | sh`.
FROM node:20-bookworm-slim AS node

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Node comes from the official image (copied, not downloaded by a pipe-to-shell installer).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN pip install --no-cache-dir "pytest>=8,<9" "ruff>=0.6" "mypy>=1.10"

# Commands run as the caller's uid:gid (--user), so no account is created here.
WORKDIR /workspace
CMD ["sh"]
