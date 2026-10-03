# Security policy

## Reporting a vulnerability

1. **Do not** open a public issue for an exploitable vulnerability.
2. Contact the maintainer through GitHub ([MrAnayDongre](https://github.com/MrAnayDongre)) with a description, reproduction steps and impact.
3. Allow reasonable time for a fix before public disclosure.

## What the design does and does not promise

The threat model, trust boundaries, guarantees and **non-guarantees** are in [docs/security.md](docs/security.md).
In short: PatchQuest runs model-chosen commands against source trees, so the API is authenticated and loopback-only by
default, repository paths are restricted, commands go through a deterministic policy gate and a shadow workspace, the
real repository is written once after validation, tenants are isolated by workspace, and security events are audited.
It does **not** claim VM-level isolation from Docker, and it is not compliance-certified.

## Secrets

- API keys come from environment variables; configuration stores variable *names*, never values.
- Command output, checkpoints, model-call records and reports are secret-redacted before they are stored.
- API tokens are stored only as SHA-256 hashes and shown once.
- Workflows and connectors refer to secrets by reference; literal secrets in a definition are rejected.

Never commit `.env` files, real keys (`sk-…`, `ghp_…`, `nvapi-…`), private keys, OAuth token files, or a `config.yaml`
with embedded credentials. Test fixtures use obviously fake key patterns for detection tests only.
