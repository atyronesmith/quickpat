# QuickPat

Convert [Red Hat AI Quickstarts](https://github.com/rh-ai-quickstart) into [Validated Patterns](https://validatedpatterns.io/) — production-ready, GitOps-driven OpenShift deployments.

QuickPat analyzes an AI Quickstart's Helm chart, detects required operators, secrets, and GPU requirements, then generates a complete Validated Pattern directory that can be deployed with `./pattern.sh make install`.

## What It Does

A typical AI Quickstart ships a Helm chart meant for `helm install`. A Validated Pattern wraps that chart with:

- **Operator lifecycle management** — auto-installs OpenShift AI, GPU operators, Service Mesh, Pipelines, etc.
- **HashiCorp Vault integration** — secrets managed via the VP secrets framework (`values-secret.yaml.template`)
- **Multi-cloud support** — platform-specific overrides for AWS, Azure, GCP, IBM Cloud
- **GitOps via ArgoCD** — declarative, drift-free cluster state
- **Multisource configuration** — infrastructure charts pulled from the upstream VP registry (no fork of multicloud-gitops required)

QuickPat automates the conversion so you don't have to manually create a dozen boilerplate files and figure out the correct YAML structure.

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

### Analyze a Quickstart

Inspect a quickstart to see what QuickPat detects — operators, dependencies, secrets, GPU requirements:

```bash
quickpat analyze /path/to/quickstart

# Or directly from GitHub
quickpat analyze https://github.com/rh-ai-quickstart/RAG
```

### Create a Validated Pattern

Generate all pattern files interactively:

```bash
quickpat create /path/to/quickstart
```

Or non-interactively with defaults:

```bash
quickpat create https://github.com/rh-ai-quickstart/RAG --non-interactive
```

### Deploy

```bash
cd ~/patterns/rag-pattern/
git init && git add -A && git commit -m "Initial pattern"
oc login <cluster>
./pattern.sh make install
```

## CLI Reference

```
quickpat [--patterns-dir DIR] <command> [options]

Commands:
  analyze    Analyze an AI Quickstart (detect operators, secrets, features)
  create     Generate a complete Validated Pattern from a Quickstart

Global Options:
  --patterns-dir DIR   Root directory for generated patterns (default: ~/patterns)
  --version            Show version

analyze options:
  path                 Path or GitHub URL to AI Quickstart
  --output, -o DIR     Output directory
  --name NAME          Pattern name override

create options:
  path                 Path or GitHub URL to AI Quickstart
  --output, -o DIR     Output directory
  --name NAME          Pattern name override
  --non-interactive    Use defaults, skip prompts
```

## Generated Pattern Structure

```
my-pattern/
├── values-global.yaml              # Global config, multisource settings
├── values-hub.yaml                 # Hub cluster: namespaces, operators, apps
├── values-secret.yaml.template     # Vault secrets template (v2.0 format)
├── Makefile                        # Includes Makefile-common
├── Makefile-common                 # Standard VP make targets (install, show, etc.)
├── pattern.sh                      # Utility container runner
├── pattern-metadata.yaml           # Pattern registry metadata
├── ansible.cfg                     # Ansible configuration
├── .ansible-lint
├── .gitignore
├── charts/
│   └── all/
│       └── <app-name>/             # Local copy of the Helm chart
├── overrides/
│   ├── values-AWS.yaml             # Platform-specific overrides
│   ├── values-Azure.yaml
│   ├── values-GCP.yaml
│   ├── values-IBMCloud.yaml
│   └── values-None.yaml
└── docs/
    └── quickstart-analysis.md      # Analysis report
```

## Supported Quickstart Layouts

QuickPat auto-detects Chart.yaml in these common AI Quickstart conventions:

| Layout | Examples |
|--------|----------|
| `deploy/helm/<name>/` | RAG, ppe-compliance-monitor |
| `deploy/cluster/helm/` | ai-virtual-agent |
| `helm/` | llm-cpu-serving |
| `chart/` | lemonade-stand-assistant |
| Root directory | Any chart at repo root |

## Detected Operators

QuickPat scans chart templates and values for indicators of required OpenShift operators:

| Operator | Detected Via |
|----------|-------------|
| Red Hat OpenShift AI | `inferenceservice`, `servingruntime`, `vllm`, `datasciencecluster` |
| NVIDIA GPU Operator | `gpu`, `nvidia`, `cuda` |
| Node Feature Discovery | Auto-added as GPU co-dependency |
| OpenShift Pipelines | `pipeline`, `pipelinerun`, `tekton` |
| OpenShift Service Mesh | `servicemesh`, `istio` (auto-added with OpenShift AI) |
| OpenShift Serverless | `knativeserving`, `knative` (auto-added with OpenShift AI) |
| AMQ Streams (Kafka) | `kafka`, `kafkatopic` |

Co-dependencies are resolved transitively — enabling GPU automatically adds NFD, enabling OpenShift AI automatically adds Service Mesh and Serverless.

## Model-Agnostic Skills

The `skills/` directory provides the same transformation logic as reusable, model-agnostic skills that work with any LLM or in pure deterministic mode.

### Sub-Skills

| Skill | Description |
|-------|-------------|
| `analyze` | Parse quickstart Helm chart |
| `detect` | Identify operators and review secrets (optional LLM) |
| `transform` | Generate pattern files |
| `validate` | Check correctness and auto-fix |

### Deterministic (No LLM)

```bash
# Full pipeline
python skills/transform_quickstart.py transform /path/to/quickstart

# Individual sub-skills
python skills/transform_quickstart.py analyze /path/to/quickstart
python skills/transform_quickstart.py detect /path/to/quickstart
python skills/transform_quickstart.py validate /path/to/pattern
```

### With LLM Enhancement

When an LLM is provided, it enhances operator detection for unusual charts and reviews secrets for false positives. A self-correcting validation loop catches and repairs issues iteratively.

```bash
# With OpenAI
python skills/transform_quickstart.py transform /path/to/quickstart --llm openai

# With Anthropic
python skills/transform_quickstart.py transform /path/to/quickstart --llm anthropic

# With local Ollama
python skills/transform_quickstart.py transform /path/to/quickstart --llm ollama --model mistral
```

### Python API

```python
from skills.transform_quickstart import transform

# Deterministic
result = transform("/path/to/quickstart")

# With any LLM — just pass a callable(system: str, user: str) -> str
from skills.transform_quickstart import make_ollama_llm
result = transform("/path/to/quickstart", llm=make_ollama_llm())
```

Built-in LLM adapters: `make_openai_llm()`, `make_anthropic_llm()`, `make_ollama_llm()`. Any `callable(system_prompt, user_message) -> str` works.

### Text Skill

Copy `skills/transform_quickstart.md` into any LLM's system prompt (ChatGPT, Claude, Gemini, local models) for interactive guided transformation.

### Self-Correcting Validation

The `validate --fix` command runs a loop that detects and auto-repairs common issues:

```bash
python skills/transform_quickstart.py validate /path/to/pattern --fix
```

Auto-fixable issues include:
- `main:` incorrectly nested under `global:` in values-global.yaml
- `multiSourceConfig.enabled` not set to `true`
- Missing `clusterGroupChartVersion`
- Deprecated `vaultPrefixOverride` (converted to `vaultPrefixes`)
- Missing or wrong `version: "2.0"` in values-secret.yaml.template
- Legacy `include common/Makefile` (fixed to `include Makefile-common`)
- `pattern.sh` missing executable bit
- Missing `overrides/` directory or platform files
- Wrong chart paths (`charts/hub/` corrected to `charts/all/`)
- Infrastructure apps using `path:` instead of `chart:` + `chartVersion:`
- Missing `sharedValueFiles` in values-hub.yaml

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required for `--llm openai` |
| `ANTHROPIC_API_KEY` | Required for `--llm anthropic` |
| Ollama | No API key needed (local at `localhost:11434`) |

## Project Structure

```
quickpat/
├── quickpat/
│   ├── cli.py          # CLI entry point (analyze, create)
│   ├── analyzer.py     # Helm chart parser, operator/secret/feature detection
│   ├── generator.py    # Pattern file generator + markdown report
│   └── operators.py    # Operator registry with detection indicators
├── skills/
│   ├── transform_quickstart.py   # Chainable sub-skills + LLM adapters
│   ├── transform_quickstart.md   # Text skill for any LLM
│   ├── skill_validate.py         # Validation checks + auto-fix loop
│   └── README.md                 # Skills usage guide
├── pyproject.toml
├── quickpat.sh                   # Wrapper: uv run quickpat
└── uv.lock
```

## License

TBD
