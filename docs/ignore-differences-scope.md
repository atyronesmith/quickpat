# Scope: LLM-Assisted ignoreDifferences Generation

## Problem

ArgoCD sync-loops when controller-managed resources have fields mutated after creation.
Validated Patterns solve this with `ignoreDifferences` in the application config, but
determining which resources need it and which jsonPointers to exclude requires
operator-specific knowledge.

quickpat doesn't generate `ignoreDifferences` today. This is one of the three key gaps
between quickpat output and a deployable VP.

## Goal

Given a quickstart's Helm templates, automatically produce the `ignoreDifferences`
block for each application in `values-prod.yaml`.

## Approach

Two-layer strategy: deterministic rules for known CRDs, LLM for unknown ones.

### Layer 1: Static Rule Table

A lookup table mapping `(apiGroup, kind)` → known `jsonPointers` to ignore. Populated
from existing VP implementations and general OpenShift/ArgoCD knowledge.

```python
KNOWN_IGNORE_RULES = {
    ("route.openshift.io", "Route"): [
        "/spec/host",
        "/spec/alternateBackends",
    ],
    ("kubeflow.org", "Notebook"): [
        "/spec",
        "/metadata/annotations",
        "/metadata/labels",
    ],
    ("datasciencepipelinesapplications.opendatahub.io", "DataSciencePipelinesApplication"): [
        "/spec",
    ],
    ("serving.kserve.io", "InferenceService"): [
        "/metadata/annotations",
        "/metadata/labels",
        "/status",
    ],
    ("serving.knative.dev", "Service"): [
        "/metadata/annotations",
        "/metadata/labels",
    ],
}
```

This handles the common cases without any LLM call. Build it by surveying
existing VP implementations and ArgoCD community docs on common ignore patterns.

### Layer 2: LLM Prompt for Unknown CRDs

When templates contain CRDs not in the static table, ask the LLM to predict which
fields will be controller-mutated.

#### Input to LLM

The LLM needs:
1. **The CRD kind and apiGroup** — e.g., `kind: DataScienceCluster, apiGroup: datasciencecluster.opendatahub.io`
2. **The template YAML** — the actual resource spec from the Helm template
3. **Context about the operator** — what the operator does (can derive from the subscription name and the quickstart's detected features)

#### LLM Prompt Design

```
System: You are an OpenShift/Kubernetes expert. ArgoCD manages resources declaratively
but some resource fields are mutated by their controllers after creation, causing
ArgoCD to detect false drift and enter sync loops.

Given a Kubernetes resource template, predict which fields will be mutated by the
resource's controller and should be added to ArgoCD's ignoreDifferences configuration.

Respond with structured output.

User: The following resource is deployed via ArgoCD in a Validated Pattern.
Predict which fields the controller will mutate after creation.

apiGroup: {group}
kind: {kind}
Operator: {operator_name}

Template:
```yaml
{template_content}
```
```

#### Response Schema

```json
{
  "type": "object",
  "properties": {
    "needs_ignore": { "type": "boolean" },
    "json_pointers": {
      "type": "array",
      "items": { "type": "string" }
    },
    "reason": { "type": "string" }
  }
}
```

#### When NOT to call the LLM

- Standard K8s resources (Deployment, Service, ConfigMap, Secret) — ArgoCD handles
  these fine, no ignoreDifferences needed
- Resources already in the static table
- Templates that are just data (ConfigMaps, Secrets)

### Implementation Steps

#### Step 1: Template CRD Extraction

Parse Helm templates to extract `(apiVersion, kind)` pairs. We already do template
scanning in `analyzer.py` — extend it to collect CRD tuples.

```python
def extract_crds(template_dir: Path) -> list[dict]:
    """Extract apiVersion/kind from all templates."""
    crds = []
    for f in template_dir.glob("*.yaml"):
        for doc in yaml.safe_load_all(f.read_text()):
            if doc and "apiVersion" in doc and "kind" in doc:
                crds.append({
                    "apiVersion": doc["apiVersion"],
                    "kind": doc["kind"],
                    "group": doc["apiVersion"].split("/")[0] if "/" in doc["apiVersion"] else "",
                    "file": f.name,
                    "spec": doc,
                })
    return crds
```

**Complication:** Helm templates contain Go template syntax (`{{ .Values.x }}`),
so they're not valid YAML. Options:
- Strip template expressions before parsing (replace `{{ ... }}` with placeholder strings)
- Use regex extraction for apiVersion/kind (simpler, less context for LLM)
- Render templates with `helm template` if available (most accurate, requires helm CLI)

Recommend: regex extraction for the CRD tuples (simple, reliable), pass raw template
text to LLM (it can handle Go template syntax in context).

#### Step 2: Static Rule Matching

```python
def match_static_rules(crds: list[dict]) -> dict[str, list]:
    """Match extracted CRDs against known ignore rules."""
    result = {}
    for crd in crds:
        key = (crd["group"], crd["kind"])
        if key in KNOWN_IGNORE_RULES:
            result[crd["file"]] = {
                "group": crd["group"],
                "kind": crd["kind"],
                "jsonPointers": KNOWN_IGNORE_RULES[key],
            }
    return result
```

#### Step 3: LLM Classification

For CRDs not in the static table:
- Filter out standard K8s kinds (Deployment, Service, ConfigMap, Secret, etc.)
- Batch remaining CRDs into a single LLM call (or one per unique kind)
- Parse response and merge with static results

#### Step 4: Integration into Generator

In `generator.py`, when writing the application entry in `values-prod.yaml`, append
`ignoreDifferences` if the app's chart templates contain CRDs that need it.

```yaml
applications:
  rag:
    name: rag
    namespace: rag
    repoURL: https://github.com/rh-ai-quickstart/RAG.git
    path: deploy/helm/rag
    chartVersion: main
    ignoreDifferences:
      - group: route.openshift.io
        kind: Route
        jsonPointers:
          - /spec/host
          - /spec/alternateBackends
```

## Files to Modify/Create

| File | Action | Description |
|------|--------|-------------|
| `quickpat/argocd.py` | **New** | CRD extraction, static rules, LLM prompt |
| `quickpat/generator.py` | Modify | Emit ignoreDifferences in app entries |
| `quickpat/pipeline.py` | Modify | Call argocd analysis, pass results to generator |
| `quickpat/analyzer.py` | Modify | Add template CRD extraction to analysis output |
| `tests/test_argocd.py` | **New** | Unit tests for static rules and LLM parsing |

## Complexity Estimate

- Static rules + CRD extraction: ~1 day
- LLM prompt + structured output parsing: ~0.5 day
- Generator integration: ~0.5 day
- Testing: ~0.5 day
- **Total: ~2.5 days**

## Open Questions

1. Should we also handle sub-chart templates? The RAG chart has dependencies
   (pgvector, llm-service, etc.) whose templates also create CRDs. This requires
   either `helm dependency build` to fetch sub-charts, or fetching from the chart
   repo index (which quickpat already has).

2. Should the static rule table be a config file (editable by users) or hardcoded?
   Config file is more flexible but adds complexity.

3. The LLM prediction quality will vary — should we add a confidence threshold
   and only emit high-confidence predictions, leaving the rest as comments?
