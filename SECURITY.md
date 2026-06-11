# Security Policy

## Reporting a vulnerability

If you discover a security issue in PatchQuest, please report it responsibly:

1. **Do not** open a public GitHub issue for exploitable vulnerabilities.
2. Email the maintainer via GitHub ([MrAnayDongre](https://github.com/MrAnayDongre)) with a description, reproduction steps, and impact assessment.
3. Allow reasonable time for a fix before public disclosure.

## Scope

PatchQuest is designed as a **local, single-user** tool. Security focus areas:

- Command execution and sandbox escape
- Path traversal outside the workspace
- Secret leakage in logs, reports, or SQLite
- Unsafe defaults in command risk classification
- Docker sandbox isolation

Out of scope for this project: multi-tenant isolation, network-facing authentication (not implemented by design).

## Secret handling

PatchQuest follows these rules:

- API keys are read from **environment variables** only.
- Config files store env var **names**, never key values.
- SecretGuard scans diffs, command output, memory records, and reports.
- Detected secrets are redacted in persisted and streamed output.

**Do not** commit:

- `.env` files
- Real API keys (`sk-…`, `nvapi-…`, `gsk_…`, etc.)
- Private keys or OAuth token JSON
- Local `config.yaml` with embedded credentials

Test fixtures use obviously fake key patterns (e.g. `sk-abc123…`) for detection tests only.

## Safe usage recommendations

- Run PatchQuest on trusted machines with trusted repositories.
- Use Docker runtime for untrusted code when Docker is available.
- Review the Safety Queue before approving risky commands.
- Keep provider API keys out of task descriptions and repo files.
- Treat mock mode as deterministic demo/testing—not a security boundary.

## Dependencies

Report third-party dependency vulnerabilities through the normal GitHub issue tracker once confirmed they affect PatchQuest's usage path.
