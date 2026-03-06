# QuickPat Plan

## Priority 1 — Fix What's Wrong

1. ~~**Secret detection is too noisy**~~ — DONE. Filters out references (`secretName`, `secretKeyRef`, `tokenSecretName`), config flags (`useCollectorToken`, `useServiceAccountToken`), paths (`collectorTokenPath`), generic keys (`key`, `secrets`). Dropped from 30+ to 15 real secrets on ai-obs.

2. ~~**Duplicate secrets in values-secret.yaml.template**~~ — DONE. Deduplicates by appending parent path segment (`pgvector_secret`, `minio_secret`) and counter suffix when needed.

3. ~~**OpenShift AI labels applied to every namespace**~~ — DONE. Per-chart `needs_oai_labels` flag based on inference CRDs in templates or inference dependencies (llm-service, vllm, llama-stack). Only matching namespaces get labeled.

4. ~~**`print_results` in cli.py**~~ — DONE. Lists all chart directories from the actual output for multi-chart patterns.

## Priority 2 — Improve Multi-Chart

5. **Smarter namespace grouping** — Group charts by subdirectory structure (`observability/` → shared namespace) as a heuristic instead of one namespace per chart.

6. **Test skills pipeline with multi-chart** — Run `skills/transform_quickstart.py transform` against a multi-chart quickstart.

## Priority 3 — Harden

7. **Unit tests** — Analyzer, validator, generator. At minimum: single-chart, multi-chart, secret dedup, validator fix loop.

8. ~~**Test against remaining quickstarts**~~ — DONE. All 11 registered quickstarts pass (create + validate). Includes multi-chart: product-recommender (3), maas-code-assistant (8), ansible-log-analysis (9), lls-observability (18).

## Priority 4 — Shared Chart Intelligence

See [shared-charts-analysis.md](shared-charts-analysis.md) for full data.

All quickstarts pull from a single shared Helm repo (`ai-architecture-charts`) with 9 reusable charts (pgvector, llm-service, llama-stack, mcp-servers, minio, etc.). Heavy reuse but significant version drift and some local forks.

9. **Dependency freshness check** — Fetch the `ai-architecture-charts` index and flag stale dependency versions in `quickpat analyze` output. e.g. "pgvector 0.1.0 → latest 0.5.5".

10. **Detect local forks of shared charts** — When a quickstart bundles a chart that also exists in `ai-architecture-charts`, flag it. The user can decide whether to use the local copy or switch to the shared dependency.

11. **External chart strategy for shared deps** — When generating a pattern with `--chart-strategy external`, reference shared charts from `ai-architecture-charts` by URL instead of copying locally. Only copy truly local/custom charts.

## Priority 5 — Registry Integration

See [pub-integration-plan.md](pub-integration-plan.md) for details.

- ~~**`quickpat list`**~~ — DONE.
- ~~**`quickpat create <name>`**~~ — DONE.
- 12. **`quickpat batch`** — Transform all registered quickstarts. Useful for CI/testing.
- 13. **Publication readiness check** — Programmatic subset of the pub checklist.
