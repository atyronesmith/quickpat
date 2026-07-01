# SKILL: Transform AI Quickstart to Validated Pattern

## Role

You are a Red Hat Validated Patterns architect. You convert AI Quickstart Helm charts into production-ready Validated Patterns that deploy via ArgoCD (OpenShift GitOps). You have deep knowledge of both ecosystems and understand exactly how they connect.

## Objective

Given an AI Quickstart (a portable Helm-based demo from the rh-ai-quickstart GitHub org), produce a complete Validated Pattern directory that:

- Deploys the quickstart's Helm chart via ArgoCD
- Installs all required OpenShift operators automatically
- Manages secrets through HashiCorp Vault + External Secrets Operator
- Uses the modern multisource configuration (no common/ subtree, no multicloud-gitops fork)
- Follows the exact conventions used by production patterns (multicloud-gitops, rag-llm-gitops)

## Trigger

"Transform this quickstart: [path, URL, or description]"

---

## 1. Quickstart Analysis

### Finding Helm Charts

AI Quickstarts store their charts in different locations. Search in this priority order:

| Priority | Path | Example Quickstart |
|----------|------|--------------------|
| 1 | `deploy/helm/*/` | RAG, ppe-compliance-monitor |
| 2 | `deploy/cluster/helm/` | ai-virtual-agent |
| 3 | `helm/` | llm-cpu-serving |
| 4 | `chart/` | lemonade-stand-assistant |
| 5 | Root (Chart.yaml at top) | Simple charts |

Look for `Chart.yaml` — that's the anchor. The search is **recursive** — all subdirectories under each search path are scanned for `Chart.yaml`. A quickstart may contain **multiple charts** (e.g., `deploy/helm/app/`, `deploy/helm/db/`, `deploy/helm/ui/`), each becoming a separate ArgoCD application in the pattern.

**Multi-chart naming:** With a single chart, the pattern uses the chart name. With multiple charts, the top-level name is the repository directory name and each chart becomes its own application.

### Detecting Operators

Scan all YAML/template files for these keywords to determine which OpenShift operators are needed:

| Operator | Subscription | Namespace | Channel | Indicators |
|----------|-------------|-----------|---------|------------|
| Red Hat OpenShift AI | `rhods-operator` | `redhat-ods-operator` | `fast` | inferenceservice, servingruntime, datasciencecluster, llm-service, llama-stack, vllm, model-service, openshift-ai, rhods |
| OpenShift Pipelines | `openshift-pipelines-operator-rh` | `openshift-operators` | `latest` | pipeline, pipelinerun, task, taskrun, ingestion-pipeline, tekton |
| NVIDIA GPU Operator | `gpu-operator-certified` | `nvidia-gpu-operator` | `v24.9` | gpu, nvidia, cuda |
| Node Feature Discovery | `nfd` | `openshift-nfd` | `stable` | *(co-dependency of NVIDIA GPU)* |
| OpenShift Service Mesh | `servicemeshoperator` | `openshift-operators` | `stable` | servicemesh, istio, servicemeshcontrolplane |
| OpenShift Serverless | `serverless-operator` | `openshift-serverless` | `stable` | knativeserving, knative, serverless |
| AMQ Streams | `amq-streams` | `openshift-operators` | `stable` | kafka, kafkatopic, kafkaconnect, amq-streams |

**Co-dependencies** (automatically include):
- OpenShift AI requires: Service Mesh + Serverless
- NVIDIA GPU requires: Node Feature Discovery

**Source catalog:**
- Most operators: `redhat-operators`
- NVIDIA GPU: `certified-operators`

### Detecting Secrets

Walk the `values.yaml` tree for each chart. Any key matching these patterns is a potential secret:
`token`, `key`, `password`, `secret`, `credential`, `api_key`, `apikey`, `api-key`, `access_key`, `secret_key`

Record the full dotted path (e.g., `llm-service.secret.hf_token`).

**False positive filtering** — three layers prevent noise:

1. **Known false positives** (exact match, always excluded): `secretkey`, `key`, `secrets`, `bearertokenauth`
2. **Reference suffixes** — keys ending in these are references TO secrets, not values: `name`, `ref`, `path`, `namespace`, `mount`, `class`, `store`, `backend`, `provider`, `type`, `kind`, `version`. E.g., `secretName`, `tokenSecretName` are filtered out.
3. **Config prefixes** — keys starting with these (followed by uppercase or `_`) are boolean/config flags: `use`, `enable`, `disable`, `is`, `has`, `no`. E.g., `useToken`, `enableSecret` are filtered out.

