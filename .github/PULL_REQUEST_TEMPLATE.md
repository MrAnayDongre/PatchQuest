## What

## Why

## Testing
<!-- Commands you ran and what they showed. A bug fix needs a test that fails without the fix. -->

## Security / side effects
<!-- Does this run commands, write files, send data out, change policy, recovery or replay? Say what could go wrong. "None" is a fine answer if true. -->

## Evidence / screenshots
<!-- UI changes: before and after. Runtime changes: a run id, ledger excerpt or measurement. -->

## Checklist
- [ ] `ruff check .` and `mypy` pass (backend)
- [ ] relevant `pytest` layers pass; PostgreSQL run if persistence changed
- [ ] `npm run typecheck`, `npm test`, `npm run build` pass if the UI changed
- [ ] docs updated if behavior or commands changed
- [ ] no secrets, tokens or private paths
