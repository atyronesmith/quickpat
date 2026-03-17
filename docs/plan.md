# QuickPat Plan

## Priority 1 — Fix What's Wrong

1. ~~**Secret detection is too noisy**~~ — DONE. Filters out references (`secretName`, `secretKeyRef`, `tokenSecretName`), config flags (`useCollectorToken`, `useServiceAccountToken`), paths (`collectorTokenPath`), generic keys (`key`, `secrets`). Dropped from 30+ to 15 real secrets on ai-obs.

2. ~~**Duplicate secrets in values-secret.yaml.template**~~ — DONE. Deduplicates by appending parent path segment (`pgvector_secret`, `minio_secret`) and counter suffix when needed.

3. ~~**OpenShift AI labels applied to every namespace**~~ — DONE. Per-chart `needs_oai_labels` flag based on inference CRDs in templates or inference dependencies (llm-service, vllm, llama-stack). Only matching namespaces get labeled.

4. ~~**`print_results` in cli.py**~~ — DONE. Lists all chart directories from the actual output for multi-chart patterns.

## Priority 2 — Improve Multi-Chart

5. ~~**Smarter namespace grouping**~~ — DONE. Charts sharing a subdirectory (e.g. `observability/korrel8r`, `observability/loki`) get a shared namespace. Numbered prefixes like `01-operators` are stripped. lls-obs: 18 charts → 4 namespaces. OAI labels applied if any chart in group needs them.

6. ~~**Test skills pipeline with multi-chart**~~ — DONE. All sub-skills (analyze, detect, transform, validate) work with multi-chart quickstarts including subdirectory grouping. Tested: ai-obs (9 charts), lls-obs (18 charts). No code changes needed.

## Priority 3 — Harden

7. ~~**Unit tests**~~ — DONE. 62 tests: analyzer (25), generator (13), validator (18), operators (6). Covers single/multi-chart, secret filtering, OAI labels, dedup, fix loop.

8. ~~**Test against remaining quickstarts**~~ — DONE. All 11 registered quickstarts pass (create + validate). Includes multi-chart: product-recommender (3), maas-code-assistant (8), ansible-log-analysis (9), lls-observability (18).

## Priority 4 — Shared Chart Intelligence

See [shared-charts-analysis.md](shared-charts-analysis.md) for full data.

All quickstarts pull from a single shared Helm repo (`ai-architecture-charts`) with 9 reusable charts (pgvector, llm-service, llama-stack, mcp-servers, minio, etc.). Heavy reuse but significant version drift and some local forks.

9. ~~**Dependency freshness check**~~ — DONE. Fetches `ai-architecture-charts` index and flags stale versions in `quickpat analyze` output.

10. ~~**Detect local forks of shared charts**~~ — DONE. Flags local charts whose name matches a shared chart in `ai-architecture-charts` and that don't pull it as a dependency. Detected: product-rec (2x minio), lls-obs (llama-stack).

11. ~~**External chart strategy for shared deps**~~ — DONE. Per-chart `strategy` and `repo_url` fields on `ChartInfo`. Generator checks per-chart strategy first, falls back to global `chart_strategy`. Only copies charts with local strategy.

## Priority 5 — Registry Integration

See [pub-integration-plan.md](pub-integration-plan.md) for details.

- ~~**`quickpat list`**~~ — DONE.
- ~~**`quickpat create <name>`**~~ — DONE.
- ~~12. **`quickpat batch`**~~ — DONE. `quickpat batch [--filter NAME] [--keep-going] [-o DIR] [--llm PROVIDER]`. Transforms all registered quickstarts with summary table. Supports `--filter` for substring match and `--keep-going` to continue on failure. All 11 quickstarts pass.
- 13. **Publication readiness check** — Programmatic subset of the pub checklist.

## Priority 6 — Enhanced Questionnaire & Spec-Driven Creation

14. ~~**Enhanced guided questionnaire**~~ — DONE. Interactive mode now includes: pattern tier (sandbox/tested/maintained), add operators not detected, namespace override table (multi-chart), secret classification (prompt/generate/skip), global options (syncPolicy, installPlanApproval). Generator accepts new config keys: `tier`, `secret_config`, `namespace_overrides`, `global_options`. All backward-compatible with defaults.

15. ~~**`quickpat new` from spec YAML**~~ — DONE. `quickpat new <spec.yaml> [-o DIR] [--name NAME]` creates a complete pattern from a declarative spec file — no quickstart source needed. Spec defines charts (local path or external repo), operators, secrets, vault, tier, and global options. Builds `QuickstartAnalysis` from spec and feeds it to the existing generator. See `examples/sample-spec.yaml`.