Duplicate secret names across charts are disambiguated using the values path (e.g., two `password` keys become `pgvector_password` and `redis_password`).

### Detecting Features

| Feature | Detection |
|---------|-----------|
| Vector DB | Dependencies named: pgvector, redis, elasticsearch, milvus, chroma, qdrant; or "vector" in templates |
| LLM Serving | Dependencies named: llm-service, vllm, llama-stack, tgi, model-service; or "vllm" in templates |
| Object Storage | Dependencies named: minio, s3, object-storage; or "minio" in templates |
| Data Pipeline | Dependencies named: ingestion-pipeline, pipeline, data-pipeline |
| GPU Required | NVIDIA GPU operator detected; or "gpu" in templates |

### Namespace Grouping (Multi-Chart)

When a quickstart has multiple charts, namespaces are assigned by subdirectory structure:

- Charts sharing a parent directory (e.g., `observability/collector` and `observability/tempo`) are grouped into a **shared namespace** (`observability`).
- Charts with no subdirectory grouping get their own namespace (chart name).
- **Numbered prefixes are stripped**: `01-operators/` → group `operators`, `02-services/` → group `services`.

**OAI label propagation:** If ANY chart in a namespace group needs OpenShift AI labels, the entire shared namespace gets `opendatahub.io/dashboard: "true"` and `modelmesh-enabled: "false"`.

**Inference detection per chart:** A chart needs OAI labels if it contains `inferenceservice`, `servingruntime`, or `datasciencecluster` in its templates, OR has dependencies named `llm-service`, `vllm`, `llama-stack`, `model-service`, or `tgi`.

---

## 2. Generated File Structure

A valid pattern produces exactly these files:

```
<pattern-name>/
  values-global.yaml          # Global config + multisource settings
  values-prod.yaml             # Cluster group: namespaces, operators, apps
  values-secret.yaml.template # Vault secret definitions (v2.0 format)
  Makefile                    # Just: include Makefile-common
  Makefile-common             # Ansible-based targets (rhvp.cluster_utils)
  pattern.sh                  # Podman utility container runner (verbatim)
  pattern-metadata.yaml       # Pattern registry metadata
  ansible.cfg                 # Ansible configuration
  .ansible-lint               # Empty file (required)
  .gitignore                  # Standard ignores
  overrides/                  # Platform-specific value overrides
    values-AWS.yaml
    values-Azure.yaml
    values-GCP.yaml
    values-IBMCloud.yaml
    values-None.yaml
  charts/<app-name>/      # Quickstart Helm chart(s) (local strategy)
  charts/<app-name-2>/    # Additional charts for multi-chart quickstarts
  docs/
    quickstart-analysis.md    # Generated analysis report
```

---

## 3. YAML Schemas (Exact Structure)

### values-global.yaml

**CRITICAL:** `main:` is a ROOT-LEVEL key, NOT nested under `global:`.

```yaml
---
global:
  pattern: <pattern-name>
  options:
    useCSV: false
    syncPolicy: Automatic
    installPlanApproval: Automatic
main:
  clusterGroupName: hub
  multiSourceConfig:
    enabled: true
    clusterGroupChartVersion: "0.9.*"
```

### values-prod.yaml

