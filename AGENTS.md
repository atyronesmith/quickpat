# AGENTS.md — QuickPat

## Project Overview

QuickPat converts Red Hat AI Quickstarts into Validated Patterns — GitOps-driven OpenShift deployments using ArgoCD, HashiCorp Vault, and the VP clustergroup chart.

## Architecture

- **CLI entry point:** `quickpat/cli.py` — 7 subcommands (`list`, `analyze`, `create`, `new`, `batch`, `check-ready`, `validate`)
- **Pipeline:** `quickpat/pipeline.py` orchestrates analyze → detect → generate → validate
- **Analyzer:** `quickpat/analyzer.py` — parses Helm charts, detects operators/secrets/GPU/features
- **Generator:** `quickpat/generator.py` — emits VP directory structure (values-global, values-hub, Makefile, etc.)
- **Validator:** `quickpat/validator.py` — structural checks + auto-fix loop
- **LLM providers:** `quickpat/providers/` — Protocol-based classes for OpenAI, Anthropic, Ollama, vLLM, DeepInfra. All optional; deterministic mode works without any LLM.
- **Config:** `quickpat/config.py` — YAML config with deep-merge defaults. API keys come from environment variables, never config files.

## Key Patterns

- LLM providers implement `Provider` Protocol from `providers/base.py` with `def complete(self, system, prompt, **kwargs) -> LLMResponse`. Structured output via `response_schema` kwarg.
- `make_provider(config: dict)` factory in `providers/factory.py` — pass `{"provider": "openai", "model": "gpt-4o-mini"}`.
- All LLM call sites handle `provider=None` (deterministic fallback) and catch exceptions gracefully.
- Config uses a singleton pattern (`config._config`); tests reset it via autouse fixture in `tests/conftest.py`.

## Testing

```bash
pytest                    # all unit tests
pytest -m "not eval"      # skip eval tests (need network + LLM keys)
pytest tests/eval/        # eval matrix: quickstarts x providers
```

- Unit tests use mock providers (`_MockStructuredProvider`, `_MockTextProvider`) — no real API calls.
- Eval tests clone real quickstart repos and optionally call live LLM APIs.
- Test fixtures for chart layouts: `single_chart_quickstart`, `multi_chart_quickstart`, `grouped_chart_quickstart`, `numbered_group_quickstart`, `gpu_chart_quickstart`.

## CI / Generated Branches

`.github/workflows/generate-patterns.yml` runs on every push to `main`:
- Matrix of 6 quickstarts (RAG, maas-code-assistant, product-recommender, lemonade-stand, llm-cpu-serving, data-governance)
- Each: `quickpat create --non-interactive` → `quickpat validate` → `helm template`
- On main push: publishes to `generated/<name>` branches (orphan, force-pushed)
- Generated branches are self-contained patterns with `scripts/` (deploy, undeploy, validate, status)
- These branches are derived output — never edit them directly

## Security

- API keys via environment variables only (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPINFRA_API_KEY`)
- Pre-commit hook runs gitleaks to block secrets in commits
- Never commit real credentials or API keys
