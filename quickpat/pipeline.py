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
from .operators import OPERATORS
from .profile import (
    PatternProfile, SecretDecision, ComputedFieldDecision, DriftEntry,
    OverrideEntry, InfraDecision, save_profile, load_profile,
    compute_fingerprint, diff_profile,
)
from .providers.base import Provider
from .spec import load_spec, build_from_spec, SpecError
from .subchart import fetch_and_analyze_subcharts
from .validator import validate_and_fix, validate, ValidationResult


@dataclass
class TransformResult:
    """Result of a quickstart-to-pattern transformation."""
    success: bool
    pattern_dir: str = ""
    analysis: Optional[QuickstartAnalysis] = None
    config: Optional[dict] = None
    files_created: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    llm_decisions: list = field(default_factory=list)
    validation: Optional[ValidationResult] = None


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
    ("route.openshift.io", "Route"): [
        "/spec/host", "/spec/alternateBackends",
    ],
    ("kubeflow.org", "Notebook"): [
        "/spec", "/metadata/annotations", "/metadata/labels",
    ],
    ("datasciencepipelinesapplications.opendatahub.io", "DataSciencePipelinesApplication"): [
        "/spec",
    ],
    ("serving.kserve.io", "InferenceService"): [
        "/metadata/annotations", "/metadata/labels",
    ],
    ("serving.knative.dev", "Service"): [
        "/metadata/annotations", "/metadata/labels",
    ],
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
    return config["output_dir"]


# ── Full Pipeline ───────────────────────────────────────────────────


def transform(
    quickstart_path: str,
    output_dir: str = None,
    pattern_name: str = None,
    llm: Provider = None,
    use_vault: bool = True,
    chart_strategy: str = "remote",
    auto_fix: bool = True,
    max_fix_iterations: int = 3,
    extra_config: dict = None,
    enable_transform: bool = False,
    transform_rules: list = None,
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
            chart_output = Path(output_dir) / "charts" / "all" / ci.name
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
    result.success = True

    # Collect warnings from validation
    for issue in val_result.issues:
        if not issue.fix_applied:
            result.warnings.append(f"[{issue.severity}] {issue.file}: {issue.message}")

    result.files_created = _list_created_files(output_dir, config)

    return result


# ── Create from Spec ─────────────────────────────────────────────────


def create_from_spec(
    spec_path: str,
    output_dir: str = None,
    pattern_name: str = None,
    auto_fix: bool = True,
    max_fix_iterations: int = 3,
) -> TransformResult:
    """Create a pattern from a spec YAML file (no quickstart source needed)."""
    result = TransformResult(success=False)

    try:
        spec = load_spec(spec_path)
        analysis, config = build_from_spec(spec, spec_path)
    except SpecError as e:
        result.warnings.append(str(e))
        return result

    if pattern_name:
        config['pattern_name'] = pattern_name
    if not output_dir:
        base = Path(cfg("pattern.output_dir", "~/patterns")).expanduser()
        output_dir = str(base / config['pattern_name'])
    config['output_dir'] = output_dir
    result.pattern_dir = output_dir
    result.analysis = analysis

    # Generate
    skill_generate(analysis, config)

    # Validate
    if auto_fix:
        val_result = validate_and_fix(
            output_dir, config, max_iterations=max_fix_iterations,
        )
    else:
        val_result = validate(output_dir, config)

    result.validation = val_result
    result.config = config
    result.success = True

    for issue in val_result.issues:
        if not issue.fix_applied:
            result.warnings.append(f"[{issue.severity}] {issue.file}: {issue.message}")

    result.files_created = _list_created_files(output_dir, config)

    return result


# ── Remote Strategy Pipeline ────────────────────────────────────────


def transform_remote(
    quickstart_path: str,
    output_dir: str = None,
    pattern_name: str = None,
    llm: Provider = None,
    auto_fix: bool = True,
    max_fix_iterations: int = 3,
    extra_config: dict = None,
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
        profile_diff = diff_profile(existing_profile, new_fp)
        result.llm_decisions.append(
            f"Profile diff: {profile_diff.change_level} — {profile_diff.summary}"
        )

        if profile_diff.change_level in ("low", "medium"):
            profile = existing_profile
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

    # 8. Save profile
    save_profile(output_dir, profile)

    # 9. Validate
    if auto_fix:
        val_result = validate_and_fix(
            output_dir, config, llm, max_iterations=max_fix_iterations,
        )
    else:
        val_result = validate(output_dir, config, llm)

    result.validation = val_result
    result.success = True

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
        secrets = _llm_classify_secrets(llm, analysis, subchart_info)
        profile.secret_decisions = secrets
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
    existing_secret_keys = {(s.group, s.name) for s in existing.secret_decisions}
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
) -> list:
    """Ask LLM to classify each secret field."""
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
            ]
    except Exception:
        pass

    return _default_classify_secrets(subchart_info)


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
        charts_dir = Path(output_dir) / "charts" / "all"
        if charts_dir.is_dir():
            for d in sorted(charts_dir.iterdir()):
                if d.is_dir():
                    files.append(f"charts/all/{d.name}/")
        else:
            files.append(f"charts/all/{config.get('app_name', 'app')}/")
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
