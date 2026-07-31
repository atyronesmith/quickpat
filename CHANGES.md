# Changelog

All notable changes to QuickPat are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- `quickpat init-ci spec.yaml [--output-dir DIR] [--force]` — installs the
  hardened CI workflow into any spec repo. Reads the pattern name from spec.yaml,
  substitutes it into the template, and writes `.github/workflows/compose.yml`.
- Hardened CI workflow template (`quickpat/templates/ci/compose.yml`):
  - `quickpat validate-spec --strict` before compose — spec errors abort the run
  - VP drift detection — fail if `vp-out/` doesn't match what compose produces
    (`pattern-metadata.yaml` excluded; it contains a daily generation date)
  - QS drift detection — same for `qs-out/`
  - Helm lint + kubeconform on `qs-out/chart/` (previously not validated)
  - Image tag check — warns on `:latest` tags in generated charts (non-blocking)
  - Doc link checker — validates URLs in `docs/**/*.md` (non-blocking)
  - PR comment table now covers both VP and QS lint/kubeconform results
- QS generator now merges each copied custom chart's `values.yaml` into the QS
  chart's top-level `values.yaml`. Fixes helm lint nil-pointer errors when
  templates reference `.Values.secretStoreRef` / `.Values.vaultPrefix` /
  `.Values.refreshInterval` that were only defined in the source chart.
- `quickpat validate-spec spec.yaml` — new CLI subcommand that validates a spec
  file semantically before composing. 15 checks across 7 categories:
  - Wiring `from:` / `to:` reference existing blocks (error)
  - `{{ blocks.X.* }}` template expressions in inputs, config, and custom env
    reference existing blocks (error)
  - Wiring `via:` is non-empty (warning)
  - `custom.*.source.chart` paths exist on disk (warning)
  - Top-level secrets have at least one field (warning)
  - `vault_path` last segment matches secret name by convention (warning)
  - Duplicate secret names in `secrets:` (error)
  - Doc `source` files exist on disk (warning)
  - Unclosed or stray `<!-- vp-only -->` / `<!-- qs-only -->` / `<!-- end -->`
    markers in doc source files (error)
  - Unknown block type with "did you mean?" suggestion using fuzzy matching (warning)
  - `vm-workspace` block present without `openshift-virtualization` (error)
  - `keycloak-oidc` + `vm-workspace` both present but not connected in wiring (warning)
  - Block-level secrets conflict with `pattern-secrets` custom chart (warning)
  - `vault: enabled: true` with no secrets declared anywhere (warning)
  - `--json` flag for machine-readable output; `--strict` to treat warnings as errors
- `compose_from_spec` now runs `validate_spec` before generation — error-severity
  issues abort compose; warnings are printed but do not block output
- QS generator skips `deploy: manual` components entirely — they are build-time
  only and should not appear in the QS Helm chart.
- `CustomComponent` gains `source_chart` field (parsed from `source.chart`)
  so the chart-path existence check has access to the declared path
- 43 tests in `tests/test_spec_validator.py` covering every check and every
  branch, including all negative/valid-case paths

- `quickpat compose spec.yaml --target rhoai=3.5` — new `--target PLATFORM=VERSION`
  flag pins all platform-specific output to a specific release:
  - Operator subscription channel (e.g. `fast` → `stable-3.5`)
  - `installPlanApproval: Manual` for pinned versions
  - Version-required co-dependencies injected automatically
    (RHOAI 3.x adds `cert-manager` and `jobset` subscriptions)
  - Validated at parse time — unknown platforms/versions fail with helpful errors
- `target:` field in `spec.yaml` for repo-level version pinning
  (CLI `--target` overrides spec `target:` when both are set)
- Version registry (`quickpat/compose/version_registry.py`) with data for
  RHOAI 2.25, 3.0, 3.4, and 3.5 — channels, DSC defaults, co-deps, OCP minimums
- Breaking-changes registry (`UPGRADE_BREAKING_CHANGES`) documents the
  2.25→3.0 blocking migration requirement and 3.3→3.4 mlflowoperator change
  (used by upgrade runbook generation — Item 2b, coming next)
- `cert-manager` and `jobset` added to the operator registry

### Fixed
- `vp-out/.gitignore` pattern `values-secret*` was too broad — excluded
  `values-secret.yaml.template` (safe to commit) in addition to
  `values-secret.yaml` (real secrets, must stay ignored). Changed to
  `values-secret.yaml` + `!values-secret.yaml.template`. Fixes CI drift
  check failures where fresh compose generated the template but git ignored it.
