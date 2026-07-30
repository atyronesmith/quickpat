# Block Catalog — compose-spec reference

All 9 block types available in an ApplicationSpec. Each section covers: when to include, config fields, dependencies on other blocks, and a minimal example.

---

## `ai-platform-foundation`

**When to include:** Always — every AI application on OpenShift needs OpenShift AI, Serverless, and Service Mesh.

**Operators installed:** `openshift-ai`, `serverless`, `servicemesh`

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `dsc` | map | `{kserve: Managed, dashboard: Managed}` | DataScienceCluster component states. Only set components you need. |
| `channel` | string | `fast` | OLM channel for OpenShift AI subscription. |

**DSC component reference:**

| Component | When to set Managed |
|---|---|
| `kserve` | Any model serving (model-serving or llama-stack blocks) |
| `dashboard` | Always — needed for AI project namespaces |
| `trustyai` | **Required when guardrails-orchestrator block is present** |
| `modelmeshserving` | Legacy model serving; include if upstream chart uses it |
| `datasciencepipelines` | When data-pipeline block is present (optional — Tekton is used instead by default) |
| `llamastackoperator` | Do NOT set manually — auto-injected when llama-stack block is present |
| `kueue`, `ray`, `trainingoperator`, `workbenches` | Most QS conversions: `Removed` |

**Example:**
```yaml
platform:
  type: ai-platform-foundation
  config:
    dsc:
      kserve: Managed
      dashboard: Managed
```

With guardrails:
```yaml
platform:
  type: ai-platform-foundation
  config:
    dsc:
      kserve: Managed
      dashboard: Managed
      trustyai: Managed       # required for guardrails-orchestrator
```

---

## `gpu-compute`

**When to include:** When any `model-serving` block has `gpu: true`.

**Operators installed:** `nvidia-gpu`, `nfd` (Node Feature Discovery)

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `mig_strategy` | string | `none` | NVIDIA MIG partitioning: `none`, `single`, `mixed`. Use `none` unless cluster has MIG-capable GPUs. |
| `dcgm` | bool | `true` | Enable DCGM (Data Center GPU Manager) exporter for metrics. |
| `channel` | string | `v24.9` | OLM channel for GPU operator. |

**Example:**
```yaml
gpu:
  type: gpu-compute
  config:
    mig_strategy: none
    dcgm: true
```

---

## `model-serving`

**When to include:** Any application that serves an LLM or detector model via KServe InferenceService.

**Generates:** `servingruntime.yaml`, `inferenceservice.yaml`

**Requires OAI namespace labels:** yes (`opendatahub.io/dashboard: "true"`)

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `model` | string | required | HuggingFace model ID or descriptive name |
| `runtime` | string | `vllm` | `vllm` or `custom`. Custom requires `image`. |
| `image` | string | — | Container image for custom runtime. Required when `runtime: custom`. |
| `gpu` | bool | false | Adds GPU resource requests/limits. |
| `replicas.min` | int | 0 | KServe scale-to-zero minimum. |
| `replicas.max` | int | 1 | Maximum replicas. |
| `resources.requests` | map | — | CPU, memory, gpu requests. |
| `resources.limits` | map | — | CPU, memory, gpu limits. |
| `storage.type` | string | — | `oci` (model car) or `s3` (from object-storage bucket). |
| `storage.uri` | string | — | For type `oci`: full OCI URI. |
| `storage.connection` | string | — | For type `s3`: secret name or wiring reference. |
| `storage.path` | string | — | For type `s3`: model directory path in bucket. |
| `vllm_args` | map | — | Extra vLLM CLI args. Key = flag name, value = arg value or `""` for bare flags. |

**Storage wiring pattern (s3):**
```yaml
storage:
  type: s3
  connection: "{{ blocks.model-storage.output.connection_name }}"
  path: granite-guardian-hap-125m
```

**Examples:**

GPU-backed LLM with OCI model:
```yaml
llm:
  type: model-serving
  config:
    model: meta-llama/Llama-3.2-3B-Instruct
    runtime: vllm
    gpu: true
    storage:
      type: oci
      uri: oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.2-3b-instruct
```

CPU-only detector with custom runtime:
```yaml
hap-detector:
  type: model-serving
  config:
    model: ibm-granite/granite-guardian-hap-125m
    runtime: custom
    image: quay.io/trustyai/guardrails-detector-huggingface-runtime:latest
    gpu: false
    resources:
      requests: { cpu: 1, memory: 4Gi }
      limits:   { cpu: 2, memory: 8Gi }
    storage:
      type: s3
      connection: "{{ blocks.model-storage.output.connection_name }}"
      path: granite-guardian-hap-125m
```

---

## `object-storage`

**When to include:** When the application needs to store/retrieve files, models, or documents. Required as a source for `data-pipeline` and for model weights when `model-serving` uses `storage.type: s3`.

**Generates (provider-conditional):** PVC + MinIO Deployment (minio), ObjectBucketClaim + setup Job (odf), data-connection Secret (all providers).

| Provider | What it provisions |
|---|---|
| `minio` | In-cluster MinIO PVC + Deployment + bucket-init container |
| `odf` | ObjectBucketClaim + ODF setup Job (ODF must be pre-installed) |
| `s3` | data-connection Secret only (pointing at external S3) |

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `provider` | string | `minio` | `minio`, `odf`, or `s3` |
| `bucket` | string | `data` | Bucket name |
| `storage` | string | `20Gi` | PVC size (minio only) |
| `init_models` | list | — | HuggingFace model IDs to pre-download into the bucket at startup |
| `endpoint` | string | — | S3 endpoint URL (s3 provider) |
| `region` | string | — | AWS region (s3 provider) |
| `odfStorageClass` | string | — | ODF storage class (odf provider) |

