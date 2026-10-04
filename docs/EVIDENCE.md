# Evidence report

Measured on one machine, 2026-10-03, branch `anay/core-hardening`. Nothing here was pushed or run against a live external service.

| Check | Result |
|---|---|
| `ruff check .` / `mypy patchquest` | clean / no issues in 241 files |
| Backend suite, SQLite | 1823 passed, 18 skipped |
| Backend suite, PostgreSQL 16 | 1833 passed, 8 skipped |
| Line coverage (`pytest --cov=patchquest`, SQLite) | 86% of 18,527 statements |
| Frontend | 330 tests, typecheck and production build pass; browser smoke (`npm run smoke`) |
| Recovery | real SIGKILL tests (API and workers, SQLite and PostgreSQL); container worker-kill run, once, by hand |
| Load | see [benchmarks](benchmarks.md): single machine, PostgreSQL with `fsync=off`; not a production capacity claim |
| Security | tenant-isolation, memory-poisoning, integration and policy suites under `tests/security`; [security](security.md) lists limits |

## Labels
TESTED: automated. LOAD_TESTED: measured at volume (single host). MOCKED_PROTOCOL: GitHub, Slack, Linear, Jira, Notion, webhook (simulators only).

## Limitations (unchanged)
No SSO/OIDC, quotas, object storage or Kubernetes manifests; no live connector verification; no multi-host test; no accessibility audit with assistive technology;
policy does not cover network reads or artifact disclosure; live model quality is weak (Qwen3-0.6B 0 of 14). See [competitive study](competitive-study.md) for what is and is not claimed relative to other systems.

Demo qualification (every stage, with evidence): [DEMO_QUALIFICATION](DEMO_QUALIFICATION.md).
