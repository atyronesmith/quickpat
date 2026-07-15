# Tutorial: quickpat compose — Lemonade-Stand

`quickpat compose` takes a composition spec — a YAML file declaring typed building blocks and custom application components — and generates a complete Validated Pattern directory. The same spec also produces a deployable QS Helm chart. One input, two outputs.

This tutorial walks through building the lemonade-stand composition spec and running it.

**What you'll build:** a VP for the [lemonade-stand-assistant](https://github.com/rh-ai-quickstart/lemonade-stand-assistant) quickstart — a guardrailed chat assistant using TrustyAI orchestration. It has 3 model-serving instances, MinIO for detector model weights, custom microservices, and two frontends.

---

## Composition Spec vs. `quickpat create`

`quickpat create` analyzes an existing Helm chart and infers what the VP needs — operators, secrets, strategy. It works well for straightforward quickstarts and produces a VP matching the upstream chart's structure.

`quickpat compose` starts from a spec you author. Instead of inferring, you declare: these are the building blocks (model serving, object storage, GPU), this is the custom application code, these are the Vault secrets. The compiler translates that declaration into the VP. The spec is portable — it documents the application's full dependency graph in one readable file.

Use `quickpat create` when you have an existing quickstart and want a fast conversion. Use `quickpat compose` when you're authoring a new application, or when you want an explicit, reviewable spec that travels alongside the pattern.

---

## The Spec Format

A composition spec has four sections:

```yaml
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata: { ... }    # application identity + upstream QS reference
blocks: { ... }      # named infrastructure building blocks
wiring: [ ... ]      # data-flow declarations between blocks
custom: { ... }      # application-specific components
```

### metadata

```yaml
metadata:
  name: lemonade-stand
  description: LLM guardrails demo with TrustyAI orchestration
  tier: sandbox            # sandbox | tested | maintained
  upstream:
    repo: https://github.com/rh-ai-quickstart/lemonade-stand-assistant.git
    path: chart
    branch: main
```

`name` becomes the pattern name, default namespace, and Helm release name. `upstream` is the existing QS chart that the VP references as a remote ArgoCD application.

### blocks

Each block is a named instance of a typed building block. The type encodes shared infrastructure knowledge — which operators to install, which namespaces to create, which local charts to add.

**ai-platform-foundation** — installs RHOAI, Serverless, and Service Mesh; creates the DataScienceCluster CR. Every AI application needs this.

```yaml
blocks:
  platform:
    type: ai-platform-foundation
    config:
      dsc:
        kserve: Managed
        trustyai: Managed    # needed for GuardrailsOrchestrator
        dashboard: Managed
        modelmeshserving: Managed
        datasciencepipelines: Removed
        kueue: Removed
        ray: Removed
        trainingoperator: Removed
        workbenches: Removed
```

**gpu-compute** — installs Node Feature Discovery and the NVIDIA GPU Operator; creates the ClusterPolicy.

```yaml
  gpu:
    type: gpu-compute
    config:
      mig_strategy: single
      dcgm: true
```

**model-serving** — one block instance per model. The same block type handles chat LLMs (vLLM on GPU) and small classifiers (CPU). For lemonade-stand, there are three:

```yaml
  llm:
    type: model-serving
    profile: llm-inference    # sets gpu: true, runtime: vllm defaults
    config:
      model: meta-llama/Llama-3.2-3B-Instruct
      storage:
        type: oci
        uri: oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.2-3b-instruct
    secrets:
      vllm-api-key:
        vault_path: lemonade-stand

  hap-detector:
    type: model-serving
    config:
      model: ibm-granite/granite-guardian-hap-125m
      runtime: custom
      image: quay.io/trustyai/guardrails-detector-huggingface-runtime:latest
      gpu: false
      storage:
        type: s3
        connection: "{{ blocks.model-storage.output.connection_name }}"
        path: granite-guardian-hap-125m

  prompt-injection-detector:
    type: model-serving
    config:
      model: protectai/deberta-v3-base-prompt-injection-v2
      runtime: custom
      image: quay.io/trustyai/guardrails-detector-huggingface-runtime:latest
      gpu: false
      storage:
        type: s3
        connection: "{{ blocks.model-storage.output.connection_name }}"
        path: deberta-v3-base-prompt-injection-v2
```

The `{{ blocks.model-storage.output.connection_name }}` reference is a template variable — resolved at compile time from the `object-storage` block's declared outputs. The compiler validates that all references resolve.

**object-storage** — deploys MinIO with an init container that downloads detector models from HuggingFace. Declares the Vault secrets that replace the upstream chart's hardcoded `THEACCESSKEY`/`THESECRETKEY`.

