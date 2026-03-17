# ai-quickstart-pub Integration Plan

## What It Is

`ai-quickstart-pub` is a central registry of published AI Quickstarts. Each quickstart is a git submodule under `quickstart/`. It includes a publication readiness checklist and review workflow.

## Current Registered Quickstarts (11)

| Quickstart | Repo |
|---|---|
| RAG | rh-ai-quickstart/RAG |
| llm-cpu-serving | rh-ai-quickstart/llm-cpu-serving |
| ai-virtual-agent | rh-ai-quickstart/ai-virtual-agent |
| openshift-ai-observability-summarizer | rh-ai-quickstart/openshift-ai-observability-summarizer |
| lls-observability | rh-ai-quickstart/lls-observability |
| guardrailing-llms | rh-ai-quickstart/guardrailing-llms |
| product-recommender-system | rh-ai-quickstart/product-recommender-system |
| it-self-service-agent | rh-ai-quickstart/it-self-service-agent |
| ansible-log-analysis | rh-ai-quickstart/ansible-log-analysis |
| maas-code-assistant | rh-ai-quickstart/maas-code-assistant |
| f5-api-security | rh-ai-quickstart/f5-api-security |

## Potential QuickPat Capabilities

### 1. `quickpat list` — List available quickstarts
Parse `.gitmodules` from the pub repo to show all published quickstarts with their URLs. No local clone needed — fetch via GitHub API or raw file. Lets users discover what's available before running `quickpat create`.

### 2. `quickpat create <name>` — Create by registry name
Instead of requiring a path or full URL, allow `quickpat create RAG` or `quickpat create ai-virtual-agent`. QuickPat resolves the name against the pub registry, clones, and transforms. Lowers the barrier to entry.

### 3. `quickpat batch` — Bulk transform all registered quickstarts
Iterate over the registry and generate patterns for all of them. Useful for testing QuickPat itself and for producing a catalog of ready-made patterns.

### 4. Publication readiness check
The pub repo has a readiness checklist. QuickPat could add a `quickpat check-ready` command that validates a quickstart against some of those criteria programmatically (README exists, chart runs, no broken deps).

## Status

- **1. `quickpat list`** — DONE. Fetches `.gitmodules` from GitHub, parses entries, displays names + URLs.
- **2. `quickpat create <name>`** — DONE. Resolves name via registry (exact, case-insensitive, substring), clones, transforms. Error on ambiguous/unknown names.
- **3. `quickpat batch`** — DONE. Transforms all registered quickstarts with summary table.
- **4. Publication readiness check** — DONE. `quickpat check-ready` validates quickstart repos against pub criteria.
