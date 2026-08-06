# ApplicationSpec Format Reference

Complete structure of a `spec.yaml` file consumed by `quickpat compose`.

---

## Top-level structure

```yaml
apiVersion: supplychain/v1alpha1   # required, always this value
kind: ApplicationSpec              # required, always this value

metadata:                          # required
  name: <string>
  description: <string>           # optional
  tier: sandbox | tested | maintained
  devices: [cpu, gpu, hpu]        # optional
  upstream:                       # optional
    ...

blocks:                            # required — at least one block
  <block-name>:
    type: <block-type>
    ...

custom:                            # optional
  <component-name>:
    ...

wiring:                            # optional, descriptive only
  - from: <block-name>
    to: <block-name>
    via: <string>
```

---

## `metadata` fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Pattern name, lowercase hyphen-separated |
| `description` | string | no | Free text description |
| `tier` | enum | no | Default: `sandbox` |
| `devices` | list | no | Default: omitted (CPU-only assumed) |
| `upstream` | map | no | Existing QS Helm chart to wrap |

### `tier` values

| Value | Meaning |
|---|---|
| `sandbox` | Proof of concept, not cluster-validated |
| `tested` | Validated on a real OpenShift cluster |
| `maintained` | Actively maintained, customer-facing |

### `devices` values and effect

| Value | Effect |
|---|---|
| `[cpu]` | CPU-only; no GPU overrides generated |
| `[gpu]` | GPU-only; GPU operators in values-prod.yaml |
| `[cpu, gpu]` | Both modes; GPU operators move to values-gpu.yaml |

When `[cpu, gpu]` is specified, the VP output includes:
- `overrides/values-gpu.yaml` — GPU operator subscriptions
- `overrides/values-cpu.yaml` — CPU-only overrides

### `upstream` fields

Used when the VP wraps an existing Quickstart Helm chart (the `quickpat create` strategy applied via compose).

| Field | Type | Default | Notes |
|---|---|---|---|
| `repo` | string | required | Git URL of the upstream QS chart repo |
| `path` | string | `chart` | Path to the Helm chart within the repo |
| `branch` | string | `main` | Branch to track |
| `extraValues` | map | — | Values written to `overrides/<app-name>.yaml`, passed via `extraValueFiles` in ArgoCD app |
| `ignoreDifferences` | list | — | ArgoCD ignoreDifferences entries for the upstream app |

**extraValues example:**
```yaml
upstream:
  repo: https://github.com/rh-ai-quickstart/RAG.git
  path: deploy/helm/rag
  branch: main
  extraValues:
    llm-service:
      secret:
        enabled: false
  ignoreDifferences:
    - group: route.openshift.io
      kind: Route
      jsonPointers: [/spec/host]
```

---

## `blocks` — BlockInstance structure

Each named entry under `blocks:` is a BlockInstance:

```yaml
blocks:
  <block-name>:
    type: <block-type>             # required — one of the 9 block types
    profile: <string>             # optional — hint, no enforced behavior currently
    config: {}                    # optional — block-type-specific config
    secrets: {}                   # optional — secret declarations
    inputs: {}                    # optional — input wiring by role
```

### `secrets` structure

```yaml
secrets:
  <secret-name>:
    vault_path: <string>          # default: "<block-name>/<secret-name>"
    key: <string>                 # default: secret-name
    generate: false               # if true, generate a random value at install time
```

Omit `secrets:` entirely unless you need to control vault paths or force generation. The compiler uses sane defaults.

### `inputs` structure

```yaml
inputs:
  <role>: <block-name>
```

Roles are semantic labels consumed by the template generator to resolve endpoints. Current roles in use:

| Block type | Role | Resolves |
|---|---|---|
| `data-pipeline` | `vector_store` | pgvector host, port, database, password |
| `data-pipeline` | `object_storage` | S3 endpoint, bucket, credentials |
| `llama-stack` | `llm` | vLLM inference service URL |
| `llama-stack` | `vector_store` | pgvector host, database |

