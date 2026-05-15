# LLM Call Site Audit — Decision Points

Audit of all five LLM call sites in QuickPat to determine which require ML
at runtime and which can be replaced with deterministic code.

**Finding:** 4 of 5 call sites are removable. 1 is borderline and needs a
rule-extraction pass on real failure cases before deciding.

## Background

QuickPat has five optional LLM call sites in `pipeline.py`, each wrapped in
`try/except` with a deterministic fallback. The original plan assumed all five
were classifier candidates requiring trained models. This audit found that
the deterministic fallbacks are already doing the work in most cases.

## Decision Points

### 1. Operator Detection (`_llm_check_operators`, pipeline.py:782)

| Field | Value |
|---|---|
| Question | Does this Helm dependency require an operator not already detected by keyword matching? |
| Labels | Bounded list (~15 keys in `OPERATORS` dict) |
| Input features | Dependency name, version, repository URL, already-detected operators |
| Existing fallback | `analyzer._detect_operators()` — keyword matching against `OPERATORS[key]['indicators']` |

**Resolution: Remove LLM call. Expand indicator keywords.**

The indicator list in `operators.py` is finite and extensible. The LLM is only
called for dependencies that survive keyword matching, but in practice it
almost never finds anything the keywords miss. When a new operator needs
detection, add its indicators to the table — that's a one-line fix, not a
training run.

---

### 2. ArgoCD Drift Prediction (`_llm_predict_drift`, pipeline.py:742)

| Field | Value |
|---|---|
| Question | Will this Kubernetes resource type cause ArgoCD drift? |
| Labels | Per resource: `{group, kind, json_pointers[], reason}` |
| Input features | Resource group/kind, deployed operators, chart name |
| Existing fallback | `KNOWN_IGNORE_RULES` static dict — 5 entries covering Route, Notebook, DSPA, InferenceService, KnativeService |

**Resolution: Remove LLM call. Expand `KNOWN_IGNORE_RULES` as new operators are encountered.**

The label space is "which JSON pointers does this controller mutate" — that's
specific to each controller and not learnable from text features. A classifier
would need to memorize controller behavior, which is just a lookup table with
extra steps. The static table handles all known cases today. New entries come
from deployment testing (CRC validation, real cluster runs), not from
inference.

---

### 3. Secret Classification (`_llm_classify_secrets`, pipeline.py:697)

| Field | Value |
|---|---|
| Question | Is this secret field a vault credential, static config, or auto-generated? |
| Labels | `vault-secret`, `static-config`, `auto-generate` |
| Input features | Field name, subchart name, env var mappings |
| Existing fallback | `_default_classify_secrets()` — substring matching on field name (`password/token` -> auto-generate, `host/port/dbname` -> static-config, else -> vault-secret) |

**Resolution: Rule extraction pass first. Classifier only if patterns survive.**

The heuristic handles ~80% of cases. Ambiguous cases like `connection-string`
(could be static or vault) or `admin-password` vs `default-password` may
collapse to rules keyed on YAML context — adjacent fields, subchart name, env
var prefix. Run the rule-extraction exercise against real quickstart failure
cases before training anything.

If specific patterns survive the rule pass, a decision tree
(decision tree trained on LLM-labeled data, exported as pure Python via
m2cgen) is the right tool. But the 80/20 rule extraction comes first.

---

### 4. Secret Review (`_llm_review_secrets`, pipeline.py:813)

| Field | Value |
|---|---|
| Question | Are any detected secrets actually false positives? |
| Labels | Free text summary + list of false positive names |
| Input features | Secret name, file path, chart name |
| Existing fallback | Returns empty string (no-op) |

**Resolution: Remove LLM call.**

This is a report annotation, not a structural decision. The converter does not
act on the output — it's advisory text in `quickstart-analysis.md`. If we want
to suppress false positives, add patterns to `_is_secret_key()` in the
analyzer rather than training a model on the review output.

---

### 5. README Summarization (`_generate_readme`, generator.py:824)

| Field | Value |
|---|---|
| Question | Generate a human-readable README for the converted pattern |
| Labels | Free text |
| Input features | Chart name, description, operators, repo URL |
| Existing fallback | Structured template using extracted fields |

**Resolution: Already solved. No LLM needed.**

`_generate_readme()` produces deterministic, always-accurate output from
structured fields the analyzer already extracts. This is better than LLM
generation because it never hallucinates operator names or deployment
instructions. The original plan to train a small model for this was the
motivation for knowledge distillation, but the template approach made it
unnecessary for this specific use case.

---

## Hypothesized Decision Points (Rejected)

### Component Topology (hub vs edge)

The original architecture spec hypothesized a `component_topology` classifier
to decide whether components run on hub or edge clusters. This doesn't exist
as a decision in QuickPat — all quickstarts deploy to hub. There is no edge
variant to classify. If multi-site deployment becomes a feature, it's a
user-specified config in `values-region.yaml`, not an inference problem.

## Summary

| # | Call Site | Resolution | Effort |
|---|---|---|---|
| 1 | `_llm_check_operators` | Remove, expand keyword table | Small |
| 2 | `_llm_predict_drift` | Remove, expand `KNOWN_IGNORE_RULES` | Small |
| 3 | `_llm_classify_secrets` | Rule extraction pass, then decide | Medium |
| 4 | `_llm_review_secrets` | Remove | Small |
| 5 | `_generate_readme` | Already solved (template) | Done |

The project shrank from an 11-week classifier build to a focused
audit-and-prune pass, plus potentially one small classifier for secret
classification if rule extraction doesn't close the gap.

## Escape Valve

One idea worth keeping from the original spec: `conversion-hints.yaml`. A
file checked in alongside a QuickStart by its author, which the converter
reads and prefers over its own inference. This handles the genuine long tail —
idiosyncratic structures that neither rules nor classifiers would handle
well — without requiring new training data.
