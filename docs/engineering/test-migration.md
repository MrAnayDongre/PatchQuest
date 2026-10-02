# Test suite migration ledger

The flat `backend/tests/test_*.py` layout (36 files, 11 subsystems mixed, the same DB/config setup copy-pasted
as 15 different autouse fixtures) was restructured into `unit/`, `integration/` and `e2e/` layers.

Every migration step compares the number of collected tests before and after; a step that changes the count
must say why in the last column.

| Old files | New module | Tests (old -> new) | Notes |
|---|---|---|---|
| test_state_machine.py, test_phase_lifecycle.py, test_approval_flow.py | unit/runtime/test_phases_and_state.py | 21 -> 21 | 3 DB fixtures + 2 helpers replaced by shared fixtures |
| test_orchestration_hardening.py | unit/runtime/test_orchestration_contracts.py | 55 -> 55 | DB fixture + 2 helpers replaced by shared ones |
| test_readonly_analysis.py, test_final_report.py | unit/runtime/test_reports_and_analysis.py | 20 -> 20 | DB fixture + helper replaced by shared ones |
| test_provider_layer.py, test_provider_safety.py | unit/providers/test_provider_contracts.py | 34 -> 34 | provider layer fixtures replaced by shared ones; fake endpoint moved to tests/support |
| test_provider_selection.py, test_nvidia_provider.py | unit/providers/test_provider_adapters.py | 51 -> 51 | DB fixtures replaced by shared one |
| test_command_risk.py, test_command_execution.py (policy classes) | unit/security/test_command_policy.py | 10+47 -> 57 | split by layer |
| test_command_execution.py (executor classes) | integration/test_executor.py | 12 -> 12 | real subprocesses => integration |
| test_secret_guard.py, test_path_safety.py | unit/security/test_secrets_and_paths.py | 23 -> 23 |  |
| test_patch_engine.py, test_patch_tools.py, test_validation_failures.py | unit/patching/test_patch_engine.py | 52 -> 52 | legacy result-dict wrappers + failure attribution kept with the engine they wrap |
| test_repo_indexer.py, test_code_graph.py, test_memory_invalidation.py | unit/repo/test_repo_intelligence.py | 20 -> 20 | 3 DB fixtures replaced by shared one |
| test_task_service.py | unit/application/test_task_service.py | 6 -> 6 | moved; DB fixture replaced by shared one |
| test_calendar_models.py, test_calendar_service.py, test_local_calendar.py, test_ics_calendar.py | unit/contrib/test_calendar.py | 20 -> 20 | 2 DB fixtures replaced by shared one |
| test_search_models.py, test_search_registry.py, test_search_service.py | unit/contrib/test_search.py | 16 -> 16 |  |
| test_api_security.py | integration/test_api_boundary.py | 21 -> 21 | DB fixture replaced by shared one |
| test_cli.py | integration/test_cli.py | 9 -> 9 | calc constants/helpers now shared |
| test_sandbox_integration.py | integration/test_docker_sandbox.py | 11 -> 11 | moved (skips without Docker/image) |
| test_validated_patch_pipeline.py | e2e/workflows/test_validated_patch_workflows.py | 24 -> 24 | helpers/constants now shared |
| test_evaluation.py | e2e/workflows/test_evaluation_lab.py | 19 -> 19 | DB/config fixture replaced by shared one |

## Result

| | Before | After |
|---|---|---|
| Backend test files | 36 flat files | 21 modules in `unit/` (15), `integration/` (4), `e2e/` (2) |
| Backend tests collected | 554 | 554 (458 unit, 53 integration, 43 e2e) |
| Frontend test files | 7 colocated with sources | 4 in `frontend/tests/{features,components}` |
| Frontend tests | 26 | 26 |
| DB/config setup fixtures | 15 copies | 1 (`tests/conftest.py::isolated_runtime`) |
| Duplicated helpers (`_insert_run`, event queries, calc repo, fake endpoint) | 4-6 copies each | 1 each in `tests/support` |
| Layer runtimes | one 45 s run | unit 10 s, integration 20 s, e2e 12 s |
| Line coverage (full suite) | n/a (not measured) | 77% of 6,776 statements |

## Removed or rewritten (not just moved)

* `test_uses_responses_endpoint_not_chat_completions` asserted on a string it built itself (a tautology). Rewritten
  to drive `NvidiaProvider` through a fake transport and check the real request (URL and payload shape).
* 15 per-file DB/config fixtures and 6 copies of run/event helpers replaced by shared ones: no behaviour lost; the tests
  that depended on them still pass unmodified.
* No test was deleted for being "redundant": a scan for identical bodies, assertion-free tests, tautologies and skips
  found nothing else to remove. The three assertion-free tests are deliberate "must not raise" invariants.