- QS chart stub directories used `.gitkeep` which helm lint rejects as an
  invalid template extension. Changed to `NOTES.txt`.
- `deploy: manual` components were included in QS chart template stubs —
  they are now skipped entirely in `_write_custom_components`.

---

## [0.2.0] — 2026-07-31

A major release centered on the **compose pipeline**: a spec-driven approach
where a single `spec.yaml` file is the source of truth for both a Validated
Pattern (VP) and a Quickstart (QS) Helm chart. One spec, two outputs.

### Added

**Compose pipeline**
- `quickpat compose spec.yaml` — compiles an `ApplicationSpec` YAML into a
  complete Validated Pattern directory (`vp-out/`) with all required files:
  `values-global.yaml`, `values-prod.yaml`, `Makefile`, `pattern.sh`, charts,
  secret templates, and overrides.
- `quickpat compose spec.yaml --format qs` — compiles the same spec into a
  self-contained Quickstart Helm chart (`qs-out/`) for direct `helm install`.
- `ApplicationSpec` format (`apiVersion: supplychain/v1alpha1 / kind: ApplicationSpec`)
  with sections for `blocks`, `secrets`, `wiring`, `custom`, `docs`, and `vault`.
- `upstream:` field to reference an existing QS Helm chart repo as the main
  application chart; empty (`upstream: {}`) for all-custom-chart patterns.

**Spec blocks**
- Three new block types for VM-isolation patterns:
  - `openshift-virtualization` — installs `kubevirt-hyperconverged`, generates
    `HyperConverged` CR chart; adds `openshift-cnv` namespace with OperatorGroup.
  - `keycloak-oidc` — installs `rhbk-operator` (channel `stable-v26`), adds
    `openshell-agents` namespace with OperatorGroup. Keycloak CR is pattern-specific
    and supplied via a custom chart rather than an auto-generated stub.
  - `vm-workspace` — documents per-user KubeVirt VM sandbox architecture; no
    additional operators (depends on `openshift-virtualization`).
- `llama-stack` block type — enables `llamastackoperator` in the DSC.
- `data-pipeline` block type — installs OpenShift Pipelines, generates Tekton
  Pipeline + Task + RBAC templates in QS output.

**Custom components**
- `deploy: argocd` (default) or `deploy: manual` on custom components. Manual
  components are kept in the repo for reference but excluded from the ArgoCD
  `applications:` list — useful for one-time build steps like bootc image builds.
- `namespace:` per custom component — each ArgoCD app gets its correct namespace
  instead of a global default.
- `extraValueFiles:` per custom component — passed through to the ArgoCD app
  entry in `values-prod.yaml`.

**Secrets**
- Top-level `secrets:` list in the spec generates `values-secret.yaml.template`
  in VP v2 format (`backingStore: vault`, `value: ''`) matching the convention
  used by all current Red Hat Validated Patterns.
- `vault: {enabled: true}` flag wires up Vault + ESO infrastructure even when
  no block-level secrets are declared.
- `onMissingValue: prompt | skip | generate` validated on parse.

**Documentation pipeline**
- `docs:` section in spec references source Markdown files that are compiled
  into both `vp-out/` and `qs-out/` READMEs.
- `<!-- vp-only -->` / `<!-- qs-only -->` / `<!-- end -->` HTML comment markers
  — invisible in GitHub renders, stripped from output. Sections without markers
  appear in both outputs unchanged. Marker lines themselves are always stripped.
- `deploy: both | vp | qs` at the file level for whole-file routing.

**Incremental output**
- `quickpat compose` now generates to a temp directory and syncs only changed
  files to `vp-out/` or `qs-out/`. Files absent from the new output are deleted
  from the destination (removes stale charts when blocks are removed from spec).
- CLI shows "N changed, M unchanged" instead of a static file count.
  A documentation-only edit touches exactly 1 file.

**Operator registry**
- `openshift-virtualization` operator entry (`kubevirt-hyperconverged`,
  channel `stable`, source `redhat-operators`).
- `rhbk` operator entry (Red Hat Build of Keycloak, channel `stable-v26`).
- Operator channels now emitted in `values-prod.yaml` subscriptions (were
  previously omitted for non-ESO operators).
- INFRA_CHART for `openshift-cnv` (HyperConverged CR).
- INFRA_CHART suppressed when a custom component with the same `chart_name`
  exists — prevents duplicate ArgoCD app entries.

