Subject: QuickPat improvements from Patternizer analysis + current status

Drew,

Your lemonade-stand VP and the Patternizer repo have been directly useful in tightening up QuickPat's generated output. I wanted to give you visibility into what changed and where things stand.

When I did a cross-pattern comparison of your lemonade-stand, the RAG VP, and MaaS, several convention divergences surfaced that led me to dig into the Patternizer's SKILL.md and reference.md as the canonical source of truth. That analysis identified six concrete issues in QuickPat's generator:

- **Namespace format** — QuickPat was emitting namespaces as a YAML list. SKILL.md specifies maps, and for good reason: lists override entirely across values files while maps merge. This was a subtle but real problem that would have bitten anyone layering per-cluster overrides.
- **ESO backtick escaping** — ExternalSecret template data needs backtick wrapping (`{{ ` + `` ` `` + `{{ .field }}` + `` ` `` + ` }}`) to survive Helm's template rendering. QuickPat was emitting raw `{{ .field }}` refs that would blow up at deploy time.
- **Chart paths** — Generator was using `charts/all/<name>` instead of the SKILL.md convention `charts/<name>`.
- **refreshInterval** — Was `15s`, reference.md specifies `2m0s`.
- **Secrets chart values.yaml** — Was generating an empty file. SKILL.md rule 6 says charts should be `helm template`-able standalone, which requires `secretStore` defaults and per-group key/refreshInterval stubs.
- **singleArgoCD** — Missing from values-global. Required per SKILL.md.

All six are fixed in the generator. I also added a validation layer — five deterministic checks that catch these issues in any pattern directory (not just QuickPat output), plus an expanded 21-rule LLM-enhanced review when `--llm` is passed. Three of the five checks have auto-fix support.

One other change worth mentioning: QuickPat now skips generating a `-secrets` chart entirely when a quickstart has no detected secrets. Vault infrastructure (the vault app, ESO operator) still installs, but you don't get an empty boilerplate chart with no ExternalSecret templates. This directly addresses the lemonade-stand observation about empty secrets charts for quickstarts that don't need them.

**Current status:**

QuickPat generates all 6 shortlisted quickstarts (RAG, maas-code-assistant, product-recommender, lemonade-stand, llm-cpu-serving, data-governance) on every push to main. CI validates each output with `quickpat validate` and `helm template`, then publishes to `generated/<name>` branches. 363 unit tests, all passing. The generated output now matches Patternizer conventions closely enough that the differences are in content decisions (which operators, which secrets) rather than structural format.

**A few things on the radar:**

- **CRC deployment validation** — I deployed the RAG pattern on a CRC instance and found 10 issues (ArgoCD sync, CRD ordering, resource limits). Those findings are captured but not all addressed yet. Would be useful to validate lemonade-stand on CRC as well once the ROSA cluster is available.
- **Upstream QS chart quality** — Most quickstart charts need forks for VP deployment (missing `secret.create` toggle, unquoted values, hardcoded RHOAI versions, stale annotations). This isn't a QuickPat problem per se, but it determines whether we generate local copies vs. external references. Right now QuickPat defaults to remote strategy with the assumption that forks exist.
- **`secret.create` toggle detection** — Your insight about the `secret.create` toggle being the determining factor for the secrets approach was useful. QuickPat doesn't currently detect whether an upstream chart supports this toggle, which would let it decide automatically whether a fork is needed.
- **ignoreDifferences** — Per your feedback, QuickPat no longer auto-generates ignoreDifferences. They're per-QS workarounds, not a standard convention. I checked all three manual VPs — only RAG has them (DSPA and Notebook), MaaS has one for a Grafana secret, lemonade-stand has none. QuickPat now requires explicit opt-in via spec YAML or `--ignore-differences` CLI flag.

Happy to walk through any of this in more detail. The Patternizer analysis was genuinely useful — having a canonical reference for VP conventions made the generator improvements straightforward rather than a guessing game.

Aaron
