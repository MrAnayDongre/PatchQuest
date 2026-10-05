# Security policy

PatchQuest runs commands that a model chose, against source trees, and can send data to model providers and connected
services. Security reports are taken seriously.

## Reporting a vulnerability

1. **Do not open a public issue, discussion or pull request** for an exploitable vulnerability.
2. Report it privately: use **GitHub's "Report a vulnerability"** button on the repository's *Security* tab (private vulnerability
   reporting) once it is enabled. Until a dedicated security contact is published here, message the maintainer
   ([@MrAnayDongre](https://github.com/MrAnayDongre)) privately on GitHub.
3. Include what you found, the version or commit, how to reproduce it, and what an attacker gains.
4. Please allow reasonable time for a fix before public disclosure. We will acknowledge the report, keep you informed, and credit you if you wish.

Do **not** post in public: working exploits, other people's credentials or tokens, run bundles or logs that contain secrets or
private source, or a vulnerable instance's address.

## What the design promises, and what it does not

The full threat model, trust boundaries, guarantees and non-guarantees are in [docs/security.md](docs/security.md). In short:

- **Commands** go through a deterministic policy gate, run inside a shadow copy of the repository with a scrubbed environment, and
  the real repository is written once, after validation, with sha256 preconditions and a journal.
- **The Docker runtime is not a virtual machine.** A kernel or container-runtime escape defeats it ([docs/sandbox.md](docs/sandbox.md)).
  The default `local` runtime runs commands as your user: use the Docker runtime for untrusted repositories or weak models.
- **Plugins** are installed by an operator. A `trusted` plugin is ordinary Python with the server's privileges; an `external_process`
  plugin gets a fresh subprocess with resource limits, but this is not a sandbox. Permissions are declarations the operator accepts,
  not operating-system enforcement ([docs/plugins.md](docs/plugins.md)).
- **Tenants** are isolated by workspace; foreign ids return 404; policy can restrict network reads and what data may leave
  ([docs/tenancy.md](docs/tenancy.md), [docs/policy.md](docs/policy.md)).
- **Connectors** are tested against protocol simulators, not live services ([docs/connectors.md](docs/connectors.md)).
- PatchQuest is not compliance-certified and has not had an external security audit. SSO/OIDC, per-tenant quotas and per-tenant encryption keys are not implemented.

## Secrets

- API keys come from environment variables; configuration stores variable *names*, never values.
- Command output, checkpoints, model-call records and reports are secret-redacted before they are stored.
- API tokens are stored only as SHA-256 hashes and shown once. Integration secrets are stored as environment references or
  encrypted values bound to their row.
- Workflows and connectors refer to secrets by reference; literal secrets in a definition are rejected.

Never commit `.env` files, real keys (`sk-…`, `ghp_…`, `nvapi-…`), private keys, OAuth token files, or a `config.yaml` containing
credentials. Test fixtures use obviously fake key patterns for detection tests only.

## Runtime trust assumptions

PatchQuest assumes the host, the database and the operator are trusted. The API is authenticated and loopback-only by default;
binding elsewhere without a token refuses to start. TLS is not terminated by PatchQuest: put a reverse proxy in front. Webhook
ingress verifies signatures, rejects replays and deduplicates deliveries.

## Supported versions

PatchQuest is pre-1.0. Fixes land on the default branch.