**Note:** `provider: odf` requires ODF (OpenShift Data Foundation) to already be installed in the cluster. The block does not install ODF.

**Example:**
```yaml
store:
  type: object-storage
  config:
    provider: minio
    bucket: documents
    storage: 20Gi
```

With pre-loaded models:
```yaml
model-storage:
  type: object-storage
  config:
    provider: minio
    storage: 50Gi
    init_models:
      - ibm-granite/granite-guardian-hap-125m
      - protectai/deberta-v3-base-prompt-injection-v2
```

---

## `vector-store`

**When to include:** Any RAG or semantic search application that needs a vector database. Deploys pgvector (PostgreSQL + pgvector extension).

**Generates:** `deployment.yaml`, `service.yaml`, `secret.yaml` (with a generated password)

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `database` | string | `vectordb` | PostgreSQL database name |
| `port` | int | `5432` | PostgreSQL port |

**Example:**
```yaml
db:
  type: vector-store
  config:
    database: ragdb
```

---

## `data-pipeline`

**When to include:** Any application that ingests documents or data into a vector store or object store on a schedule. Deploys a Tekton Pipeline.

**Operators installed:** `openshift-pipelines`

**Generates:** `pipeline.yaml`, `ingest-task.yaml`, `pipeline-run.yaml`, `trigger.yaml`, `rbac.yaml`

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `sources` | list | — | List of `{name: string}` source descriptors |
| `schedule` | string | required | `manual`, `hourly`, or `daily` |
| `chunk_size` | int | `512` | Document chunk size for vector embedding |

**Inputs (required for correct generation):**

| Input role | Description |
|---|---|
| `vector_store` | Block name of the pgvector block (provides connection details) |
| `object_storage` | Block name of the object-storage block (provides S3 source bucket) |

**Example:**
```yaml
ingest:
  type: data-pipeline
  config:
    sources:
      - name: docs
    schedule: manual
    chunk_size: 512
  inputs:
    vector_store: db
    object_storage: store
```

---

## `guardrails-orchestrator`

**When to include:** Any application that needs content safety filtering via TrustyAI GuardrailsOrchestrator.

**Generates:** `orchestrator.yaml` (GuardrailsOrchestrator CR), `configmap.yaml`

**Hard dependency:** `ai-platform-foundation` DSC must have `trustyai: Managed`. The skill sets this automatically — do not forget it.

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `enable_built_in_detectors` | bool | `true` | Enable TrustyAI built-in detectors |
| `enable_guardrails_gateway` | bool | `false` | Enable HTTP gateway mode |
| `otel_protocol` | string | `grpc` | OpenTelemetry protocol: `grpc` or `http` |
| `detectors` | map | — | Map of detector name → `{endpoint, port}` |
| `llm.endpoint` | string | — | LLM predictor endpoint (use wiring reference) |
| `chunker.endpoint` / `port` | — | — | Optional chunker service |
| `language_detector.endpoint` / `port` | — | — | Optional language detector service |

**Always use wiring references for endpoints:**
```yaml
detectors:
  hap:
    endpoint: "{{ blocks.hap-detector.output.predictor_host }}"
    port: 8000
llm:
  endpoint: "{{ blocks.llm.output.predictor_host }}"
```

**Example:**
```yaml
guardrails:
  type: guardrails-orchestrator
  config:
    enable_built_in_detectors: true
    otel_protocol: grpc
    detectors:
      hap:
        endpoint: "{{ blocks.hap-detector.output.predictor_host }}"
        port: 8000
    llm:
      endpoint: "{{ blocks.llm.output.predictor_host }}"
```

---

## `llama-stack`

**When to include:** Agentic applications that need a LlamaStack server to coordinate LLM + vector store + tool calls.

**Generates:** `deployment.yaml`, `service.yaml`

**Auto-injects `llamastackoperator: Managed` into DSC** — do not add this to the `ai-platform-foundation` DSC config manually.

**Config fields:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `port` | int | `8321` | LlamaStack server port |

**Inputs (resolved to concrete endpoints by the compiler):**

| Input role | Description |
|---|---|
| `llm` | Block name of the model-serving block (resolves vLLM inference URL) |
| `vector_store` | Block name of the vector-store block (resolves pgvector host + db) |

**Example:**
```yaml
llm-server:
  type: llama-stack
  config:
    port: 8321
  inputs:
    llm: llm
    vector_store: db
```

---

## `sso-auth`

**When to include:** Do not include — this block type is registered but has no generation logic yet. It is a placeholder for future Keycloak/SSO support. Omit it from any generated spec.

---

## Common Patterns

### RAG chatbot (no ingestion pipeline)
Blocks: `ai-platform-foundation`, `model-serving`, `vector-store`, `object-storage`

### RAG with ingestion
Add: `data-pipeline` with `inputs.vector_store` and `inputs.object_storage`

### RAG with agentic layer
Replace direct LLM serving with `llama-stack` block; keep `model-serving` as the underlying inference block

### Guardrails / content safety
Add: `guardrails-orchestrator` + detector `model-serving` blocks + set `trustyai: Managed` in DSC

### GPU + CPU deployment modes
Use `devices: [cpu, gpu]` in metadata — GPU operators move to `values-gpu.yaml`
