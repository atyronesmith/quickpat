# Refactoring Plan: Merge Skills and App Tracks

## Problem

Two parallel pipelines exist:
- `quickpat/cli.py` → directly uses `analyzer` + `generator` (no LLM, no validation)
- `skills/transform_quickstart.py` → wraps same modules + adds LLM + validation

The CLI doesn't use the skills. The text skill is monolithic.

## Current Dependency Flow

```
skills/transform_quickstart.py ──→ quickpat/analyzer.py
                               ──→ quickpat/generator.py
                               ──→ quickpat/operators.py
                               ──→ quickpat/config.py
skills/skill_validate.py       ──→ quickpat/config.py

quickpat/cli.py                ──→ quickpat/analyzer.py (reimplements pipeline)
                               ──→ quickpat/generator.py
                               ──→ quickpat/registry.py
```

App never imports from skills (one-way dependency).

## What LLMs Actually Do

Only 3 LLM touch points in the entire codebase:
1. Operator detection — unusual dependency reasoning
2. Secret review — false positive identification
3. Validation review — semantic correctness checking

Everything else is deterministic.

## Target Architecture

```
quickpat/                # App: workflow engine + core library
  cli.py                 # CLI commands call pipeline
  pipeline.py            # NEW: orchestration (current transform() logic)
  llm.py                 # NEW: adapters moved from skills/
  analyzer.py            # Core (unchanged)
  generator.py           # Core (unchanged)
  validator.py           # MOVED from skills/skill_validate.py
  operators.py           # Core (unchanged)
  config.py              # Core (unchanged)
  registry.py            # Core (unchanged)

skills/                  # LLM-facing skills (text prompts + schemas)
  operator_detection.md  # Focused: "given deps, which operators?"
  secret_review.md       # Focused: "which secrets are false positives?"
  validation_review.md   # Focused: "is this pattern correct?"
  schemas.py             # Response schemas (shared by skills)
  transform_quickstart.md  # High-level context doc (reference)
```

## What Moves

| From | To | What |
|------|----|------|
| `skills/transform_quickstart.py` → `transform()` | `quickpat/pipeline.py` | Pipeline orchestration |
| `skills/transform_quickstart.py` → `TransformResult` | `quickpat/pipeline.py` | Result dataclass |
| `skills/transform_quickstart.py` → `make_*_llm()` | `quickpat/llm.py` | LLM adapter factories |
| `skills/skill_validate.py` → all | `quickpat/validator.py` | Validation + auto-fix |
| `skills/transform_quickstart.py` → schemas | `skills/schemas.py` | Response schemas |

## What Stays in skills/

- 3 focused LLM prompt/schema pairs
- `transform_quickstart.md` as reference documentation
- `README.md` updated to reflect new role

## CLI Changes

- `quickpat create` calls `quickpat.pipeline.transform()`
- `quickpat validate` calls `quickpat.validator.validate_and_fix()`
- LLM provider selection uses `quickpat.llm.make_llm(provider_name)`

## Migration Steps

1. Create `quickpat/llm.py` — move adapter factories
2. Create `quickpat/validator.py` — move from `skills/skill_validate.py`
3. Create `quickpat/pipeline.py` — move `transform()` + sub-skills
4. Update `quickpat/cli.py` — delegate to pipeline
5. Split `transform_quickstart.md` into 3 focused skill docs
6. Update all imports in tests + eval harness
7. Remove `skills/transform_quickstart.py` and `skills/skill_validate.py`
8. Verify all tests pass