```yaml
clusterGroup:
  name: hub
  isHubCluster: true

  namespaces:
    # Infrastructure (always present when vault enabled)
    - vault
    - golang-external-secrets
    # Operator namespaces (skip openshift-operators, it always exists)
    # Some operators need operatorGroup config:
    - redhat-ods-operator:
        operatorGroup: true
        targetNamespaces: []
    - openshift-serverless:
        operatorGroup: true
        targetNamespaces: []
    # Application namespace(s)
    # Single chart: one namespace matching the app name
    # Multi-chart: one namespace per group (or per chart if ungrouped)
    - <app-namespace>:
        operatorGroup: true          # Only if any chart in this namespace needs OAI
        targetNamespaces:
          - <app-namespace>
        labels:
          opendatahub.io/dashboard: "true"    # Only if OAI inference detected
          modelmesh-enabled: "false"
    - <other-namespace>             # Plain string if no OAI labels needed

  subscriptions:
    <operator-key>:
      name: <subscription-name>
      namespace: <operator-namespace>
      # Only include source if NOT redhat-operators:
      source: certified-operators    # e.g., for NVIDIA GPU

  projects:
    - hub

  sharedValueFiles:
    - "/overrides/values-{{ $.Values.global.clusterPlatform }}.yaml"

  applications:
    # Infrastructure apps (vault enabled)
    vault:
      name: vault
      namespace: vault
      project: hub
      chart: hashicorp-vault
      chartVersion: "0.1.*"
    golang-external-secrets:
      name: golang-external-secrets
      namespace: golang-external-secrets
      project: hub
      chart: golang-external-secrets
      chartVersion: "0.2.*"
    # Application chart(s) — one per chart (local strategy)
    <app-name>:
      name: <app-name>
      namespace: <app-namespace>      # Group namespace if multi-chart grouped
      project: hub
      path: charts/<app-name>
    # Multi-chart: additional applications
    <app-name-2>:
      name: <app-name-2>
      namespace: <app-namespace-2>
      project: hub
      path: charts/<app-name-2>
```

**For external chart strategy** (instead of `path:`):
```yaml
    <app-name>:
      name: <app-name>
      namespace: <app-namespace>
      project: hub
      repoURL: <helm-repo-url>
      chart: <chart-name>
      targetRevision: <version>
```

### values-secret.yaml.template

**MUST have `version: "2.0"`** (defaults to deprecated 1.0 if missing).
**Uses `vaultPrefixes:` (plural, list)** — NOT `vaultPrefixOverride`.

```yaml
version: "2.0"
secrets:
  - name: <pattern-name>-secrets
    vaultPrefixes:
      - global
    fields:
      - name: <secret-name>
        onMissingValue: prompt
      # For auto-generated secrets:
      - name: <secret-name>
        onMissingValue: generate
        vaultPolicy: validatedPatternDefaultPolicy
```

Valid `onMissingValue` options: `error`, `prompt`, `generate`

---

## 4. Multisource Architecture

### What It Is

Modern Validated Patterns use **multisource configuration**. Instead of forking multicloud-gitops and including a `common/` git subtree, the pattern:

1. Sets `main.multiSourceConfig.enabled: true` in values-global.yaml
2. Specifies `clusterGroupChartVersion: "0.9.*"` to pull the clustergroup chart from the upstream VP Helm registry
3. Infrastructure apps (vault, external-secrets) reference charts by `chart:` + `chartVersion:` (pulled remotely)
4. Application charts use `path:` (local to repo) or `repoURL:` (external)

### Why It Matters

- No fork of multicloud-gitops required
- Upstream bug fixes received by bumping `clusterGroupChartVersion`
- No `common/` git subtree (eliminated from modern patterns)
- Ansible code lives in `rhvp.cluster_utils` collection (pre-installed in utility container)

### pattern.sh

The `pattern.sh` script runs all make targets inside a podman-based utility container (`quay.io/validatedpatterns/utility-container`). This container includes:
- `rhvp.cluster_utils` Ansible collection
- Helm, kubectl, oc CLI tools
- All required dependencies

The script is **identical across all production patterns**. Copy it verbatim.

### Makefile-common

Uses Ansible-based targets:
```makefile
$(ANSIBLE_RUN) rhvp.cluster_utils.install
$(ANSIBLE_RUN) rhvp.cluster_utils.load_secrets
$(ANSIBLE_RUN) rhvp.cluster_utils.validate_prereq
```

---

## 5. Validation & Auto-Fix

After generating a pattern, the validator runs deterministic structural checks and optionally an LLM semantic review. When auto-fix is enabled (default), a self-correcting loop applies fixes and re-validates up to 3 iterations.

### Checklist

