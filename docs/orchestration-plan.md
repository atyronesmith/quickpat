# Orchestration & Deployment Plan

## The Question

Should we adopt an agentic workflow framework, and how do we bridge
CLI standalone → local containers (podman) → OpenShift?

## Honest Assessment of QuickPat's Needs

QuickPat's workflow is **mostly deterministic** with 3 narrow LLM touch points:

1. Operator detection (one LLM call)
2. Secret review (one LLM call)
3. Validation review (one LLM call)

There are no agent loops, no multi-turn reasoning, no tool calling, no
memory management. The LLM is a function call, not an agent. This is
important because most agentic frameworks are designed for problems we
don't have.

## Framework Landscape (Red Hat Ecosystem)

### Llama Stack (Red Hat AI / OpenShift AI)

Red Hat's strategic bet. Provides a unified AI runtime (inference + RAG +
agents) with a Responses API compatible with OpenAI. Tech Preview in
OpenShift AI 2.25. Includes tool orchestration and the Responses API for
agentic workflows.

**Fit for QuickPat:** Llama Stack is an *inference server*, not a workflow
engine. We'd use it as the LLM backend (replacing our direct API calls)
but it doesn't orchestrate our analyze→generate→validate pipeline. Good
for the "serve a model on OpenShift" part, not for "run the pipeline."

### BeeAI Framework (IBM / Linux Foundation)

Open source multi-agent framework from IBM Research. Python + TypeScript,
Apache 2.0. Supports MCP, A2A protocols. Declarative YAML orchestration.
Kubernetes Helm deployment. Active development (v0.1.78, Feb 2026).

**Fit for QuickPat:** Designed for multi-agent collaboration where agents
reason, call tools, and coordinate. QuickPat doesn't need agents — it
needs a pipeline with optional LLM enhancement. BeeAI would be
over-engineering: we'd be wrapping 3 simple LLM calls in an agent
framework. The dependency footprint is also large.

### Kagenti (Red Hat Emerging Tech)

Kubernetes control plane for AI agents. Provides Component CRDs, SPIFFE
identity injection, A2A protocol support. Incubation project.

**Fit for QuickPat:** Kagenti is infrastructure for *deploying and
managing* agents on K8s, not for building them. If we built QuickPat as
an A2A-compatible agent, Kagenti could deploy and secure it on OpenShift.
Worth watching but too early (v0.2.0-alpha) and assumes we're building
agents first.

### kagent (Solo.io / CNCF)

K8s-native framework for DevOps/platform AI agents. Includes MCP tools
for K8s, Helm, Argo, Prometheus. CNCF sandbox project.

**Fit for QuickPat:** Designed for cluster management agents, not for
our use case. Wrong tool.

### Tekton (Red Hat / CDF)

K8s-native CI/CD pipeline engine. Red Hat is the primary contributor
(OpenShift Pipelines). Each step runs in a container. Pipeline defined
as K8s CRDs (Task, Pipeline, PipelineRun).

**Fit for QuickPat:** Good match for the *pipeline* aspect. Each
sub-skill (analyze, detect, generate, validate) maps cleanly to a
Tekton Task. But Tekton is CI/CD plumbing — it doesn't understand LLMs,
doesn't help with the CLI story, and adds significant K8s complexity
for what is fundamentally a simple sequential pipeline.

### LangGraph (LangChain)

Graph-based agent orchestration. Popular, mature, well-documented.
LangServe for deployment. No particular Red Hat involvement.

**Fit for QuickPat:** LangGraph is powerful but designed for complex
agent graphs with conditional logic, cycles, and state management.
Our pipeline is linear (analyze → detect → generate → validate).
Using LangGraph would be like using a DAG engine for a for-loop.
Also adds heavy langchain dependencies.

## Recommendation: Don't Adopt a Framework. Containerize.

The frameworks above solve problems QuickPat doesn't have. Our pipeline
is a linear sequence of deterministic steps with 3 optional LLM calls.
No framework will simplify that — they'll complicate it.

Instead, use the **Red Hat container toolchain** to bridge the CLI →
local → OpenShift gap:

```
Phase 1: Refactor (current)    → Clean Python package
Phase 2: Containerize           → Containerfile + podman
Phase 3: K8s Job                → Same container, run as Job/CronJob
Phase 4: OpenShift integration  → Llama Stack for LLM, Tekton for CI
```

