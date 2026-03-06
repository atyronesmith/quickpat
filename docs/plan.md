# QuickPat Plan

## Priority 1 — Fix What's Wrong

1. ~~**Secret detection is too noisy**~~ — DONE. Filters out references (`secretName`, `secretKeyRef`, `tokenSecretName`), config flags (`useCollectorToken`, `useServiceAccountToken`), paths (`collectorTokenPath`), generic keys (`key`, `secrets`). Dropped from 30+ to 15 real secrets on ai-obs.

2. ~~**Duplicate secrets in values-secret.yaml.template**~~ — DONE. Deduplicates by appending parent path segment (`pgvector_secret`, `minio_secret`) and counter suffix when needed.

3. ~~**OpenShift AI labels applied to every namespace**~~ — DONE. Per-chart `needs_oai_labels` flag based on inference CRDs in templates or inference dependencies (llm-service, vllm, llama-stack). Only matching namespaces get labeled.

4. **`print_results` in cli.py** — Still shows single-chart output format for multi-chart quickstarts. Should list all chart directories.

## Priority 2 — Improve Multi-Chart

5. **Smarter namespace grouping** — Group charts by subdirectory structure (`observability/` → shared namespace) as a heuristic instead of one namespace per chart.

6. **Test skills pipeline with multi-chart** — Run `skills/transform_quickstart.py transform` against a multi-chart quickstart.

## Priority 3 — Harden

7. **Unit tests** — Analyzer, validator, generator. At minimum: single-chart, multi-chart, secret dedup, validator fix loop.

8. **Test against remaining quickstarts** — ppe-compliance-monitor, ai-virtual-agent, llm-cpu-serving, lemonade-stand-assistant.
