# Roadmap

No dates. This is what is known to be missing, in the order the evidence suggests it matters. See
[docs/EVIDENCE.md](docs/EVIDENCE.md) and [docs/DEMO_QUALIFICATION.md](docs/DEMO_QUALIFICATION.md) for what is verified today.

**Trust and qualification**
- Live validation of the GitHub, Slack, Linear, Jira and Notion connectors (today: protocol simulators only).
- Multi-host qualification of worker pools sharing one PostgreSQL (today: one host, including real containers).
- Durable-commit (`fsync=on`) PostgreSQL load numbers, and load with real model and test workloads.
- A pull-request action (needs a branch-push action first).

**Team features**
- SSO/OIDC, per-tenant quotas and per-tenant encryption keys.
- An object-storage backend for artifacts; Kubernetes manifests.
- Policy on network use from commands inside a local-mode workspace.

**Product quality**
- An accessibility audit with assistive technology; qualification beyond Chromium.
- Stronger live-model results (the bundled corpus is the yardstick; Qwen3-0.6B solved 0 of 14).
- Smaller workflow-canvas legibility at laptop widths.

**Developer experience**
- A published package and release process; a changelog once there are releases.