### Phase 1: Refactor the Python Package (Now)

Execute the refactoring plan (docs/refactor-plan.md):
- Move pipeline orchestration into `quickpat/pipeline.py`
- Move validation into `quickpat/validator.py`
- Move LLM adapters into `quickpat/llm.py`
- CLI delegates to pipeline

Result: `quickpat` is a clean Python package that works standalone.

### Phase 2: Containerize with Podman (Next)

Create a `Containerfile` that packages quickpat:

```dockerfile
FROM registry.access.redhat.com/ubi9/python-311:latest
COPY . /app
WORKDIR /app
RUN pip install .
ENTRYPOINT ["quickpat"]
```

Run locally:
```bash
# CLI mode (same as today)
uv run quickpat create RAG

# Container mode (same result, isolated)
podman build -t quickpat .
podman run --rm -v ./output:/output quickpat create RAG -o /output/rag-pattern

# With LLM (pass API key)
podman run --rm -e ANTHROPIC_API_KEY -v ./output:/output \
  quickpat create RAG --llm anthropic -o /output/rag-pattern
```

Key: `podman generate kube` produces K8s YAML from the same container,
giving a direct path to OpenShift.

### Phase 3: Run as a Kubernetes Job

The same container runs as a K8s Job:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: quickpat-transform-rag
spec:
  template:
    spec:
      containers:
        - name: quickpat
          image: quay.io/yourorg/quickpat:latest
          command: ["quickpat", "create", "RAG", "-o", "/output/rag-pattern"]
          env:
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: llm-credentials
                  key: anthropic-api-key
          volumeMounts:
            - name: output
              mountPath: /output
      volumes:
        - name: output
          persistentVolumeClaim:
            claimName: pattern-output
      restartPolicy: Never
```

For batch processing (all quickstarts), use a CronJob or a simple
shell loop creating Jobs.

### Phase 4: OpenShift Integration

When running on OpenShift, leverage the platform:

- **LLM backend:** Use Llama Stack / Red Hat AI Inference Server
  instead of external API calls. Configure via `quickpat.yaml`:
  ```yaml
  llm:
    provider: openai  # Llama Stack is OpenAI-compatible
    openai:
      base_url: http://llama-stack.ai-services.svc:8000/v1
      model: granite-3.1-8b-instruct
  ```

- **CI/CD pipeline:** Wrap the Job in a Tekton Task if you want it
  as part of a larger pipeline (e.g., "transform → git push →
  deploy pattern"):
  ```yaml
  apiVersion: tekton.dev/v1
  kind: Task
  metadata:
    name: quickpat-transform
  spec:
    params:
      - name: quickstart-name
    steps:
      - name: transform
        image: quay.io/yourorg/quickpat:latest
        command: ["quickpat", "create", "$(params.quickstart-name)"]
  ```

- **Git integration:** After transform, a Tekton step commits and
  pushes the pattern to a git repo, triggering ArgoCD.

- **Secrets:** Use OpenShift Secrets or Vault for LLM API keys
  (we already generate Vault configs — dogfood it).

## What About MCP / A2A?

If we want QuickPat to be *callable by other agents* (e.g., a platform
engineering agent that decides when to transform quickstarts), we could
expose it as an MCP tool server. This is a thin HTTP wrapper:

```python
# Future: quickpat as an MCP tool
@mcp.tool("transform_quickstart")
def transform(quickstart_name: str, provider: str = "none"):
    result = pipeline.transform(quickstart_name, llm=make_llm(provider))
    return result.to_dict()
```

This is additive — it doesn't change the core architecture. Add it when
there's an actual consumer. Don't build it speculatively.

## Decision Summary

| Option | Verdict | Why |
|--------|---------|-----|
| BeeAI | Skip | Multi-agent framework for a non-agent problem |
| Kagenti | Watch | Too early (alpha), assumes agents |
| kagent | Skip | Wrong domain (cluster management) |
| LangGraph | Skip | Graph engine for a linear pipeline |
| Tekton | Phase 4 | Good for CI/CD wrapping, not core orchestration |
| Llama Stack | Phase 4 | Use as LLM backend on OpenShift |
| **Containerize** | **Do this** | Podman → K8s Job → OpenShift, minimal friction |

The progression is: **Python CLI → Podman container → K8s Job →
OpenShift with Llama Stack + Tekton**. Each step reuses the same
codebase and container image. No framework adoption needed.