```yaml
  model-storage:
    type: object-storage
    config:
      provider: minio
      storage: 50Gi
      init_models:
        - ibm-granite/granite-guardian-hap-125m
        - protectai/deberta-v3-base-prompt-injection-v2
    secrets:
      access-key:
        vault_path: lemonade-stand/minio
        key: AWS_ACCESS_KEY_ID
      secret-key:
        vault_path: lemonade-stand/minio
        key: AWS_SECRET_ACCESS_KEY
```

**guardrails-orchestrator** — creates the TrustyAI GuardrailsOrchestrator CR and its routing ConfigMap.

```yaml
  guardrails:
    type: guardrails-orchestrator
    config:
      enable_built_in_detectors: true
      detectors:
        hap:
          endpoint: "{{ blocks.hap-detector.output.predictor_host }}"
          port: 8000
        prompt_injection:
          endpoint: "{{ blocks.prompt-injection-detector.output.predictor_host }}"
          port: 8000
      llm:
        endpoint: "{{ blocks.llm.output.predictor_host }}"
```

### wiring

Declarative data-flow between blocks. Used for validation (does the block graph make sense?) and documentation. Does not generate Kubernetes resources.

```yaml
wiring:
  - from: guardrails
    to: llm
    via: chat-generation
  - from: model-storage
    to: hap-detector
    via: model-weights
  - from: model-storage
    to: prompt-injection-detector
    via: model-weights
```

### custom

Application-specific components that aren't reusable building blocks. These generate Deployment + Service resources, optionally with a Route.

```yaml
custom:
  lemonade-stand-app:
    source:
      image: quay.io/ckavili/lemon-fastapi:1.0.26
    ports:
      - { name: http, port: 8080, route: true }
    env:
      GUARDRAILS_ORCHESTRATOR_SERVICE_SERVICE_HOST: "{{ blocks.guardrails.output.service_host }}"
      VLLM_MODEL: "{{ blocks.llm.config.model }}"

  chunker-service:
    source:
      image: quay.io/rh-ee-mmisiura/chunkers:v2.0
    ports:
      - { name: grpc, port: 8085 }

  lingua-detector:
    source:
      image: quay.io/ckavili/lingua-language-detector:0.0.25
    ports:
      - { name: http, port: 8080 }

  shiny-dashboard:
    source:
      image: quay.io/sara_banderby/shinydashboard:fedora
    ports:
      - { name: http, port: 3838, route: true }
    env:
      METRICS_URL: "{{ custom.lemonade-stand-app.endpoint }}/metrics"
```

The FastAPI app, chunker, language detector, and Shiny dashboard are unique to lemonade-stand — there's no reason to build block abstractions for them. The boundary rule: if a component appears in 3+ quickstarts, it should be a block; if it's unique to this application, it's custom.

---

## Running compose

A complete spec is in `examples/lemonade-stand-compose.yaml`. Run it:

```bash
quickpat compose examples/lemonade-stand-compose.yaml -o /tmp/lemonade-stand-pattern
```

Output:

```
=== QuickPat Compose: ApplicationSpec -> Validated Pattern ===

Pattern generated: /tmp/lemonade-stand-pattern/
Files: 17
  values-global.yaml
  values-prod.yaml
  Makefile
  Makefile-common
  pattern.sh
  pattern-metadata.yaml
  ansible.cfg
  .ansible-lint
  .gitignore
  docs/quickstart-analysis.md
  values-secret.yaml.template
  charts/lemonade-stand-secrets/
  overrides/values-AWS.yaml
  overrides/values-Azure.yaml
  overrides/values-GCP.yaml
  overrides/values-IBMCloud.yaml
  overrides/values-None.yaml

Blocks compiled: platform, gpu, llm, hap-detector, prompt-injection-detector, model-storage, guardrails
```

---

## What was generated

### values-prod.yaml

The heart of the VP. The compiler derived all of this from the 7 blocks in the spec:

```yaml
clusterGroup:
  name: prod
  namespaces:
    vault: {}
    external-secrets-operator:
      operatorGroup: true
      targetNamespaces: []
    external-secrets: {}
    openshift-nfd: {}
    nvidia-gpu-operator: {}
    redhat-ods-operator:
      operatorGroup: true
      targetNamespaces: []
    openshift-serverless:
      operatorGroup: true
      targetNamespaces: []
    lemonade-stand:
      operatorGroup: true
      targetNamespaces: [lemonade-stand]
      labels:
        opendatahub.io/dashboard: 'true'
        modelmesh-enabled: 'false'

  subscriptions:
    nfd:
      name: nfd
      namespace: openshift-nfd
    nvidia:
      name: gpu-operator-certified
      namespace: nvidia-gpu-operator
      source: certified-operators
    rhoai:
      name: rhods-operator
      namespace: redhat-ods-operator
    serverless:
      name: serverless-operator
      namespace: openshift-serverless
    servicemesh:
      name: servicemeshoperator
      namespace: openshift-operators
    openshift-external-secrets:
      name: openshift-external-secrets-operator
      namespace: external-secrets-operator
      channel: stable-v1

  applications:
    vault:
      name: vault
      namespace: vault
      chart: hashicorp-vault
      chartVersion: 0.1.*
    openshift-external-secrets:
      name: openshift-external-secrets
      namespace: external-secrets
      chart: openshift-external-secrets
      chartVersion: 0.0.*
    nfd:
      name: nfd
      namespace: openshift-nfd
      path: charts/nfd
    nvidia-config:
      name: nvidia-config
      namespace: nvidia-gpu-operator
      path: charts/nvidia-config
    dsc:
      name: dsc
      namespace: redhat-ods-operator
      path: charts/dsc
    lemonade-stand:
      name: lemonade-stand
      namespace: lemonade-stand
      repoURL: https://github.com/rh-ai-quickstart/lemonade-stand-assistant.git
      path: chart
      chartVersion: main
    lemonade-stand-secrets:
      name: lemonade-stand-secrets
      namespace: lemonade-stand
      path: charts/lemonade-stand-secrets
```

What each block contributed:

| Block | Subscriptions | Namespaces | ArgoCD apps |
|---|---|---|---|
| `platform` (ai-platform-foundation) | rhoai, serverless, servicemesh | redhat-ods-operator, openshift-serverless | dsc |
| `gpu` (gpu-compute) | nfd, nvidia | openshift-nfd, nvidia-gpu-operator | nfd, nvidia-config |
| `llm`, `hap-detector`, `prompt-injection-detector` (model-serving) | — | lemonade-stand (with OAI labels) | lemonade-stand (upstream QS) |
| `model-storage` (object-storage) | — | — | (part of upstream chart) |
| `guardrails` (guardrails-orchestrator) | — | — | (part of upstream chart) |
| Framework | openshift-external-secrets | vault, external-secrets-operator, external-secrets | vault, openshift-external-secrets, lemonade-stand-secrets |

### values-secret.yaml.template

The 3 Vault secrets declared across the blocks:

```yaml
version: '2.0'
secrets:
- name: llm
  vaultPrefixes: [hub]
  fields:
  - name: vllm-api-key
    onMissingValue: prompt
- name: model-storage
  vaultPrefixes: [hub]
  fields:
  - name: access-key
    onMissingValue: prompt
  - name: secret-key
    onMissingValue: prompt
```

The upstream chart hardcodes `THEACCESSKEY` and `THESECRETKEY` in the MinIO deployment. The spec declares them as Vault-managed secrets — an improvement the spec catches that a manual review would need to catch manually.

---

## Deploying the pattern

```bash
cd /tmp/lemonade-stand-pattern
git init && git add -A && git commit -m "Initial pattern"

# Copy and fill in secrets
cp values-secret.yaml.template ~/values-secret-lemonade-stand.yaml
# Edit the file — add real values for vllm-api-key, access-key, secret-key

oc login <cluster>
./pattern.sh make install
```

---

## Block reference

| Block type | Operators installed | Local charts generated |
|---|---|---|
| `ai-platform-foundation` | openshift-ai, serverless, servicemesh | dsc (DataScienceCluster CR) |
| `gpu-compute` | nvidia-gpu, nfd | nfd (NodeFeatureDiscovery CR), nvidia-config (ClusterPolicy) |
| `model-serving` | — | None (served by upstream QS chart or local chart per instance) |
| `object-storage` | — | None (served by upstream QS chart) |
| `guardrails-orchestrator` | — | None (served by upstream QS chart) |
| `vector-store` | — | None |
| `data-pipeline` | openshift-pipelines | None |
| `sso-auth` | — | None |

## What compose doesn't do yet

- DSC component customization from `config.dsc` — the generated DSC uses the default from the operator registry. The `trustyai: Managed` config in the spec is noted but not yet applied to the chart template.
- Per-instance local chart generation for `model-serving` — when the upstream QS chart doesn't exist or you want decomposed ArgoCD apps, this is the Phase 2 path.
- ExternalSecret template generation inside `charts/lemonade-stand-secrets/` — the chart directory structure is created but the ESO templates are a work in progress.
