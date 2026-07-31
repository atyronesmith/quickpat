# QuickPat — Planned Enhancements

Items are grouped by theme. Within each group, roughly ordered by priority.
Completed items move to CHANGES.md at release time.

---

## Spec validation (`quickpat validate-spec`)

- [x] **SV-1** Wiring `from:` / `to:` references validated against `blocks:` keys (error)
- [x] **SV-2/4** `{{ blocks.X.* }}` template expressions in inputs, config, custom env (error)
- [x] **SV-3** Wiring `via:` is non-empty (warning)
- [x] **SV-5** `custom.*.source.chart` path exists on disk when `spec_dir` is known (warning)
- [x] **SV-6** Secrets with no `fields:` entries (warning)
- [x] **SV-7** `vault_path` last segment matches secret name by convention (warning)
- [x] **SV-8** Duplicate secret names in top-level `secrets:` (error)
- [x] **SV-9** Doc `source` files exist on disk when `spec_dir` is known (warning)
- [x] **SV-10** Doc marker balance — unclosed / stray `<!-- vp-only -->` / `<!-- qs-only -->` (error)
- [x] **SV-11** Unknown block type — "did you mean X?" suggestion using fuzzy matching (warning)
- [x] **SV-12** Block-level secrets conflict with `pattern-secrets` custom chart (warning)
- [x] **SV-13** `vm-workspace` block without `openshift-virtualization` (error)
- [x] **SV-14** `keycloak-oidc` + `vm-workspace` present but not wired (warning)
- [x] **SV-15** `vault: enabled: true` with no `secrets:` declared anywhere (warning)
- [x] `quickpat validate-spec <spec.yaml>` CLI subcommand with `--json` and `--strict` flags
- [x] `validate_spec` called automatically on `quickpat compose` — errors abort, warnings print
- [x] `ValidationResult` / `Issue` reused from `validator.py` (no new reporting infra)
- [ ] `quickpat lint spec.yaml` — alias for `validate-spec` (more discoverable name)

## CI/CD templates

- [x] `quickpat/templates/ci/compose.yml` — hardened GitHub Actions workflow template
- [x] `quickpat init-ci` command — installs the template into a spec repo
- [x] Drift detection: fail PR if `vp-out/` or `qs-out/` are stale after spec change
- [x] QS lint and kubeconform steps (was missing entirely)
- [x] Doc link checker on `docs/*.md` (pure-Python, no extra binary needed)
- [x] Image tag check — warns on `:latest` tags in generated charts
- [ ] Security scan (Checkov / trivy) on generated manifests
- [ ] `quickpat upgrade-ci` — re-run `init-ci --force` and show a diff of what changed

## Target versioning (`--target platform=version`)

- [x] `quickpat/compose/version_registry.py` — RHOAI version → operator channels + DSC defaults
  - RHOAI 2.25 (last 2.x; no cert-manager/jobset deps)
  - RHOAI 3.0 (added llamastackoperator; hard break from 2.x)
  - RHOAI 3.4 (added mlflowoperator DSC component)
  - RHOAI 3.5 (current GA; stable-3.5 channel)
- [x] `TargetSpec` dataclass and `target:` field on `ApplicationSpec`
- [x] `--target platform=version` CLI flag on `compose` (overrides spec `target:`)
- [x] Version-aware channel resolution in `_build_subscriptions()` and `compile_spec()`
- [x] `installPlanApproval: Manual` emitted when target version is pinned
- [x] Cert Manager and Jobset Operator added as co-dependencies for RHOAI 3.x targets
- [x] `UPGRADE_BREAKING_CHANGES` registry (2.25→3.0 blocking; 3.3→3.4 warning)
- [ ] `--target old..new` upgrade path: generates `upgrade/vX.Y-to-vA.B/RUNBOOK.md`
  - Pre-upgrade checklist (blocking changes must be resolved first)
  - Changed subscriptions (channel bumps, new operators)
  - DSC component diff
  - Post-upgrade verification steps
- [ ] `startingCSV` emitted alongside `installPlanApproval: Manual` for exact version pinning
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
