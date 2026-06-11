# Contributing to PatchQuest

Thank you for considering a contribution. PatchQuest is a local-first agentic coding harness; changes should preserve safety guarantees, test coverage, and the deterministic orchestration model.

## Development setup

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Frontend (separate terminal)
cd frontend
npm install
```

Run the backend on port 8000 and the frontend dev server on port 5173.

## Before submitting a pull request

1. **Backend tests** — all must pass:
   ```bash
   cd backend && python -m pytest
   ```

2. **Frontend checks**:
   ```bash
   cd frontend
   npm run typecheck
   npm run test
   npm run build
   ```

3. **No secrets** — never commit API keys, `.env` files, or local `config.yaml` with credentials. Use `sample.config.yaml` for examples.

4. **Scope** — keep changes focused. Safety-related code (`secret_guard.py`, `command_risk.py`, `safety.py`) requires extra scrutiny.

## Code conventions

- **Python**: match existing module layout under `backend/patchquest/`. Prefer typed function signatures. Use `logger` instead of `print`.
- **TypeScript**: functional React components, shared types in `frontend/src/api/types.ts`.
- **Tests**: add or extend tests for behavior changes. Place backend tests in `backend/tests/`.

## Pull request process

1. Fork the repository and create a feature branch from `main`.
2. Describe what changed and why.
3. Note any configuration or migration steps.
4. Confirm tests pass locally.

## Areas where contributions are welcome

- Provider adapters and model configuration
- Tree-sitter language support
- Search and calendar provider integrations
- Frontend UX and accessibility
- Documentation and examples
- Test coverage for edge cases in orchestration and safety

## Questions

Open a GitHub issue for bugs, feature requests, or design discussions before large refactors.