**Spec compose tutorial and tooling**
- `docs/compose-tutorial.md` — full walkthrough of the compose spec format
  using the lemonade-stand pattern as a worked example.
- `docs/compose-spec-tutorial-cheatsheet.md` — quick reference card.
- `docs/adding-block-types.md` — step-by-step guide for registering new block
  types in `blocks.py` and `operators.py`, with the SAW blocks as an example.
- `examples/lemonade-stand-compose.yaml` — complete real-world spec example.
- Claude skill (`skills/compose-spec/`) for interactive spec scaffolding.

**TransformResult**
- `files_unchanged: list` field — tracks files skipped because content was
  identical; exposed in the result and printed by the CLI.

### Fixed

- `upstream: null` (bare YAML null) no longer crashes with `AttributeError`
  — added `or {}` guard.
- `extraValueFiles: null` and other nullable custom component fields no longer
  crash with `TypeError` on iteration — `or []` / `or {}` guards applied.
- Empty `source:` or `target:` in `docs:` entries now raises a clear
  `AppSpecError` instead of crashing with `IsADirectoryError` inside generation.
- `onMissingValue` in top-level secrets validated against `{prompt, skip, generate}`.
- VP v2 secret template fields now use `value: ''` (empty string) rather than
  `value: null` — `null` is not a valid VP secret loader form.
- INFRA_CHART auto-generated app suppressed when a custom component with the
  same `chart_name` is declared — previously one silently overwrote the other.
- `_get_app_charts()` multi-chart path now skips remote-strategy charts when
  `git_repo_url` is empty, preventing orphan namespace entries in `values-prod.yaml`
  with no corresponding ArgoCD application.
- `_sync_dir()` now deletes files in `dst` absent from `src` — stale charts and
  blocks from prior runs no longer persist and cause ArgoCD to deploy removed apps.
- `docs/quickstart-analysis.md` operator table is now sorted deterministically
  (Python set iteration was producing different row order between runs).
- QS CLI output now shows "N changed, M unchanged" consistently with VP output.
- `import warnings` moved from inside a loop body to module level in `doc_filter.py`.
- `hasattr()` guards on `CustomComponent` fields replaced with direct attribute
  access (all fields are always present on the dataclass).

### Changed

- `_build_subscriptions()` now reads `channel` from the `OPERATORS` dict and
  emits it into `values-prod.yaml` for all operators (previously only ESO
  received a channel entry).
- Custom component namespaces now use the per-component `namespace:` field
  rather than the global `app_namespace` (pattern name) for all ArgoCD app
  entries.
- `rhbk` operator namespace changed from `rhbk-operator` to `openshell-agents`
  to match the hand-written secure-agent-workspace VP convention.
- `compose_from_spec` and `compose_qs_from_spec` generate to a temp directory
  and sync to the real output — internal behavior change, no API change.

---

## [0.1.0] — 2026-04-01

Initial release.

### Added
- `quickpat create <name>` — analyzes an existing AI Quickstart Helm chart and
  generates a Validated Pattern directory with operator subscriptions, secrets
  management (Vault + ESO), ArgoCD app wiring, and pattern metadata.
- `quickpat analyze <path>` — inspects a Helm chart and reports detected
  operators, secrets, LLM services, and chart strategy.
- `quickpat validate <path>` — validates a generated VP directory against
  Patternizer conventions: file structure, values format, ESO escaping,
  namespace format, legacy artifacts.
- `quickpat list` — lists available quickstarts from the pattern registry.
- `quickpat update <path>` — updates an existing VP with changes from the
  upstream QS chart (diff-based, preserves manual edits).
- `quickpat check-ready <path>` — checks cluster readiness before deploying
  a pattern (operator availability, resource requirements).
- Multi-chart quickstart support — quickstarts with multiple Helm sub-charts
  are detected and wired as separate ArgoCD applications.
- Block type library: `ai-platform-foundation`, `gpu-compute`, `model-serving`,
  `object-storage`, `guardrails-orchestrator`, `vector-store`, `sso-auth`.
- Operator registry with OLM subscription data for: RHOAI, Serverless,
  Service Mesh, NVIDIA GPU Operator, NFD, OpenShift Pipelines, AMQ Streams.
- Config file support (`quickpat.yaml`) for project-level defaults.
- Evaluation test harness for quickstart × LLM provider matrix.
- LLM adapter support: Anthropic, OpenAI, DeepInfra (for LLM-assisted analysis).
