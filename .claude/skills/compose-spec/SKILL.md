---
name: compose-spec
description: >
  Use this skill when the user asks to scaffold, generate, create, or write an
  ApplicationSpec (spec.yaml) for quickpat compose. Trigger on: "help me write
  a spec", "scaffold a spec", "create a spec.yaml", "I want to compose a new
  quickstart", "new application spec", "init a spec", or any request to start
  a new VP or QS from scratch using the compose path.
---

# compose-spec — ApplicationSpec Scaffolding Skill

## Step 1 — Read references

Before asking anything, read all three reference files:

1. `references/block-catalog.md` — all 9 block types, config fields, dependencies
2. `references/spec-format.md` — full ApplicationSpec structure and metadata fields
3. `references/example-spec.yaml` — complete annotated real-world example

These give you the knowledge needed to map user intent to correct block config.

## Step 2 — Intent interview

Ask ONE open question:

> "What does this application do? Describe it in plain terms — what model it uses, what it stores, whether it needs data ingestion, any guardrails or safety checks, and whether it needs a GPU."

From the answer, propose a block set. Example:
> "Sounds like: `ai-platform-foundation` + `model-serving` + `vector-store` + `data-pipeline`. Does that sound right, or is anything missing?"

Confirm before proceeding. Do not proceed until the block set is agreed.

## Step 3 — Block configuration

For each confirmed block, ask only the questions where the answer cannot be defaulted. Work through blocks in this order (infrastructure first, application second):

**`ai-platform-foundation`** — ask only if non-default DSC components are needed:
- Does the app use TrustyAI guardrails? (adds `trustyai: Managed`)
- Does the app use DataScience Pipelines? (adds `datasciencepipelines: Managed`)
- Otherwise use conservative defaults — do not ask.

**`gpu-compute`** — ask only:
- MIG strategy? (default: none — most users take the default, only ask if they mentioned MIG)

**`model-serving`** — always ask:
- Model name or HuggingFace ID? (required)
- GPU or CPU? (default: GPU if gpu-compute block present, else CPU)
- Runtime: vllm or custom? (default: vllm; if custom, ask for the container image)

**`object-storage`** — ask:
- Provider: minio, odf, or s3? (default: minio)
- Bucket name? (default: "data")
- Storage size? (default: 20Gi — only ask if they mention large models)

**`vector-store`** — almost never need to ask:
- Database name? (default: "vectordb" — skip unless they specify)

**`data-pipeline`** — ask:
- Schedule: manual, hourly, or daily? (no default — always ask)
- Chunk size? (default: 512 — skip unless they mention document splitting)

**`guardrails-orchestrator`** — ask:
- HAP (hate/abuse/profanity) detector? (yes/no)
- Prompt injection detector? (yes/no)
- Any other detectors?
- NOTE: automatically set `trustyai: Managed` in the ai-platform-foundation DSC config.

**`llama-stack`** — almost never need to ask:
- Port? (default: 8321 — skip unless they specify)
- NOTE: auto-injects `llamastackoperator: Managed` into DSC — do not add manually.

## Step 4 — Upstream chart

Ask:
> "Is there an existing Quickstart Helm chart this VP should wrap, or are you building from scratch with the compose path?"

If yes: ask for the git repo URL. Path defaults to `chart`, branch defaults to `main`.
If no: omit the `upstream:` section entirely.

## Step 5 — Custom components

Ask:
> "Any hand-written charts or custom containers? For example, a UI, an API gateway, or a custom preprocessor?"

If yes: ask for container image. Then ask: does it need an HTTP route exposed? Replicas (default 1)?

## Step 6 — Metadata

Infer the name from the user's description (lowercase, hyphen-separated). Confirm it.
- Tier: default `sandbox`. Only change if they say this is production-validated.
- Devices: if gpu-compute block is included and CPU fallback is also desired, use `[cpu, gpu]`. If GPU-only, use `[gpu]`. If CPU-only, use `[cpu]`.

## Step 7 — Generate

Output a complete, valid `spec.yaml` as a fenced YAML code block. It must be copy-pasteable — no placeholders, no TODOs.

Add one line after the code block:
> "Save this as `spec.yaml` in your application repo, then run `quickpat compose spec.yaml` (for VP) or `quickpat compose spec.yaml --format qs` (for a QS Helm chart)."

## Rules

- Always output a complete spec — never partial.
- Do not ask about `wiring:` — derive it from `inputs:` fields, which is what the compiler actually uses. The wiring section is descriptive only; omit it unless the user asks.
- Do not ask about `sso-auth` — it is a registered block with no generation yet; omit it.
- Do not add `secrets:` fields unless the user specifically mentions credentials they want to manage. The defaults are correct for most cases.
- Do not add every possible DSC component — only add components the application actually needs. Conservative default: `kserve: Managed` and `dashboard: Managed` only.
- For the `guardrails-orchestrator` block, always set the detector endpoint references using `{{ blocks.<name>.output.predictor_host }}` — never hardcode service names.
- When `guardrails-orchestrator` is in the block set, always set `trustyai: Managed` in the `ai-platform-foundation` DSC config, regardless of whether the user mentioned it.

## Installation note

This skill lives in the quickpat repo at `.claude/skills/compose-spec/`. To use it from a new application repo:
- Copy the directory to `~/.claude/skills/compose-spec/`, or
- Run Claude Code from the quickpat directory and then work from there.