**`inputs` is what drives generation.** The `wiring:` section at the top level is descriptive only.

### Wiring references in config values

Block config values can reference other blocks' outputs using double-brace syntax:

```yaml
"{{ blocks.<block-name>.output.<field> }}"
"{{ blocks.<block-name>.config.<field> }}"
"{{ custom.<component-name>.endpoint }}"
```

Common output references:

| Reference | Resolves to |
|---|---|
| `{{ blocks.X.output.predictor_host }}` | KServe InferenceService predictor hostname |
| `{{ blocks.X.output.connection_name }}` | data-connection Secret name from object-storage |
| `{{ blocks.X.output.service_host }}` | Kubernetes Service hostname |
| `{{ blocks.X.config.model }}` | The `config.model` value of block X |

---

## `custom` — CustomComponent structure

```yaml
custom:
  <component-name>:
    source:
      image: <image-ref>           # OR source.chart: charts/<name>
      chart: charts/<name>         # local chart copied into vp-out/charts/
    namespace: <string>             # optional — ArgoCD app namespace (default: pattern name)
    extraValueFiles:               # optional — ArgoCD extraValueFiles paths
      - /overrides/<name>.yaml
    extraValues:                   # optional — inline values → overrides/<name>.yaml
      key: value                   #   (parity with upstream.extraValues; auto-adds
                                   #    /overrides/<name>.yaml to extraValueFiles)
    deploy: argocd                 # optional — argocd (default) | manual
    replicas: 1                    # optional, default: 1
    ports:
      - name: http
        port: 8080
        route: true                # optional — creates an OpenShift Route
        tls:
          termination: edge        # optional — edge, passthrough, or reencrypt
    env:
      ENV_VAR: value               # optional map; supports wiring references
    resources:
      requests:
        cpu: "100m"
        memory: "256Mi"
      limits:
        cpu: "500m"
        memory: "512Mi"
    probes:                        # optional
      liveness:
        path: /health
        port: 8080
      readiness:
        path: /ready
        port: 8080
    monitor: {}                    # optional — ServiceMonitor config
    description: <string>         # optional
```

Custom components are copied from `charts/<component-name>/` in the application repo if they exist. The `source.image` and fields above are used to generate the chart when it doesn't exist yet.

**Override files:** when `extraValueFiles` lists `/overrides/<file>.yaml`, compose copies `<spec_dir>/overrides/<file>.yaml` into `vp-out/overrides/` (same source-layer model as `charts/`). If the source file is missing and `extraValues` is unset, a documented stub is written so the ArgoCD reference always resolves. When both `extraValues` and a source file exist for the same path, `extraValues` wins.

---

## `wiring` — descriptive only

```yaml
wiring:
  - from: <block-name>
    to: <block-name>
    via: <string>        # optional label for the connection
```

Documents the logical connections between blocks. The compiler does NOT use this for generation — it uses `inputs:` on each block instead. Include wiring for documentation purposes when the connections are non-obvious.

---

## Complete minimal example (RAG chatbot)

```yaml
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec

metadata:
  name: my-rag-app
  description: RAG chatbot with pgvector and MinIO
  tier: sandbox
  upstream:
    repo: https://github.com/rh-ai-quickstart/RAG.git
    path: chart
    branch: main

blocks:
  platform:
    type: ai-platform-foundation
    config:
      dsc:
        kserve: Managed
        dashboard: Managed

  llm:
    type: model-serving
    config:
      model: meta-llama/Llama-3.2-3B-Instruct
      runtime: vllm
      gpu: true
      storage:
        type: oci
        uri: oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.2-3b-instruct

  gpu:
    type: gpu-compute

  db:
    type: vector-store
    config:
      database: ragdb

  store:
    type: object-storage
    config:
      provider: minio
      bucket: documents

  ingest:
    type: data-pipeline
    config:
      schedule: manual
    inputs:
      vector_store: db
      object_storage: store
```
