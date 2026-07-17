# QuickPat

Convert [Red Hat AI Quickstarts](https://github.com/rh-ai-quickstart) into [Validated Patterns](https://validatedpatterns.io/) — production-ready, GitOps-driven OpenShift deployments. Two authoring paths:

- **`quickpat create`** — analyzes an existing Quickstart Helm chart and generates a VP automatically
- **`quickpat compose`** — compiles a declarative `ApplicationSpec` into either a VP or a Quickstart Helm chart

## Theory of Operation

### Path 1 — Chart analysis (`quickpat create`)

1. **Analyze** — parse the upstream Helm chart: extract resource types, detect operator indicators, classify secrets as prompt/generate/skip, identify GPU requirements, flag stale deps and local forks
2. **Generate** — produce the VP directory: `values-prod.yaml` (namespaces, subscriptions, ArgoCD apps), `values-secret.yaml.template` (Vault secret groups), infra charts (DSC, NFD, ClusterPolicy), ExternalSecret templates, platform overrides, Makefile, `pattern.sh`
3. **Validate** — check the generated output against Patternizer conventions; auto-fix common issues up to N iterations

The upstream chart remains the ArgoCD application source (remote strategy). The VP adds the operator lifecycle, secrets framework, and GitOps layer around it.

### Path 2 — Composition spec (`quickpat compose`)

1. **Parse** — load `ApplicationSpec` YAML: 8 block types, custom components, wiring, device modes, upstream overrides
2. **Compile** — collect operators from blocks, resolve DSC component states (auto-injecting `llamastackoperator` when a llama-stack block is present, etc.), detect existing hand-written charts in the application repo, build configuration
3. **Generate VP** — same generator as Path 1, but config-driven from blocks; infra charts (DSC, ClusterPolicy) use block-specific config; platform overrides carry storage provider hints; device overrides (values-gpu.yaml) hold GPU operators when `devices: [cpu, gpu]` is declared
4. **Generate QS** — separate generator produces a self-contained Helm chart: provider-conditional object storage templates (minio/odf/s3), inline Tekton Pipeline + Task for data ingestion, LlamaStack Deployment + Service, model-serving InferenceService + ServingRuntime, NOTES.txt prerequisites, `values.yaml` with Helm references, `scripts/create-secrets.sh`

The application repo (e.g. `github.com/atyronesmith/lemonade-stand`) contains `spec.yaml` + hand-written custom component charts. Both `vp-out/` and `qs-out/` are committed alongside the source. One spec, two deployment targets.

---

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:atyronesmith/quickpat.git
cd quickpat
uv sync
uv run quickpat --help
```

---

## Quick Start

### Create a VP from an existing Quickstart

```bash
quickpat create RAG --non-interactive
```

### Compile an ApplicationSpec (compose path)

```bash
# VP output — writes to vp-out/ next to spec.yaml
quickpat compose my-app/spec.yaml

# QS Helm chart — writes to qs-out/ next to spec.yaml
quickpat compose my-app/spec.yaml --format qs

# Skip RBAC for ODF setup Job (strict RBAC environments)
quickpat compose my-app/spec.yaml --format qs --no-create-service-account
```

See [docs/compose-tutorial.md](docs/compose-tutorial.md) for a complete walkthrough. Reference implementations:
- [lemonade-stand](https://github.com/atyronesmith/lemonade-stand) — TrustyAI guardrails demo, 6 blocks + 5 custom components
- [rag-chatbot](https://github.com/atyronesmith/rag-chatbot) — RAG + LlamaStack agents, 6 blocks + 1 custom component

---

## CLI Reference

```
quickpat [--patterns-dir DIR] <command> [options]
```

### `quickpat create <path>`

Generate a VP from a quickstart source (interactive or `--non-interactive`).

```bash
quickpat create RAG
quickpat create https://github.com/rh-ai-quickstart/RAG
quickpat create /path/to/local/chart --non-interactive
```

### `quickpat compose <spec.yaml>`

Compile an `ApplicationSpec` into a VP or QS Helm chart.

| Option | Description |
|--------|-------------|
| `--format vp\|qs` | Output format: `vp` (Validated Pattern, default) or `qs` (Helm chart) |
| `--output, -o DIR` | Output directory (default: `vp-out/` or `qs-out/` next to spec.yaml) |
| `--name NAME` | Pattern name override |
| `--no-fix` | Skip auto-fix validation pass (VP only) |
| `--create-service-account` / `--no-create-service-account` | Generate RBAC for ODF setup Job (default: true) |

### `quickpat analyze <path>`

Analyze a quickstart — detect operators, dependencies, secrets, features.

### `quickpat validate <path>`

Validate a generated pattern for Patternizer conformance. `--fix` runs auto-repair.

### `quickpat list`

List available quickstarts from the ai-quickstart-pub registry.

---

## ApplicationSpec Format (`quickpat compose`)

A composition spec declares the application in terms of building blocks. The compiler handles operator subscriptions, DSC configuration, infra chart generation, and secrets wiring.

```yaml
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec

metadata:
  name: my-app
  description: My AI application
  tier: sandbox           # sandbox | tested | maintained

  # devices: supported deployment modes — generates per-device override files.
  # GPU operators move to values-gpu.yaml instead of values-prod.yaml.
  devices: [cpu, gpu]

  upstream:
    repo: https://github.com/rh-ai-quickstart/RAG.git
    path: deploy/helm/rag
    branch: main

    # extraValues: written to overrides/<app-name>.yaml, passed to upstream
    # chart via extraValueFiles in the ArgoCD app entry.
    extraValues:
      llm-service:
        secret:
          enabled: false

    # ignoreDifferences: resources ArgoCD should not reconcile.
    ignoreDifferences:
      - group: route.openshift.io
        kind: Route
        jsonPointers: [/spec/host]

blocks:
  platform:
    type: ai-platform-foundation
    config:
      dsc:
        kserve: Managed
        dashboard: Managed
        datasciencepipelines: Managed

  gpu:
    type: gpu-compute
    config:
      mig_strategy: none
      dcgm: true

  llm:
    type: model-serving
    config:
      model: meta-llama/Llama-3.2-3B-Instruct
      runtime: vllm
      gpu: true
      storage:
        type: oci
        uri: oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.2-3b-instruct

  db:
    type: vector-store
    config:
      database: ragdb

  store:
    type: object-storage
    config:
      provider: minio   # minio | odf | s3
      bucket: documents

  ingest:
    type: data-pipeline
    config:
      sources:
        - name: docs
          type: s3
      schedule: manual
      chunk_size: 512
    inputs:
      vector_store: db
      object_storage: store

  llm-server:
    type: llama-stack
    config:
      port: 8321
    inputs:
      llm: llm
      vector_store: db

wiring:
  - from: store
    to: ingest
    via: document-staging
  - from: llm
    to: llm-server
    via: inference-backend

custom:
  my-ui:
    description: Custom chat UI
    source:
      image: quay.io/myorg/my-ui:latest
    replicas: 1
    ports:
      - name: http
        port: 8080
        route: true
        tls: { termination: edge }
```

### Block Types

| Block | Coverage | What it provides |
|---|---|---|
| `ai-platform-foundation` | 85% of QSs | OpenShift AI + Serverless + Service Mesh; DataScienceCluster CR |
| `gpu-compute` | 66% | NFD + NVIDIA GPU Operator; ClusterPolicy CR |
| `model-serving` | 33%+ | KServe ServingRuntime + InferenceService (vLLM or custom runtime) |
| `vector-store` | 47% | pgvector Deployment + Service + credentials |
| `object-storage` | 66% | MinIO / ODF / S3 — provider-conditional templates |
| `data-pipeline` | 9% (growing) | Tekton Pipeline + Task; input resolution from vector_store + object_storage |
| `guardrails-orchestrator` | 9% | TrustyAI GuardrailsOrchestrator CR + ConfigMap |
| `llama-stack` | — | LlamaStack server; auto-injects `llamastackoperator: Managed` into DSC |
| `sso-auth` | 14% | Keycloak CR + Realm |

### Object Storage Providers (QS output)

| File | minio | odf | s3 |
|---|---|---|---|
| PVC + Deployment (MinIO) | ✅ | — | — |
| Bucket-init container | ✅ | — | — |
| ObjectBucketClaim | — | ✅ | — |
| ODF setup Job + RBAC | — | ✅ | — |
| `data-connection` Secret | ✅ always | ✅ (written by Job) | ✅ always |

---

## Generated Pattern Structure

```
my-app/
├── spec.yaml                     ← source of truth (edit this)
├── charts/                       ← hand-written custom component charts
│   └── <component>/
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
├── vp-out/                       ← generated VP (ArgoCD target, committed)
│   ├── values-prod.yaml          ← namespaces, subscriptions, ArgoCD apps
│   ├── values-global.yaml
│   ├── values-secret.yaml.template
│   ├── charts/
│   │   ├── dsc/                  ← DataScienceCluster CR
│   │   ├── nfd/                  ← NodeFeatureDiscovery CR
│   │   ├── nvidia-config/        ← ClusterPolicy CR
│   │   └── <app>-secrets/        ← ExternalSecret templates
│   ├── overrides/
│   │   ├── values-AWS.yaml       ← storage provider hints per platform
│   │   ├── values-gpu.yaml       ← GPU operators (when devices declared)
│   │   ├── values-cpu.yaml
│   │   └── <app-name>.yaml       ← upstream.extraValues
│   ├── Makefile, Makefile-common, pattern.sh, pattern-metadata.yaml
│   └── ansible.cfg, .gitignore, LICENSE
└── qs-out/                       ← generated QS Helm chart (helm install target, committed)
    ├── chart/
    │   ├── Chart.yaml
    │   ├── values.yaml
    │   └── templates/
    │       ├── NOTES.txt
    │       ├── <block>/          ← inline Kubernetes manifests per block
    │       └── <component>/      ← copied from charts/ (custom components)
    ├── scripts/
    │   └── create-secrets.sh
    └── README.md
```

---

## Patternizer Conformance

Generated VP output is validated against [Patternizer](https://github.com/validatedpatterns/patternizer) v1.3.1+ conventions:
- Map-form namespaces (`vault: {}` not `- vault`)
- ESO backtick escaping in ExternalSecret templates
- `TARGET_VARIANT` in `pattern.sh` ansible-playbook env block
- Local chart paths at `charts/<name>/`
- `values-secret.yaml.template` version 2.0 with `vaultPrefixes`

---

## Project Structure

```
quickpat/
├── quickpat/
│   ├── cli.py           — CLI entry point (compose, create, analyze, validate, ...)
│   ├── generator.py     — VP file generator (values-prod, infra charts, overrides, secrets)
│   ├── analyzer.py      — Helm chart parser + operator/secret/feature detection
│   ├── validator.py     — Pattern validation + auto-fix loop
│   ├── pipeline.py      — Orchestration: parse → compile → generate → validate
│   ├── operators.py     — Operator registry + INFRA_CHARTS (DSC, NFD, ClusterPolicy)
│   └── compose/
│       ├── parser.py        — ApplicationSpec + BlockInstance dataclasses
│       ├── blocks.py        — Block type registry (9 types → operators)
│       ├── compiler.py      — Spec → (analysis, config) for PatternGenerator
│       ├── block_templates.py — QS inline manifest generators per block type
│       ├── qs_generator.py  — QS chart directory writer
│       └── renderer.py      — Jinja2 context builder for block template refs
├── tests/               — 439 tests
├── examples/
│   ├── sample-spec.yaml
│   └── lemonade-stand-compose.yaml
└── docs/
    ├── compose-tutorial.md   — Full ApplicationSpec walkthrough
    └── decision-points.md
```

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
