# QuickPat — Planned Enhancements

Items are grouped by theme. Within each group, roughly ordered by priority.
Completed items move to CHANGES.md at release time.

---

## Spec validation (`quickpat validate-spec`)

- [ ] **SV-1** Wiring `from:` / `to:` references validated against `blocks:` keys (error)
- [ ] **SV-2** Block `inputs:` values reference existing block names (error)
- [ ] **SV-3** Template expressions `{{ blocks.X.output.Y }}` reference existing blocks (error)
- [ ] **SV-4** Doc marker balance — unclosed `<!-- vp-only -->` / `<!-- qs-only -->` (error)
- [ ] **SV-5** Duplicate secret names in top-level `secrets:` (error)
- [ ] **SV-6** Secrets with no `fields:` entries (warning)
- [ ] **SV-7** `custom.*.source.chart` path exists on disk when `spec_dir` is known (warning)
- [ ] **SV-8** Doc `source` files exist on disk when `spec_dir` is known (warning)
- [ ] **SV-9** `vm-workspace` block without `openshift-virtualization` block (error)
- [ ] **SV-10** `vault: enabled: true` with no `secrets:` entries (suggestion)
- [ ] **SV-11** Unknown block type — "did you mean X?" suggestion (warning)
- [ ] New `quickpat validate-spec <spec.yaml>` CLI subcommand with `--json` and `--strict` flags
- [ ] Run `validate-spec` automatically on `quickpat compose` — errors abort, warnings print
- [ ] `ValidationResult` / `Issue` reused from `validator.py` (no new reporting infra)

## CI/CD templates

- [ ] `quickpat/templates/ci/compose.yml` — hardened GitHub Actions workflow template
- [ ] `quickpat init-ci` command — copies the template into a spec repo
- [ ] Drift detection step: fail PR if `vp-out/` or `qs-out/` are stale after spec change
- [ ] QS lint and kubeconform steps (currently only VP is validated in CI)
- [ ] Doc link checker (`lychee`) on `docs/*.md`
- [ ] Image tag check — warn on `:latest` tags in generated charts
- [ ] Security scan (Checkov / trivy) on generated manifests

## Target versioning (`--target platform=version`)

- [ ] `quickpat/compose/version_registry.py` — RHOAI version → operator channels + DSC defaults
  - RHOAI 2.25 (last 2.x; no cert-manager/jobset deps)
  - RHOAI 3.0 (added llamastackoperator; hard break from 2.x)
  - RHOAI 3.4 (added mlflowoperator DSC component)
  - RHOAI 3.5 (current GA; stable-3.5 channel)
- [ ] `TargetSpec` dataclass and `target:` field on `ApplicationSpec`
- [ ] `--target platform=version` CLI flag on `compose` (overrides spec `target:`)
- [ ] Version-aware channel resolution in `_build_subscriptions()` and `compile_spec()`
- [ ] `installPlanApproval: Manual` + `startingCSV` emitted when target version is pinned
- [ ] Cert Manager and Jobset Operator added as co-dependencies for RHOAI 3.x targets
- [ ] `UPGRADE_BREAKING_CHANGES` registry (2.25→3.0 blocking; 3.3→3.4 warning; etc.)
- [ ] `--target old..new` upgrade path: generates `upgrade/vX.Y-to-vA.B/RUNBOOK.md`
  - Pre-upgrade checklist (blocking changes must be resolved first)
  - Changed subscriptions (channel bumps, new operators)
  - DSC component diff
  - Post-upgrade verification steps
- [ ] Warn when spec has `ai-platform-foundation` block but no `target:` (channel defaults to `fast`)

## Block library

- [ ] `identity-provider` block (generic OIDC — Dex, Okta, Azure AD) separate from `keycloak-oidc`
- [ ] `image-registry` block (Quay, Harbor, OpenShift internal registry)
- [ ] `service-mesh` block (standalone, not just as RHOAI co-dependency)
- [ ] `kafka` block (`amq-streams` subscription + Kafka CR)
- [ ] `database` block (PostgreSQL via CrunchyData PGO or CNPG)
- [ ] `cert-manager` block (cert-manager Operator + ClusterIssuer)
- [ ] Block `outputs:` schema — compiler validates `{{ blocks.X.output.Y }}` against declared outputs

## Compose pipeline improvements

- [ ] `quickpat diff spec.yaml` — show what would change in `vp-out/` without writing
- [ ] `quickpat compose --validate-only` — parse + validate spec, no file output
- [ ] Multi-target compose: `quickpat compose spec.yaml --target rhoai=3.4 --target rhoai=3.5`
  generates two output directories for side-by-side comparison
- [ ] `custom.*.source.chart` can be a remote URL (git repo + path) not just a local path
- [ ] Wiring reference resolution — `{{ blocks.X.output.Y }}` compiled to actual K8s service names
  rather than left as template strings for the implementer to wire manually
- [ ] `pattern-metadata.yaml` provenance includes spec file hash for traceability

## QS generator improvements

- [ ] Unknown block types in QS output produce a warning (currently silently produce no output)
- [ ] QS `values.yaml` secret placeholders derived from top-level `secrets:` (currently only block secrets)
- [ ] QS `NOTES.txt` references the deployment steps from `docs/README.md` qs-only sections
- [ ] Helm dependencies support — generate `Chart.yaml` with `dependencies:` for shared charts

## Documentation pipeline

- [ ] Nested doc markers handled gracefully (currently flat boolean skip, no stack)
- [ ] `<!-- both -->` explicit marker (complementary to vp-only / qs-only for clarity)
- [ ] Multiple doc files in `docs:` — support `docs/*.md` glob
- [ ] Table of contents auto-injection for generated READMEs

## Developer experience

- [ ] `quickpat new spec.yaml` — interactive spec scaffolding wizard (guided prompts)
- [ ] `quickpat lint spec.yaml` — alias for `validate-spec` (more discoverable name)
- [ ] Shell completion (bash, zsh, fish) for all subcommands
- [ ] `--quiet` flag on `compose` — suppress file-by-file output, only print summary
- [ ] Watch mode: `quickpat compose spec.yaml --watch` — auto-regenerate on file changes

## Testing and quality

- [ ] Eval harness integration — run compose against real cluster and verify ArgoCD sync
- [ ] Fixture-based snapshot tests — commit expected `vp-out/` for reference specs; fail
  if compose output diverges (catches regressions without running a cluster)
- [ ] Property-based tests (Hypothesis) for the spec parser — random valid/invalid specs
