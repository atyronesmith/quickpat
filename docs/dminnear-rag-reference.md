# dminnear RAG QS→VP Reference Implementation

dminnear manually converted the RAG quickstart into a validated pattern, forked from
`validatedpatterns-sandbox/ai-quickstart-rag` starting 2025-11-13. This documents what
he built and what quickpat doesn't yet generate.

## Repo Structure

```
dminnear-rh/ai-quickstart-rag/
├── values-global.yaml
├── values-prod.yaml                  # quickpat generates values-hub.yaml
├── values-secret.yaml.template
├── Makefile / Makefile-common / pattern.sh / pattern-metadata.yaml / ansible.cfg
├── charts/
│   ├── dsc/                          # DataScienceCluster CR
│   ├── nfd/                          # NodeFeatureDiscovery CR
│   ├── nvidia-config/                # NVIDIA ClusterPolicy CR
│   └── pattern-secrets/              # ExternalSecret CRs (Vault-backed)
│       ├── configure-pipeline-secret.yaml
│       ├── llm-service-secret.yaml
│       ├── minio-secret.yaml
│       └── pgvector-secret.yaml
└── overrides/
    ├── rag-quickstart.yaml           # chart value overrides
    ├── values-cpu.yaml               # CPU-only deployment profile
    ├── values-gpu.yaml               # GPU deployment profile
    └── values-gpu-AWS.yaml           # GPU on AWS (machineset provisioning)
```

## Gap Analysis: quickpat vs dminnear

### 1. ignoreDifferences (NOT GENERATED)

dminnear's `values-prod.yaml` includes `ignoreDifferences` on the rag-quickstart app:

```yaml
rag-quickstart:
  ignoreDifferences:
    - group: datasciencepipelinesapplications.opendatahub.io
      kind: DataSciencePipelinesApplication
      jsonPointers:
        - /spec
    - group: kubeflow.org
      kind: Notebook
      jsonPointers:
        - /spec
        - /metadata/annotations
        - /metadata/labels
    - group: route.openshift.io
      kind: Route
      jsonPointers:
        - /spec/host
        - /spec/alternateBackends
```

**Why needed:** ArgoCD detects drift on resources whose controllers mutate fields
after creation. Without ignoreDifferences, ArgoCD loops on sync.

**Known CRD→ignore patterns:**
- `Route` → `/spec/host`, `/spec/alternateBackends` (OpenShift router sets these)
- `Notebook` → `/spec`, `/metadata/annotations`, `/metadata/labels` (RHOAI controller)
- `DataSciencePipelinesApplication` → `/spec` (DSP controller reconciles spec)
- Other likely candidates: `InferenceService`, `ServingRuntime`, `KnativeServing`

### 2. ExternalSecret Charts (PARTIALLY GENERATED)

quickpat generates a `charts/pattern-secrets/` chart, but dminnear's version is more
sophisticated:

- Uses `external-secrets.io/v1` `ExternalSecret` CRs (not just K8s Secrets)
- Constructs composite values (e.g., `jdbc-uri` from host/port/dbname/password)
- References Vault paths: `secret/data/hub/<secret-name>`
- Uses `ClusterSecretStore` named `vault-backend`

quickpat currently generates simple K8s Secret templates. The ExternalSecret approach
is what validated patterns actually use in production.

### 3. Operator CRD Charts (NOT GENERATED)

dminnear created dedicated charts for operator configuration CRs:

- `charts/dsc/` → `DataScienceCluster` CR (enables kserve, pipelines, workbenches, etc.)
- `charts/nfd/` → `NodeFeatureDiscovery` CR
- `charts/nvidia-config/` → NVIDIA `ClusterPolicy` CR

quickpat installs operators via subscriptions but doesn't generate the CRs that
configure them.

### 4. Device-Based Profiles (NOT GENERATED)

dminnear uses `global.device` (cpu/gpu) to switch between deployment profiles via
`sharedValueFiles`:

```yaml
sharedValueFiles:
  - /overrides/values-{{ $.Values.global.device }}.yaml
  - /overrides/values-{{ $.Values.global.device }}-{{ $.Values.global.clusterPlatform }}.yaml
```

The GPU profile adds NFD/NVIDIA subscriptions and apps. The CPU profile configures
model resources for CPU-only inference. The GPU-AWS profile adds imperative machineset
provisioning.

quickpat generates platform overrides (AWS/Azure/GCP) but not device profiles.

### 5. Chart Override File (PARTIALLY GENERATED)

dminnear's `overrides/rag-quickstart.yaml` disables in-chart secret creation and
reconfigures pipelines for VP deployment:

```yaml
llm-service:
  secret:
    enabled: false
pgvector:
  secret:
    create: false
configure-pipeline:
  secret:
    create: false
  minio:
    secret:
      create: false
```

quickpat generates an overrides file but doesn't know to disable in-chart secrets
when Vault/ExternalSecrets are handling them.

### 6. Secrets Template Maturity

dminnear's `values-secret.yaml.template` has:
- Correct field names with default values (user=postgres, dbname=rag_blueprint, etc.)
- Correct minio defaults (user=minio_rag_user)
- Password fields use `onMissingValue: generate` with `basicPolicy`
- Non-secret fields populated with known defaults

quickpat's version is noisier — it lists every detected key including pipeline config
values that aren't really secrets (SOURCE, EMBEDDING_MODEL, NAME, VERSION, etc.).