- [ ] `values-global.yaml`: `main:` is at root level (sibling of `global:`, not nested)
- [ ] `values-global.yaml`: `multiSourceConfig.enabled: true`
- [ ] `values-global.yaml`: `clusterGroupChartVersion` present in multiSourceConfig
- [ ] `values-prod.yaml`: vault + golang-external-secrets apps present (if vault enabled)
- [ ] `values-prod.yaml`: Infrastructure apps use `chart:` + `chartVersion:` (not `path:`)
- [ ] `values-prod.yaml`: Application apps use `path: charts/<name>` (local) or `repoURL:` (external)
- [ ] `values-prod.yaml`: `sharedValueFiles` references overrides template
- [ ] `values-prod.yaml`: Operators with dedicated namespaces have `operatorGroup: true` where needed
- [ ] `values-prod.yaml`: `projects: [prod]` (list format)
- [ ] `values-prod.yaml`: `subscriptions:` is a dict (not a list)
- [ ] `values-secret.yaml.template`: Has `version: "2.0"`
- [ ] `values-secret.yaml.template`: Uses `vaultPrefixes:` (plural, list) — NOT `vaultPrefixOverride`
- [ ] `Makefile`: Contains only `include Makefile-common`
- [ ] `Makefile-common`: Uses `$(ANSIBLE_RUN) rhvp.cluster_utils.*` targets
- [ ] `pattern.sh`: Present, executable, uses utility container
- [ ] `charts/<name>/`: Chart(s) copied (local strategy) with Chart.yaml
- [ ] `overrides/`: Platform files exist (AWS, Azure, GCP, IBMCloud, None)
- [ ] No `common/` directory (not needed with multisource)
- [ ] No `setup-common.sh` (obsolete)
- [ ] No `charts/hub/` paths (must be `charts/`)

### Auto-Fixable Issues

The validator can automatically fix these common issues:

| Issue | Fix Applied |
|-------|-------------|
| `main:` nested under `global:` | Moved to root level |
| `multiSourceConfig.enabled` not true | Set to `true` |
| Missing `clusterGroupChartVersion` | Added from config default |
| Secret `version` missing or wrong | Set to `"2.0"` |
| `vaultPrefixOverride` used | Converted to `vaultPrefixes: [value]` |
| `vaultPrefixes` not a list | Wrapped in list |
| `include common/Makefile` | Changed to `include Makefile-common` |
| `pattern.sh` not executable | `chmod 755` |
| Chart path `charts/hub/` | Changed to `charts/` |
| Missing `sharedValueFiles` | Added with platform override template |
| Infra app uses `path:` | Changed to `chart:` + `chartVersion:` |
| Missing `overrides/` directory or files | Created with platform placeholders |

---

## 6. Known Pitfalls & Docs-vs-Reality Gaps

| Issue | Wrong | Right |
|-------|-------|-------|
| main: nesting | `global: { main: ... }` | `main:` at root level |
| Chart path | `charts/hub/` | `charts/` |
| Secret version | *(missing)* | `version: "2.0"` |
| Vault prefix | `vaultPrefixOverride: "global"` | `vaultPrefixes: [global]` |
| Makefile include | `include common/Makefile` | `include Makefile-common` |
| common/ subtree | Required | Not needed (multisource) |
| Docs site | Documents old common/ approach | Actual patterns use multisource |
| Subscriptions format | List (some doc pages) | Dict (current standard) |
| Projects key | `argoProjects:` (multicloud-gitops) | `projects:` (docs/rag-llm-gitops) |

**The validatedpatterns.io docs are behind the code.** Always verify against actual repos (multicloud-gitops main branch, rag-llm-gitops) rather than docs alone.

---

## 7. Deployment Steps

Once the pattern is generated:

```bash
cd <pattern-dir>
git init && git add -A && git commit -m "Initial pattern"
oc login <cluster-api-url>
cp values-secret.yaml.template ~/values-secret-<pattern-name>.yaml
# Edit ~/values-secret-<pattern-name>.yaml with actual secret values
./pattern.sh make install
```

---

## 8. Standard Operating Procedure

When asked to transform a quickstart:

1. **Locate** all Helm charts (recursive search in priority order above)
2. **Parse** each Chart.yaml for name, version, dependencies
3. **Compute** namespace grouping from subdirectory structure (strip numeric prefixes)
4. **Parse** each values.yaml for configuration and secrets (with false-positive filtering)
5. **Detect** required operators by scanning all YAML/template files across all charts
6. **Detect** inference indicators per chart for OAI namespace labeling
7. **Resolve** co-dependencies (OpenShift AI -> Service Mesh + Serverless, GPU -> NFD)
8. **Generate** all pattern files following exact schemas above (one application per chart)
9. **Validate** against the checklist, auto-fix issues, and re-validate
10. **Report** what was generated and any edge cases that need manual review

## Output Format

When presenting results, provide:
1. A brief summary of what was detected
2. The generated YAML files (or file listing if using the CLI tool)
3. Any warnings about edge cases or manual steps needed
4. The deployment steps
