"""Load and validate spec YAML files for `quickpat new`."""

from pathlib import Path

import yaml

from .analyzer import ChartInfo, QuickstartAnalysis, SecretRef
from .operators import OPERATORS, resolve_co_dependencies


REQUIRED_FIELDS = ['name']

VALID_TIERS = {'sandbox', 'tested', 'maintained'}
VALID_SECRET_ACTIONS = {'prompt', 'generate'}


class SpecError(Exception):
    """Raised when a spec file is invalid."""


def load_spec(path: str) -> dict:
    """Load a spec YAML file and return the raw dict."""
    p = Path(path)
    if not p.exists():
        raise SpecError(f"Spec file not found: {path}")
    with open(p) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SpecError(f"Spec file must be a YAML mapping: {path}")
    return data


def validate_spec(spec: dict) -> list:
    """Validate a spec dict, returning a list of error strings (empty = valid)."""
    errors = []

    for field in REQUIRED_FIELDS:
        if field not in spec:
            errors.append(f"Missing required field: {field}")

    if 'tier' in spec and spec['tier'] not in VALID_TIERS:
        errors.append(f"Invalid tier '{spec['tier']}', must be one of: {sorted(VALID_TIERS)}")

    if 'charts' in spec:
        if not isinstance(spec['charts'], list) or not spec['charts']:
            errors.append("'charts' must be a non-empty list")
        else:
            for i, chart in enumerate(spec['charts']):
                if not isinstance(chart, dict):
                    errors.append(f"charts[{i}]: must be a mapping")
                    continue
                if 'name' not in chart:
                    errors.append(f"charts[{i}]: missing 'name'")
                if 'path' not in chart and 'repo' not in chart:
                    errors.append(f"charts[{i}]: must have 'path' (local) or 'repo' (external)")

    if 'operators' in spec:
        if not isinstance(spec['operators'], list):
            errors.append("'operators' must be a list")
        else:
            for op in spec['operators']:
                if op not in OPERATORS:
                    errors.append(f"Unknown operator: '{op}'. Valid: {sorted(OPERATORS.keys())}")

    if 'secrets' in spec:
        if not isinstance(spec['secrets'], list):
            errors.append("'secrets' must be a list")
        else:
            for i, s in enumerate(spec['secrets']):
                if not isinstance(s, dict):
                    errors.append(f"secrets[{i}]: must be a mapping")
                    continue
                if 'name' not in s:
                    errors.append(f"secrets[{i}]: missing 'name'")
                action = s.get('onMissingValue', 'prompt')
                if action not in VALID_SECRET_ACTIONS:
                    errors.append(
                        f"secrets[{i}]: invalid onMissingValue '{action}', "
                        f"must be one of: {sorted(VALID_SECRET_ACTIONS)}"
                    )

    return errors


def build_from_spec(spec: dict, spec_path: str = "") -> tuple:
    """Convert a validated spec dict into (QuickstartAnalysis, config).

    Returns (analysis, config) ready for PatternGenerator.
    """
    errors = validate_spec(spec)
    if errors:
        raise SpecError("Invalid spec:\n  " + "\n  ".join(errors))

    spec_dir = Path(spec_path).parent if spec_path else Path(".")

    # Build ChartInfo list
    charts = []
    for entry in spec.get('charts', []):
        ci = ChartInfo(name=entry['name'])
        if 'path' in entry:
            chart_path = Path(entry['path'])
            if not chart_path.is_absolute():
                chart_path = (spec_dir / chart_path).resolve()
            ci.chart_path = str(chart_path)
            ci.strategy = 'local'
            # Read version from Chart.yaml if available
            chart_yaml = chart_path / 'Chart.yaml'
            if chart_yaml.exists():
                with open(chart_yaml) as f:
                    chart_data = yaml.safe_load(f) or {}
                ci.version = chart_data.get('version', '0.1.0')
                ci.description = chart_data.get('description', '')
        elif 'repo' in entry:
            ci.strategy = 'external'
            ci.repo_url = entry['repo']
            ci.version = entry.get('version', '0.1.0')

        ci.group = entry.get('namespace', '')

        if 'labels' in entry:
            # If user specifies OAI labels, mark it
            if 'opendatahub.io/dashboard' in entry.get('labels', {}):
                ci.needs_oai_labels = True

        charts.append(ci)

    # Build operator list with co-deps
    raw_ops = spec.get('operators', [])
    operators = resolve_co_dependencies(set(raw_ops))

    # Build secrets
    secrets = []
    secret_config = {}
    for s in spec.get('secrets', []):
        secrets.append(SecretRef(
            name=s['name'],
            path=s['name'],
            description=s.get('description', ''),
        ))
        action = s.get('onMissingValue', 'prompt')
        if action != 'prompt':
            secret_config[s['name']] = action

    # Build analysis
    analysis = QuickstartAnalysis(
        name=spec['name'],
        version=spec.get('version', '0.1.0'),
        description=spec.get('description', ''),
        charts=charts,
        detected_operators=operators,
        detected_secrets=secrets,
    )
    if charts:
        analysis.chart_path = charts[0].chart_path or spec.get('name', '')

    # Build config
    opts = spec.get('options', {})
    vault_cfg = spec.get('vault', {})

    # Single-chart: use chart name/namespace; multi-chart: use spec name
    if len(charts) == 1:
        app_name = charts[0].name
        app_ns = charts[0].group or charts[0].name
    else:
        app_name = spec['name']
        app_ns = spec['name']

    config = {
        'pattern_name': spec['name'],
        'app_name': app_name,
        'app_namespace': app_ns,
        'operators': operators,
        'chart_strategy': 'local',  # per-chart strategy via ChartInfo
        'use_vault': vault_cfg.get('enabled', bool(secrets)),
        'tier': spec.get('tier', 'sandbox'),
        'clustergroup_version': opts.get('clustergroup_version', '0.9.*'),
    }

    if secret_config:
        config['secret_config'] = secret_config

    global_options = {}
    if 'syncPolicy' in opts:
        global_options['syncPolicy'] = opts['syncPolicy']
    if 'installPlanApproval' in opts:
        global_options['installPlanApproval'] = opts['installPlanApproval']
    if global_options:
        config['global_options'] = global_options

    return analysis, config
