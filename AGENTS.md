# AGENTS.md — QuickPat

## Project Overview

QuickPat converts Red Hat AI Quickstarts into Validated Patterns — GitOps-driven OpenShift deployments using ArgoCD, HashiCorp Vault, and the VP clustergroup chart. Two authoring paths: `quickpat create` (analyze an existing QS Helm chart) and `quickpat compose` (compile a declarative `ApplicationSpec` into a VP or QS Helm chart from the same input).

## Architecture

- **CLI entry point:** `quickpat/cli.py` — subcommands: `list`, `analyze`, `create`, `compose`, `publish-vp`, `update`, `validate`, `new`, `batch`, `check-ready`
- **Pipeline:** `quickpat/pipeline.py` — top-level orchestration for both the `create` path (analyze → generate → validate) and the `compose` path (`compose_from_spec`, `compose_qs_from_spec`)
- **Analyzer:** `quickpat/analyzer.py` — parses Helm charts, detects operators/secrets/GPU/features
- **Generator:** `quickpat/generator.py` — emits VP directory structure (values-global, values-prod, infra charts, ExternalSecrets, overrides, Makefile, pattern.sh)
- **Publish:** `quickpat/publish.py` — `publish_vp()` publishes a repo's already-committed `vp-out/` tree to an immutable `vp-v{N}` tag whose tree sits at the tag's own root. Exists because the Validated Patterns Operator's `Pattern` CRD has no subdirectory-path field — it always clones the whole repo and expects `values-global.yaml`/`values-prod.yaml`/`Makefile`/`pattern.sh`/`charts/` at the repo root, which breaks any layout (like the one-repo-both-paths model) that nests `vp-out/` under something else. Uses `git commit-tree` directly on `HEAD:vp-out` (no worktree, no subtree, correct `.gitignore` handling for free), chains each tag's commit to the previous one for free diffing, and derives the next version from the remote's existing tags rather than local state. Repointing a live `Pattern` CR at a new tag is deliberately left as a printed suggestion, never executed automatically.
- **Validator:** `quickpat/validator.py` — structural checks + Patternizer conformance checks + auto-fix loop
- **Compose package:** `quickpat/compose/` — ApplicationSpec authoring path:
  - `parser.py` — loads and validates `ApplicationSpec` YAML into typed dataclasses
  - `blocks.py` — block type registry (9 types → operators, DSC config, labels)
  - `compiler.py` — translates `ApplicationSpec` → `(QuickstartAnalysis, config)` for `PatternGenerator`
  - `block_templates.py` — inline Kubernetes manifest generators per block type (QS output)
  - `qs_generator.py` — writes the QS Helm chart directory
  - `renderer.py` — resolves `{{ blocks.X.output.Y }}` wiring references
- **Update command:** `quickpat/cli.py:cmd_update` — re-clones the upstream QS repo and re-runs the `create` path against an existing pattern, preserving the `.quickpat/profile.yaml` fingerprints and secret/drift decisions. Detects upstream drift via subchart hash comparison.
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

## Patternizer Conformance

Generated output must conform to the VP authoring rules from [Patternizer](https://github.com/validatedpatterns/patternizer)'s `SKILL.md` and `reference.md`. Key conventions enforced:

- Namespaces as maps (not lists) — maps merge across values files
- ESO backtick escaping in ExternalSecret templates
- Chart paths: `charts/<name>` (not `charts/all/` or `charts/hub/`)
- Secrets charts must have `values.yaml` with `secretStore` stubs
- `singleArgoCD: true`, `multiSourceConfig.enabled: true`

The validator (`validator.py`) checks these both deterministically and via LLM-enhanced review (21 rules in `VALIDATION_CHECKLIST`). When adding new generation logic, verify against the Patternizer skill files at `/path/to/patternizer/src/internal/embedded/skills/pattern-author/`.

## Security

- API keys via environment variables only (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPINFRA_API_KEY`)
- Pre-commit hook runs gitleaks to block secrets in commits
- Never commit real credentials or API keys
