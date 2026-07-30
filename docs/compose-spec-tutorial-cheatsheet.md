# compose-spec Skill Tutorial — Cheat Sheet
## Subject: spending-transaction-monitor

This cheat sheet gives you the pre-researched answers for every question
the skill will ask. Use it during the tutorial so the interview flows
naturally without having to look things up.

---

## What is this application? (Phase 1 — Intent)

**One-sentence answer:**
> "An AI-powered spending transaction monitor that detects fraud and unusual
> spending in real time. It uses a LlamaStack agent to evaluate transactions
> against natural language alert rules, stores transaction embeddings in
> pgvector, and provides a React UI with Keycloak SSO."

**Block set the skill should propose (confirm these):**

| Block | Why |
|---|---|
| `ai-platform-foundation` | Always required |
| `gpu-compute` | LLM runs on GPU |
| `model-serving` | vLLM serving the LLM |
| `vector-store` | Transaction embeddings via pgvector |
| `llama-stack` | Agent layer that orchestrates LLM + retrieval |
| `object-storage` | MinIO stores model weights and ML data |

**Blocks to skip:**
- `data-pipeline` — the ML training pipeline is DSPA/KFP-based, not Tekton; skip it
- `guardrails-orchestrator` — no TrustyAI in this app
- `sso-auth` — placeholder block, no generation; Keycloak goes in custom components instead

---

## Block configuration answers (Phase 2)

### ai-platform-foundation
- **Non-default DSC components?** No — just kserve + dashboard defaults.
  Do NOT add trustyai (no guardrails) or datasciencepipelines (no Tekton pipeline).

### gpu-compute
- **MIG strategy?** `none` (default, accept it)

### model-serving
- **Model name:** `meta-llama/Llama-3.1-8B-Instruct`
- **GPU or CPU?** GPU
- **Runtime:** `vllm` (default, accept it)
- **Storage type:** OCI model car
  - URI: `oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.1-8b-instruct`

### vector-store
- **Database name:** `spending-monitor`
  (The QS uses pgvector for both transaction embeddings and LlamaStack memory)

### llama-stack
- **Port:** 8321 (default, accept it)
- **Inputs:**
  - llm → the model-serving block (name it `llm` when defining blocks)
  - vector_store → the vector-store block (name it `db` when defining blocks)

### object-storage
- **Provider:** `minio`
- **Bucket:** `models`
- **Storage size:** `20Gi` (stores ML model weights)

---

## Upstream chart (Phase 3)

**Is there an existing QS chart?** YES

| Field | Value |
|---|---|
| repo | `https://github.com/rh-ai-quickstart/spending-transaction-monitor.git` |
| path | `deploy/helm/spending-monitor` |
| branch | `main` |

---

## Custom components (Phase 4)

**Are there custom components?** YES — two: the API and the UI.
Keycloak (SSO) also goes here since `sso-auth` is a placeholder block.

| Component | Image | Port | Route? |
|---|---|---|---|
| `spending-monitor-api` | `quay.io/rh-ai-quickstart/spending-monitor-api:latest` | 8000 | yes |
| `spending-monitor-ui` | `quay.io/rh-ai-quickstart/spending-monitor-ui:latest` | 8080 | yes |

**Note:** Keycloak is a complex subchart — skip it as a custom component for now
(it's a future `sso-auth` block story). The skill may ask; answer "just the API and UI."

---

## Metadata (Phase 5)

| Field | Value |
|---|---|
| name | `spending-transaction-monitor` |
| description | `AI-powered fraud detection and spending alert system` |
| tier | `sandbox` |
| devices | `[gpu]` (GPU-only; no CPU fallback needed for a financial demo) |

---

## Architecture summary (for context)

```
[Spending Monitor UI] ─── HTTPS/Route ──→ [Spending Monitor API]
                                               │
                         ┌─────────────────────┤
                         │                     │
                    [LlamaStack]          [PostgreSQL/pgvector]
                         │                (transaction data +
                    [vLLM / KServe]        vector embeddings)
                    Llama-3.1-8B
                         │
                    [MinIO]  ← ML model weights
```

**Key design decisions:**
- LlamaStack is the agent layer — it orchestrates LLM calls against natural-language alert rules
- pgvector stores transaction embeddings for similarity-based fraud pattern retrieval
- The ML pipeline (alert-recommender) is optional and disabled by default — omit from spec
- Keycloak SSO is present but goes into custom components (sso-auth block is a placeholder)

---

## After the skill outputs the spec

1. Save to a new application repo directory as `spec.yaml`
2. Run: `quickpat compose spec.yaml`
3. Check the VP output in `vp-out/`
4. Run: `quickpat validate vp-out/`
