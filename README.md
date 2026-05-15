# QuickPat

Convert [Red Hat AI Quickstarts](https://github.com/rh-ai-quickstart) into [Validated Patterns](https://validatedpatterns.io/) — production-ready, GitOps-driven OpenShift deployments.

QuickPat analyzes Helm charts, detects required operators, secrets, and GPU requirements, then generates a complete Validated Pattern directory that can be deployed with `./pattern.sh make install`.

## CI Status

Every push to `main` generates all 6 shortlisted patterns, validates them, and publishes to `generated/<name>` branches:

| Quickstart | Branch |
|------------|--------|
| RAG | `generated/RAG` |
| maas-code-assistant | `generated/maas-code-assistant` |
| product-recommender | `generated/product-recommender` |
| lemonade-stand | `generated/lemonade-stand` |
| llm-cpu-serving | `generated/llm-cpu-serving` |
| data-governance | `generated/data-governance` |

Each branch is a self-contained Validated Pattern ready for deployment. See [Deploying a Generated Pattern](#deploying-a-generated-pattern).

## What It Does

A typical AI Quickstart ships a Helm chart meant for `helm install`. A Validated Pattern wraps that chart with:

- **Operator lifecycle management** — auto-installs OpenShift AI, GPU operators, Service Mesh, Pipelines, etc.
- **HashiCorp Vault integration** — secrets managed via the VP secrets framework (`values-secret.yaml.template`)
- **Multi-cloud support** — platform-specific overrides for AWS, Azure, GCP, IBM Cloud
- **GitOps via ArgoCD** — declarative, drift-free cluster state
- **Multisource configuration** — infrastructure charts pulled from the upstream VP registry

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:atyronesmith/quickpat.git
cd quickpat
uv sync
```

Run via `uv`:

```bash
uv run quickpat --help
```

Or use the wrapper script:

```bash
./quickpat.sh --help
```

## Quick Start

### List Available Quickstarts

```bash
quickpat list
```

### Analyze a Quickstart

```bash
quickpat analyze /path/to/quickstart
quickpat analyze https://github.com/rh-ai-quickstart/RAG
quickpat analyze RAG   # resolve by registry name
```

### Create a Validated Pattern

Interactive mode (guided questionnaire):

```bash
quickpat create RAG
```

Non-interactive with defaults:

```bash
quickpat create RAG --non-interactive
```

With LLM-enhanced detection:

```bash
quickpat create RAG --llm openai
```

### Create from Spec (No Quickstart Source)

```bash
quickpat new spec.yaml -o /tmp/my-pattern
```

### Deploy

```bash
cd ~/patterns/rag-pattern/
git init && git add -A && git commit -m "Initial pattern"
oc login <cluster>
./pattern.sh make install
```

### Deploying a Generated Pattern

CI publishes pre-generated patterns to `generated/<name>` branches. To deploy one on CRC or any OpenShift cluster:

```bash
git clone -b generated/RAG https://github.com/atyronesmith/quickpat.git /tmp/rag-pattern
cd /tmp/rag-pattern
./scripts/deploy.sh
```

Each generated pattern includes `scripts/` with deploy, undeploy, validate, and status scripts. See `scripts/README.md` in any generated branch for details.

To point an existing Pattern CR at a generated branch:

```bash
oc patch pattern <name> --type=merge \
  -p '{"spec":{"gitSpec":{"targetRepo":"https://github.com/atyronesmith/quickpat.git","targetRevision":"generated/RAG"}}}'
```

## CLI Reference

```
quickpat [--patterns-dir DIR] <command> [options]
```

### Global Options

| Option | Description |
|--------|-------------|
| `--patterns-dir DIR` | Root directory for generated patterns (default: `~/patterns`) |
| `--version` | Show version |

### Commands

#### `quickpat list`

List available AI Quickstarts from the [ai-quickstart-pub](https://github.com/rh-ai-quickstart/ai-quickstart-pub) registry.

#### `quickpat analyze <path>`

Analyze a quickstart — detect operators, dependencies, secrets, features, stale deps, and local forks.

| Option | Description |
|--------|-------------|
| `path` | Path, GitHub URL, or registry name |
| `--output, -o DIR` | Output directory |
| `--name NAME` | Pattern name override |

#### `quickpat create <path>`

Generate a complete Validated Pattern from a quickstart source. In interactive mode, prompts for pattern tier, operator selection, namespace overrides, secret classification, chart strategy, vault, and global options.

| Option | Description |
|--------|-------------|
| `path` | Path, GitHub URL, or registry name |
| `--output, -o DIR` | Output directory |
| `--name NAME` | Pattern name override |
| `--non-interactive` | Use defaults, skip prompts |
| `--llm PROVIDER` | LLM provider for enhanced detection |
| `--model NAME` | Model name override |
| `--llm-url URL` | Base URL for ollama/vllm |

#### `quickpat new <spec.yaml>`

Create a Validated Pattern from a declarative spec YAML — no quickstart source needed. See [Spec YAML Format](#spec-yaml-format).

| Option | Description |
|--------|-------------|
| `spec` | Path to spec YAML file |
| `--output, -o DIR` | Output directory |
| `--name NAME` | Pattern name override |
| `--non-interactive` | Use defaults, skip prompts |

#### `quickpat batch`

Transform all registered quickstarts in bulk.

| Option | Description |
|--------|-------------|
| `--output, -o DIR` | Root output directory |
| `--filter NAME` | Only process quickstarts matching this substring |
| `--keep-going` | Continue on failure instead of stopping |
| `--llm PROVIDER` | LLM provider |

#### `quickpat check-ready <path>`

Check if a quickstart is publication-ready against the [ai-quickstart-pub](https://github.com/rh-ai-quickstart/ai-quickstart-pub) criteria. Checks README, LICENSE, Chart.yaml fields, values.yaml, templates, hardcoded image tags, stale dependencies, local forks, .gitignore, and sensitive files.

| Option | Description |
|--------|-------------|
| `path` | Path, GitHub URL, or registry name |

#### `quickpat validate <path>`

Validate a generated pattern for structural correctness. With `--fix`, runs a self-correcting loop that auto-repairs common issues.

| Option | Description |
|--------|-------------|
| `path` | Path to pattern directory |
| `--fix` | Auto-fix issues |
| `--max-iterations N` | Max auto-fix iterations (default: 3) |
| `--llm PROVIDER` | LLM provider for enhanced validation |

Auto-fixable issues include missing `multiSourceConfig.enabled`, `main:` incorrectly nested under `global:`, missing `clusterGroupChartVersion`, deprecated `vaultPrefixOverride`, wrong `version: "2.0"` in secrets template, legacy Makefile includes, missing executable bit on `pattern.sh`, wrong chart paths, and missing `overrides/` directory.

## Spec YAML Format

The `quickpat new` command accepts a declarative spec file. All fields except `name` are optional.

```yaml
name: my-ai-app
description: Custom AI application pattern
tier: sandbox   # sandbox | tested | maintained

charts:
  - name: my-inference
    path: ./charts/my-inference    # local chart (copied into pattern)
    namespace: ai-inference
    labels:
      opendatahub.io/dashboard: "true"

  - name: my-frontend
    repo: https://charts.example.com  # external chart (referenced by URL)
    version: "1.2.0"
    namespace: frontend

operators:
  - openshift-ai
  - nvidia-gpu

secrets:
  - name: hf_token
    onMissingValue: prompt     # user provides at deploy time
  - name: db_password
    onMissingValue: generate   # auto-generate with vault policy

vault:
  enabled: true

options:
  syncPolicy: Automatic           # Automatic | Manual
  installPlanApproval: Automatic   # Automatic | Manual
  clustergroup_version: "0.9.*"
```

Charts can mix `path:` (local, copied into pattern) and `repo:` (external, referenced by URL).

See `examples/sample-spec.yaml` for a complete reference.

## Interactive Questionnaire

When running `quickpat create` without `--non-interactive`, the guided questionnaire covers:

| Section | Description |
|---------|-------------|
| Pattern name | Name for the generated pattern |
| Pattern tier | `sandbox` / `tested` / `maintained` |
| Operators | Remove detected operators or add undetected ones |
| Namespaces | Override auto-derived namespace assignments (multi-chart only) |
| Secrets | Classify each secret as `prompt` / `generate` / `skip` |
| Chart strategy | Local (copy into pattern) or External (reference by URL) |
| Vault | Enable/disable HashiCorp Vault |
| Global options | Sync policy, install plan approval |
| Output directory | Where to write the pattern |

## Generated Pattern Structure

```
my-pattern/
├── values-global.yaml              # Global config, multisource settings
├── values-hub.yaml                 # Hub cluster: namespaces, operators, apps
├── values-secret.yaml.template     # Vault secrets template (v2.0 format)
├── Makefile
├── Makefile-common
├── pattern.sh
├── pattern-metadata.yaml
├── ansible.cfg
├── .ansible-lint
├── .gitignore
├── charts/
│   └── all/
│       └── <app-name>/             # Local copy of the Helm chart(s)
├── overrides/
│   ├── values-AWS.yaml
│   ├── values-Azure.yaml
│   ├── values-GCP.yaml
│   ├── values-IBMCloud.yaml
│   └── values-None.yaml
├── scripts/
│   ├── crc-setup.sh                # CRC cluster setup (32GB, 12 CPU)
│   ├── deploy.sh                   # Deploy pattern to CRC/OpenShift
│   ├── undeploy.sh                 # Clean removal of pattern
│   ├── validate-deployment.sh      # Post-deploy validation checks
│   ├── status.sh                   # Cluster status report
│   ├── dsc.yaml                    # DataScienceCluster CR for OpenShift AI
│   └── README.md                   # Script documentation
└── docs/
    └── quickstart-analysis.md
```

## Supported Quickstart Layouts

QuickPat auto-detects Chart.yaml in these common conventions:

| Layout | Examples |
|--------|----------|
| `deploy/helm/<name>/` | RAG, ppe-compliance-monitor |
| `deploy/cluster/helm/` | ai-virtual-agent |
| `helm/` | llm-cpu-serving |
| `chart/` | lemonade-stand-assistant |
| Root directory | Any chart at repo root |

Multi-chart quickstarts are fully supported — charts sharing a subdirectory get a shared namespace (e.g. `observability/korrel8r` + `observability/loki` share one namespace).

## Detected Operators

| Operator | Detected Via |
|----------|-------------|
| Red Hat OpenShift AI | `inferenceservice`, `servingruntime`, `vllm`, `datasciencecluster` |
| NVIDIA GPU Operator | `gpu`, `nvidia`, `cuda` |
| Node Feature Discovery | Auto-added as GPU co-dependency |
| OpenShift Pipelines | `pipeline`, `pipelinerun`, `tekton` |
| OpenShift Service Mesh | `servicemesh`, `istio` (auto-added with OpenShift AI) |
| OpenShift Serverless | `knativeserving`, `knative` (auto-added with OpenShift AI) |
| AMQ Streams (Kafka) | `kafka`, `kafkatopic` |

Co-dependencies are resolved transitively — enabling GPU automatically adds NFD, enabling OpenShift AI adds Service Mesh and Serverless.

## LLM Providers

QuickPat optionally uses LLMs for enhanced operator detection, secret review, and validation. Pass `--llm <provider>` to `create`, `batch`, or `validate`.

| Provider | Flag | Config Required |
|----------|------|-----------------|
| OpenAI | `--llm openai` | `OPENAI_API_KEY` env var |
| Anthropic | `--llm anthropic` | `ANTHROPIC_API_KEY` env var |
| Ollama | `--llm ollama` | Local at `localhost:11434` (no key) |
| vLLM | `--llm vllm` | `--llm-url` for custom endpoint |
| DeepInfra | `--llm deepinfra` | `DEEPINFRA_API_KEY` env var |

Override the model with `--model <name>` and the endpoint with `--llm-url <url>`.

All LLM features are optional — QuickPat works fully in deterministic mode without any LLM.

## Configuration

QuickPat loads settings from `quickpat.yaml` (project root) or `~/.config/quickpat/config.yaml`. Environment variables override config file values.

```yaml
llm:
  provider: none
  openai:
    model: gpt-4o-mini
  anthropic:
    model: claude-sonnet-4-20250514
  ollama:
    model: llama3.1
    base_url: http://localhost:11434

pattern:
  output_dir: ~/patterns
  chart_strategy: local
  clustergroup_version: "0.9.*"

registry:
  quickstart_url: https://raw.githubusercontent.com/rh-ai-quickstart/ai-quickstart-pub/main/.gitmodules
  chart_repo_index_url: https://rh-ai-quickstart.github.io/ai-architecture-charts/index.yaml

platforms:
  - AWS
  - Azure
  - GCP
  - IBMCloud
  - None
```

See `quickpat.yaml.sample` for a complete reference.

## Text Skill

Copy `skills/transform_quickstart.md` into any LLM's system prompt (ChatGPT, Claude, Gemini, local models) for interactive guided transformation.

## Project Structure

```
quickpat/
├── .github/
│   └── workflows/
│       └── generate-patterns.yml   # CI: generate, validate, publish
├── quickpat/
│   ├── cli.py          # CLI entry point (7 subcommands)
│   ├── analyzer.py     # Helm chart parser, operator/secret/feature detection
│   ├── generator.py    # Pattern file generator + markdown report
│   ├── validator.py    # Pattern validation + auto-fix loop
│   ├── pipeline.py     # Orchestration: analyze -> detect -> generate -> validate
│   ├── spec.py         # Spec YAML loader for `quickpat new`
│   ├── readiness.py    # Publication readiness checks
│   ├── operators.py    # Operator registry with detection indicators
│   ├── registry.py     # ai-quickstart-pub registry + shared chart index
│   ├── providers/      # LLM provider classes (5 providers)
│   ├── profile.py      # Deployment profile presets
│   ├── subchart.py     # Sub-chart dependency inspector
│   ├── transformer.py  # Chart-to-pattern transformer
│   └── config.py       # Config loader (YAML + defaults)
├── skills/
│   └── transform_quickstart.md   # Text skill for any LLM
├── tests/              # 348 tests
├── examples/
│   └── sample-spec.yaml
├── docs/
│   ├── plan.md
│   ├── pub-integration-plan.md
│   └── shared-charts-analysis.md
├── pyproject.toml
├── quickpat.sh         # Wrapper: uv run quickpat
└── uv.lock
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
