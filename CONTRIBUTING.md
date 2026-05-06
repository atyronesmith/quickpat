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

## Code Style

- Keep it simple — flat files, minimal abstraction
- No comments unless the *why* is non-obvious
- API keys via environment variables only, never in config files or code
