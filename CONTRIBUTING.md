# Contributing to QuickPat

## Setup

```bash
git clone git@github.com:atyronesmith/quickpat.git
cd quickpat
pip install -e ".[dev]"
pre-commit install
```

The `pre-commit install` step wires up gitleaks to scan every commit for secrets. It runs automatically — no manual steps needed after the initial setup.

## Running Tests

```bash
pytest
```

Skip evaluation tests (require network + LLM keys):

```bash
pytest -m "not eval"
```

## CI Workflow

Every push to `main` triggers `.github/workflows/generate-patterns.yml`:

1. **Generate** — runs `quickpat create` for all 6 shortlisted quickstarts in parallel (matrix strategy)
2. **Validate** — runs `quickpat validate` on each generated pattern
3. **Helm template** — renders `charts/pattern-secrets/` and upstream charts
4. **Publish** — pushes each pattern to a `generated/<name>` branch for CRC/cluster testing

PRs run steps 1-3 (no publish). Generated branches are force-pushed on every main push — they're derived output, not source.

To test a generated pattern on CRC:

```bash
git clone -b generated/RAG https://github.com/atyronesmith/quickpat.git /tmp/rag-pattern
cd /tmp/rag-pattern && ./scripts/deploy.sh
```

## Code Style

- Keep it simple — flat files, minimal abstraction
- No comments unless the *why* is non-obvious
- API keys via environment variables only, never in config files or code
