"""Pipeline orchestration for quickpat.

Chains sub-skills: analyze -> detect -> generate -> validate/fix.
Each sub-skill can run independently or as part of the full pipeline.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .analyzer import QuickstartAnalyzer, QuickstartAnalysis
from .config import get as cfg
from .generator import PatternGenerator
from .profile import (
    PatternProfile, SecretDecision, ComputedFieldDecision, DriftEntry,
    OverrideEntry, save_profile, load_profile,
    compute_fingerprint, diff_profile,
)
from .providers.base import Provider
from .compose import load_application_spec, compile_spec, AppSpecError, ComposeError
from .compose.qs_generator import QSGenerator
from .subchart import fetch_and_analyze_subcharts
from .validator import validate_and_fix, validate, ValidationResult


@dataclass
class TransformResult:
    """Result of a quickstart-to-pattern transformation."""
    success: bool
    pattern_dir: str = ""
    analysis: Optional[QuickstartAnalysis] = None
    config: Optional[dict] = None
    files_created: list = field(default_factory=list)    # files written (new or changed)
    files_unchanged: list = field(default_factory=list)  # files skipped (content identical)
    warnings: list = field(default_factory=list)
    llm_decisions: list = field(default_factory=list)
    validation: Optional[ValidationResult] = None


def _sync_dir(src: Path, dst: Path) -> tuple:
    """Sync src → dst: copy changed files and delete files no longer in src.

    Returns (files_written, files_unchanged) as lists of str relative paths.
    files_written contains new files and files whose content changed.
    files_unchanged contains files that already existed with identical content.

    Files present in dst but absent from src are deleted so that removed
    blocks, charts, and components don't persist across compose runs and
    cause ArgoCD to deploy applications that no longer exist in the spec.
    """
    import shutil as _shutil

    files_written = []
    files_unchanged = []

    # Build the set of relative paths that src produces
    src_rels = set()
    for src_file in sorted(src.rglob('*')):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src)
        src_rels.add(rel)
        dst_file = dst / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        src_bytes = src_file.read_bytes()
        if dst_file.exists() and dst_file.read_bytes() == src_bytes:
            files_unchanged.append(str(rel))
        else:
            _shutil.copy2(src_file, dst_file)
            files_written.append(str(rel))

    # Delete files in dst that are no longer produced by src
    if dst.exists():
        for dst_file in sorted(dst.rglob('*')):
            if not dst_file.is_file():
                continue
            rel = dst_file.relative_to(dst)
            if rel not in src_rels:
                dst_file.unlink()
                # Remove empty parent directories left behind
                for parent in dst_file.parents:
                    if parent == dst:
                        break
                    try:
                        parent.rmdir()  # only succeeds if empty
                    except OSError:
                        break

    return files_written, files_unchanged


# ── Response schemas for structured output ─────────────────────────

SECRET_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "secrets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "group": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": ["vault-secret", "static-config", "auto-generate"],
                    },
                    "reason": {"type": "string"},
                    "default_value": {"type": "string"},
                },
                "required": ["name", "group", "classification"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["secrets"],
    "additionalProperties": False,
}

OVERRIDE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "overrides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "value": {},
                    "reason": {"type": "string"},
                },
                "required": ["path", "value", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overrides"],
    "additionalProperties": False,
}


# ── Static ArgoCD Ignore Rules ──────────────────────────────────────

KNOWN_IGNORE_RULES: dict[tuple[str, str], list[str]] = {
    # Empty by design. ignoreDifferences are per-QS workarounds, not a
    # standard convention. Provide them explicitly via spec YAML or
    # --ignore-differences CLI flag.
}


def _static_drift_entries(resource_types: list) -> list[DriftEntry]:
    """Return DriftEntry list from the static rules table."""
    entries = []
    for group, kind in resource_types:
        pointers = KNOWN_IGNORE_RULES.get((group, kind))
        if pointers:
            entries.append(DriftEntry(
                group=group, kind=kind,
                json_pointers=list(pointers),
                reason="known controller-mutated fields",
            ))
    return entries


# ── Sub-skill: Analyze ──────────────────────────────────────────────


def skill_analyze(quickstart_path: str) -> QuickstartAnalysis:
    """Parse quickstart Helm chart(s). Pure deterministic."""
    analyzer = QuickstartAnalyzer(quickstart_path)
    return analyzer.analyze()


# ── Sub-skill: Detect ───────────────────────────────────────────────


def skill_detect(analysis: QuickstartAnalysis) -> tuple:
    """Detect operators. Keyword matching in analyzer covers all known cases."""
    return list(analysis.detected_operators), ""


# ── Sub-skill: Generate ─────────────────────────────────────────────


def skill_generate(analysis: QuickstartAnalysis, config: dict) -> str:
    """Generate pattern files from analysis + config. Pure deterministic."""
    generator = PatternGenerator(analysis, config)
    generator.generate()
    # Surface non-fatal generator messages (e.g. missing override stubs)
    config['_generator_warnings'] = list(generator.warnings)
    return config["output_dir"]


# ── Full Pipeline ───────────────────────────────────────────────────


def transform(
    quickstart_path: str,
    output_dir: str | None = None,
    pattern_name: str | None = None,
    llm: Provider | None = None,
    use_vault: bool = True,
    chart_strategy: str = "remote",
    auto_fix: bool = True,
    max_fix_iterations: int = 3,
    extra_config: dict | None = None,
    enable_transform: bool = False,
    transform_rules: list | None = None,
) -> TransformResult:
    """Full pipeline: analyze -> detect -> generate -> validate/fix."""
    result = TransformResult(success=False)

    # 1. Analyze
    try:
        analysis = skill_analyze(quickstart_path)
        result.analysis = analysis
    except FileNotFoundError as e:
        result.warnings.append(str(e))
        return result

    # 2. Resolve names
    if not pattern_name:
        pattern_name = analysis.name
    if not output_dir:
        base = Path(cfg("pattern.output_dir", "~/patterns")).expanduser()
        output_dir = str(base / pattern_name)
    result.pattern_dir = output_dir

    # 3. Detect operators (keyword matching)
    operators, _ = skill_detect(analysis)

    # 3b. Predict ArgoCD drift from static rules
    drift_entries = _static_drift_entries(analysis.resource_types)

    # 4. Build config
    config = {
        "pattern_name": pattern_name,
        "app_name": analysis.name,
        "app_namespace": analysis.name,
        "operators": operators,
        "chart_strategy": chart_strategy,
        "use_vault": use_vault,
        "output_dir": output_dir,
        "clustergroup_version": cfg("pattern.clustergroup_version", "0.9.*"),
    }
    if drift_entries:
        config['ignore_differences'] = [
            {'group': d.group, 'kind': d.kind, 'jsonPointers': d.json_pointers}
            for d in drift_entries
        ]
    if extra_config:
        config.update(extra_config)
    result.config = config

    # 5. Generate
    skill_generate(analysis, config)

    # 5b. Transform charts (optional Layer 2 rewrites)
    if enable_transform and chart_strategy == "local":
        from .transformer import transform_chart as tx_chart
        for ci in analysis.charts:
            chart_output = Path(output_dir) / "charts" / ci.name
            if chart_output.is_dir():
                tx_result = tx_chart(
                    str(chart_output), analysis, ci,
                    rules=transform_rules,
                )
                result.warnings.extend(tx_result.warnings)
                if tx_result.rules_applied:
                    result.llm_decisions.append(
                        f"Chart transforms applied to {ci.name}: "
                        f"{', '.join(tx_result.rules_applied)}"
                    )

    # 6. Validate (with optional LLM + auto-fix loop)
    if auto_fix:
        val_result = validate_and_fix(
            output_dir, config, llm, max_iterations=max_fix_iterations,
        )
    else:
        val_result = validate(output_dir, config, llm)

    result.validation = val_result
    result.success = val_result.valid

    # Collect warnings from validation
    for issue in val_result.issues:
        if not issue.fix_applied:
            result.warnings.append(f"[{issue.severity}] {issue.file}: {issue.message}")

    result.files_created = _list_created_files(output_dir, config)

    return result


# ── Compose from ApplicationSpec ────────────────────────────────────


def compose_from_spec(
    spec_path: str,
    output_dir: str | None = None,
    pattern_name: str | None = None,
    auto_fix: bool = True,
    max_fix_iterations: int = 3,
    create_service_account: bool = True,
    cli_target=None,
) -> TransformResult:
    """Compile an ApplicationSpec YAML into a Validated Pattern directory.

    When output_dir is not specified, writes into vp-out/ inside the same
    directory as spec_path (the application repo). This keeps the VP output
    co-located with the source so ArgoCD can watch a single repo.
    """
    result = TransformResult(success=False)

    try:
        spec = load_application_spec(spec_path)
    except AppSpecError as e:
        result.warnings.append(str(e))
        return result

    spec_dir = str(Path(spec_path).resolve().parent)
    if not output_dir:
        output_dir = str(Path(spec_dir) / 'vp-out')
    if pattern_name:
        spec.name = pattern_name

    # Spec-level semantic validation — runs before generation.
    # Error-severity issues abort; warnings are surfaced but do not block.
    from .compose.spec_validator import validate_spec as _validate_spec
    spec_val = _validate_spec(spec, spec_dir=spec_dir)
    for issue in spec_val.issues:
        prefix = '[spec:error]' if issue.severity == 'error' else '[spec:warning]'
        result.warnings.append(f"{prefix} {issue.file}: {issue.message}")
    if not spec_val.valid:
        return result  # success remains False

    try:
        analysis, config = compile_spec(
            spec, output_dir, spec_dir=spec_dir,
            create_service_account=create_service_account,
            cli_target=cli_target,
        )
    except ComposeError as e:
        result.warnings.append(str(e))
        return result

    result.pattern_dir = output_dir
    result.analysis = analysis
    result.config = config

    # Generate to a temp directory, then sync only changed files to output_dir.
    # This prevents unnecessary git churn when only a subset of files changed
    # (e.g. docs/ update should not touch values-prod.yaml).
    import tempfile
    with tempfile.TemporaryDirectory() as _tmp:
        tmp_out = Path(_tmp) / 'out'
        tmp_config = {**config, 'output_dir': str(tmp_out)}

        skill_generate(analysis, tmp_config)

        if auto_fix:
            val_result = validate_and_fix(
                str(tmp_out), tmp_config, max_iterations=max_fix_iterations,
            )
        else:
            val_result = validate(str(tmp_out), tmp_config)

        if val_result.valid:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            files_written, files_unchanged = _sync_dir(tmp_out, Path(output_dir))
        else:
            files_written, files_unchanged = [], []

    result.validation = val_result
    result.success = val_result.valid
    result.files_created = files_written
    result.files_unchanged = files_unchanged

    for w in tmp_config.get('_generator_warnings', []):
        result.warnings.append(w)

    for issue in val_result.issues:
        if not issue.fix_applied:
            result.warnings.append(f"[{issue.severity}] {issue.file}: {issue.message}")

    return result


def compose_upgrade_from_spec(
    spec_path: str,
    platform: str,
    from_version: str,
    to_version: str,
    output_dir: str | None = None,
) -> TransformResult:
    """Generate an upgrade runbook for upgrading a spec repo between versions.

    Output goes to <output_dir>/<platform>-v<from>-to-v<to>/RUNBOOK.md.
    Default output_dir: upgrade/ inside the spec repo directory.
    """
    result = TransformResult(success=False)

    try:
        spec = load_application_spec(spec_path)
    except AppSpecError as e:
        result.warnings.append(str(e))
        return result

    spec_dir = str(Path(spec_path).resolve().parent)
    if not output_dir:
        output_dir = str(Path(spec_dir) / 'upgrade')

    from .compose.upgrade_generator import generate_upgrade_runbook
    try:
        runbook_path = generate_upgrade_runbook(
            spec=spec,
            platform=platform,
            from_version=from_version,
            to_version=to_version,
            output_dir=Path(output_dir),
            spec_dir=spec_dir,
        )
    except (ValueError, OSError) as e:
        result.warnings.append(str(e))
        return result

    result.success = True
    result.pattern_dir = str(runbook_path.parent)
    result.files_created = [str(runbook_path.name)]
    return result


def compose_qs_from_spec(
    spec_path: str,
    output_dir: str | None = None,
    pattern_name: str | None = None,
    create_service_account: bool = True,
    cli_target=None,
) -> TransformResult:
    """Compile an ApplicationSpec YAML into a self-contained QS Helm chart.

    When output_dir is not specified, writes into qs-out/ inside the same
    directory as spec_path (the application repo).
    """
    result = TransformResult(success=False)

    try:
        spec = load_application_spec(spec_path)
    except AppSpecError as e:
        result.warnings.append(str(e))
        return result

    spec_dir = str(Path(spec_path).resolve().parent)
    if not output_dir:
        output_dir = str(Path(spec_dir) / 'qs-out')
    if pattern_name:
        spec.name = pattern_name

    # Spec-level semantic validation — runs before generation.
    # Error-severity issues abort; warnings are surfaced but do not block.
    from .compose.spec_validator import validate_spec as _validate_spec
    spec_val = _validate_spec(spec, spec_dir=spec_dir)
    for issue in spec_val.issues:
        prefix = '[spec:error]' if issue.severity == 'error' else '[spec:warning]'
        result.warnings.append(f"{prefix} {issue.file}: {issue.message}")
    if not spec_val.valid:
        return result  # success remains False

    try:
        _, config = compile_spec(
            spec, output_dir, spec_dir=spec_dir,
            create_service_account=create_service_account,
            cli_target=cli_target,
        )
    except ComposeError as e:
        result.warnings.append(str(e))
        return result

    result.pattern_dir = output_dir
    result.config = config

    import tempfile
    with tempfile.TemporaryDirectory() as _tmp:
        tmp_out = Path(_tmp) / 'out'
        gen = QSGenerator(spec, config, tmp_out)
        gen.generate()

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        files_written, files_unchanged = _sync_dir(tmp_out, Path(output_dir))

    result.success = True
    result.files_created = files_written
    result.files_unchanged = files_unchanged
    return result


# ── Remote Strategy Pipeline ────────────────────────────────────────


def transform_remote(
    quickstart_path: str,
    output_dir: str | None = None,
    pattern_name: str | None = None,
    llm: Provider | None = None,
    auto_fix: bool = True,
    max_fix_iterations: int = 3,
    extra_config: dict | None = None,
    force: bool = False,
) -> TransformResult:
    """Remote strategy pipeline: analyze -> fetch sub-charts -> decide -> generate -> profile."""
    result = TransformResult(success=False)

    # 1. Analyze
    try:
        analysis = skill_analyze(quickstart_path)
        result.analysis = analysis
    except FileNotFoundError as e:
        result.warnings.append(str(e))
        return result

    # 2. Detect git origin
    analyzer = QuickstartAnalyzer(quickstart_path)
    git_url, chart_path_in_repo = analyzer.detect_git_origin()

    # 3. Resolve names
    if not pattern_name:
        pattern_name = analysis.name
    if not output_dir:
        base = Path(cfg("pattern.output_dir", "~/patterns")).expanduser()
        output_dir = str(base / pattern_name)
    result.pattern_dir = output_dir

    # 4. Fetch and analyze sub-charts
    subchart_info = {}
    if analysis.dependencies:
        subchart_info = fetch_and_analyze_subcharts(analysis.dependencies)

    # 5. Check for existing profile
    existing_profile = load_profile(output_dir)
    profile = None

    if existing_profile:
        new_fp = compute_fingerprint(
            analysis.chart_path or quickstart_path,
            subchart_info=subchart_info,
            operators=list(analysis.detected_operators),
        )
        # Detect secret-field drift the fingerprint alone can miss so update
        # does not skip a stale pattern. Resource types are not passed here:
        # drift_entries are usually empty (KNOWN_IGNORE_RULES is empty), so
        # every kind would look "new" and defeat the unchanged skip.
        detected_secrets = _default_classify_secrets(subchart_info)
        profile_diff = diff_profile(
            existing_profile, new_fp,
            new_secrets=detected_secrets,
        )
        result.llm_decisions.append(
            f"Profile diff: {profile_diff.change_level} — {profile_diff.summary}"
        )

        if not force and profile_diff.unchanged:
            result.warnings.append(
                "No upstream changes detected; skipping regeneration "
                "(use --force to regenerate anyway)"
            )
            if auto_fix:
                val_result = validate_and_fix(
                    output_dir, None, llm, max_iterations=max_fix_iterations,
                )
            else:
                val_result = validate(output_dir, None, llm)
            result.validation = val_result
            result.success = val_result.valid
            for issue in val_result.issues:
                if not issue.fix_applied:
                    result.warnings.append(
                        f"[{issue.severity}] {issue.file}: {issue.message}"
                    )
            return result

        if profile_diff.change_level in ("low", "medium"):
            # Reuse prior decisions but refresh the fingerprint so the next
            # update can correctly detect "unchanged".
            profile = existing_profile
            profile.source_fingerprint = new_fp
        else:
            profile = _rebuild_profile(
                existing_profile, analysis, subchart_info, llm,
                result, git_url, chart_path_in_repo,
            )
    else:
        profile = _build_new_profile(
            analysis, subchart_info, llm, result,
            git_url, chart_path_in_repo,
        )

    # 6. Build config from profile
    operators, _ = skill_detect(analysis)
    config = _profile_to_config(
        profile, analysis, operators, output_dir, pattern_name,
    )
    if extra_config:
        config.update(extra_config)
    result.config = config

    # 7. Generate
    skill_generate(analysis, config)

    # 8. Update profile with user-provided overrides before saving
    if extra_config:
        if extra_config.get('git_repo_url'):
            profile.source_repo_url = extra_config['git_repo_url']
        if 'chart_path_in_repo' in extra_config:
            profile.source_chart_path = extra_config['chart_path_in_repo']

    save_profile(output_dir, profile)

    # 9. Validate
    if auto_fix:
        val_result = validate_and_fix(
            output_dir, config, llm, max_iterations=max_fix_iterations,
        )
    else:
        val_result = validate(output_dir, config, llm)

    result.validation = val_result
    result.success = val_result.valid

    for issue in val_result.issues:
        if not issue.fix_applied:
            result.warnings.append(f"[{issue.severity}] {issue.file}: {issue.message}")

    result.files_created = _list_created_files(output_dir, config)
    return result


def _build_new_profile(
    analysis, subchart_info, llm, result,
    git_url, chart_path_in_repo,
) -> PatternProfile:
    """Build a profile from scratch using LLM decisions."""
    profile = PatternProfile(
        source_repo_url=git_url,
        source_chart_path=chart_path_in_repo,
    )

    # Classify secrets
    if llm and subchart_info:
        secrets, llm_warning = _llm_classify_secrets(llm, analysis, subchart_info)
        profile.secret_decisions = secrets
        if llm_warning:
            result.warnings.append(llm_warning)
        else:
            result.llm_decisions.append(
                f"Classified {len(secrets)} secrets via LLM"
            )
    else:
        profile.secret_decisions = _default_classify_secrets(subchart_info)

    # Computed fields from sub-chart analysis
    for sc_name, sc_info in subchart_info.items():
        for cf in sc_info.computed_fields:
            profile.computed_fields.append(ComputedFieldDecision(
                group=sc_name,
                field_name=cf.name,
                template=cf.template,
                source_fields=cf.source_fields,
            ))

    # Predict drift from static rules
    resource_types = list(analysis.resource_types)
    for sc_info in subchart_info.values():
        for rt in sc_info.resource_types:
            if rt not in resource_types:
                resource_types.append(rt)
    drift = _static_drift_entries(resource_types)
    if drift:
        profile.drift_entries = drift

    # Build overrides from secret gates
    overrides = []
    for sc_name, sc_info in subchart_info.items():
        for gate in sc_info.secret_gates:
            overrides.append(OverrideEntry(
                path=f"{sc_name}.{gate.condition_path}",
                value=False,
                reason="Secrets managed by secrets chart",
            ))
    profile.override_entries = overrides

    # Build secret target names from sub-chart info
    for sc_name, sc_info in subchart_info.items():
        for gate in sc_info.secret_gates:
            if gate.k8s_secret_name:
                profile.secret_target_names[sc_name] = gate.k8s_secret_name
                break
        else:
            if sc_info.env_secret_refs:
                first_ref = next(iter(sc_info.env_secret_refs.values()))
                profile.secret_target_names[sc_name] = first_ref[0]

    # Fingerprint
    profile.source_fingerprint = compute_fingerprint(
        analysis.chart_path or '',
        subchart_info=subchart_info,
        operators=list(analysis.detected_operators),
    )

    return profile


def _rebuild_profile(
    existing, analysis, subchart_info, llm, result,
    git_url, chart_path_in_repo,
) -> PatternProfile:
    """Rebuild a profile, keeping unchanged decisions and re-prompting for changes."""
    new_profile = _build_new_profile(
        analysis, subchart_info, llm, result,
        git_url, chart_path_in_repo,
    )
    # Carry forward unchanged decisions from existing profile
    for s in existing.secret_decisions:
        key = (s.group, s.name)
        new_keys = {(ns.group, ns.name) for ns in new_profile.secret_decisions}
        if key in new_keys:
            # Replace with existing decision (user already classified this)
            new_profile.secret_decisions = [
                ns if (ns.group, ns.name) != key else s
                for ns in new_profile.secret_decisions
            ]

    return new_profile


def _profile_to_config(
    profile, analysis, operators, output_dir, pattern_name,
) -> dict:
    """Convert a PatternProfile into generator config."""
    # Build secret_groups from profile decisions
    secret_groups = {}
    for sd in profile.secret_decisions:
        group = secret_groups.setdefault(sd.group, [])
        group.append({
            'name': sd.name,
            'classification': sd.classification,
            'default_value': sd.default_value,
        })

    # Add computed fields to their groups
    for cf in profile.computed_fields:
        group = secret_groups.setdefault(cf.group, [])
        group.append({
            'name': cf.field_name,
            'computed': True,
            'template': cf.template,
            'source_fields': cf.source_fields,
        })

    # Build override entries
    override_entries = [
        {'path': o.path, 'value': o.value}
        for o in profile.override_entries
    ]

    # Build ignore differences from drift entries
    ignore_differences = [
        {
            'group': d.group,
            'kind': d.kind,
            'jsonPointers': d.json_pointers,
        }
        for d in profile.drift_entries
    ]

    # Build extra value files
    app_name = analysis.name
    extra_value_files = []
    if override_entries:
        extra_value_files.append(f'/overrides/{app_name}.yaml')

    config = {
        'pattern_name': pattern_name,
        'app_name': app_name,
        'app_namespace': analysis.name,
        'operators': operators,
        'chart_strategy': 'remote',
        'use_vault': True,
        'output_dir': output_dir,
        'clustergroup_version': cfg("pattern.clustergroup_version", "0.9.*"),
        'git_repo_url': profile.source_repo_url,
        'chart_path_in_repo': profile.source_chart_path,
        'chart_branch': 'main',
        'vault_prefix': profile.vault_prefix,
        'secret_groups': secret_groups,
        'secret_target_names': profile.secret_target_names,
        'override_entries': override_entries,
        'extra_value_files': extra_value_files or None,
        'ignore_differences': ignore_differences or None,
    }

    return config


def _classify_secret_field(field_name: str) -> str:
    """Classify a single secret field by name pattern."""
    fn = field_name.lower().replace('-', '_')

    # Externally-issued credentials: check before auto-generate patterns
    # because these contain "secret"/"token"/"key" but aren't auto-generated
    credential_compounds = {
        'access_key', 'secret_key', 'secret_access',
        'credentials', 'credential',
    }
    if any(c in fn for c in credential_compounds):
        return 'vault-secret'
    # Service-prefixed tokens (HF_TOKEN, GOOGLE_TOKEN, etc.)
    if fn.endswith('_token') and '_' in fn:
        return 'vault-secret'

    # Auto-generated: passwords and keys the system creates
    autogen_patterns = {'password', 'passwd'}
    if any(p in fn for p in autogen_patterns):
        return 'auto-generate'

    # Static config: infrastructure settings with sensible defaults
    config_patterns = {
        'host', 'port', 'dbname', 'database', 'user', 'username', 'endpoint',
        'url', 'source', 'model', 'version', 'bucket', 'region',
        'schema', 'mode', 'service', 'name', 'namespace', 'connection',
    }
    if any(p in fn for p in config_patterns):
        return 'static-config'

    return 'vault-secret'


def _default_classify_secrets(subchart_info) -> list:
    """Classify secrets without LLM — uses field name heuristics."""
    decisions = []
    for sc_name, sc_info in subchart_info.items():
        for field_name in sc_info.secret_fields:
            decisions.append(SecretDecision(
                name=field_name,
                group=sc_name,
                classification=_classify_secret_field(field_name),
                vault_key=field_name,
                source_path=f"{sc_name}.secret.{field_name}",
            ))
    return decisions


# ── LLM helpers ─────────────────────────────────────────────────────


def _llm_classify_secrets(
    llm: Provider,
    analysis: QuickstartAnalysis,
    subchart_info: dict,
) -> tuple[list, str | None]:
    """Ask LLM to classify each secret field.

    Returns (decisions, warning_or_none).  When the LLM call fails the
    fallback is heuristic classification and warning_or_none carries the
    reason string so callers can surface it.
    """
    fields_desc = []
    for sc_name, sc_info in subchart_info.items():
        for field_name in sc_info.secret_fields:
            fields_desc.append(f"  - {sc_name}.{field_name}")
        for env_var, (secret_name, key) in sc_info.env_secret_refs.items():
            fields_desc.append(f"    (consumed as env {env_var} from secret {secret_name})")

    system = (
        "You are a Kubernetes secrets expert. Classify each secret field into one of:\n"
        "- vault-secret: Real credential (API tokens, passwords users must provide)\n"
        "- static-config: Infrastructure config with a sensible default (host, port, db name)\n"
        "- auto-generate: Password/key that should be randomly generated\n"
        "Include a default_value for static-config fields."
    )
    user = (
        f"Chart: {analysis.name}\n"
        f"Secret fields:\n" + "\n".join(fields_desc)
    )

    try:
        response = llm.complete(system, user, response_schema=SECRET_CLASSIFICATION_SCHEMA)
        result = response.parsed if response.parsed else response.content
        if isinstance(result, dict):
            return [
                SecretDecision(
                    name=s['name'], group=s['group'],
                    classification=s['classification'],
                    vault_key=s['name'],
                    source_path=f"{s['group']}.secret.{s['name']}",
                    default_value=s.get('default_value', ''),
                )
                for s in result.get('secrets', [])
            ], None
    except Exception as e:
        return _default_classify_secrets(subchart_info), (
            f"LLM secret classification failed ({e}); using heuristic defaults"
        )

    return _default_classify_secrets(subchart_info), None


def _list_created_files(output_dir: str, config: dict) -> list:
    files = [
        "values-global.yaml",
        f"values-{config.get('cluster_group_name', 'prod')}.yaml",
        "Makefile",
        "Makefile-common",
        "pattern.sh",
        "pattern-metadata.yaml",
        "ansible.cfg",
        ".ansible-lint",
        ".gitignore",
        "docs/quickstart-analysis.md",
    ]
    if config.get("use_vault"):
        files.append("values-secret.yaml.template")
    if config.get("chart_strategy") == "local":
        charts_dir = Path(output_dir) / "charts"
        if charts_dir.is_dir():
            for d in sorted(charts_dir.iterdir()):
                if d.is_dir() and not d.name.endswith('-secrets'):
                    files.append(f"charts/{d.name}/")
        else:
            files.append(f"charts/{config.get('app_name', 'app')}/")
    elif config.get("chart_strategy") == "remote":
        app_name = config.get("app_name", "app")
        secrets_chart_name = f"{app_name}-secrets"
        ps_dir = Path(output_dir) / "charts" / secrets_chart_name
        if ps_dir.is_dir():
            files.append(f"charts/{secrets_chart_name}/")
        profile_path = Path(output_dir) / ".quickpat" / "profile.yaml"
        if profile_path.exists():
            files.append(".quickpat/profile.yaml")
    for platform in cfg("platforms", ["AWS", "Azure", "GCP", "IBMCloud", "None"]):
        files.append(f"overrides/values-{platform}.yaml")
    return files
