"""Pattern profile — stores human decisions for replay across runs.

A profile captures the five decision types made during first-time pattern
creation (secret classification, computed fields, drift entries, overrides,
infrastructure). On subsequent runs, the profile is diffed against the
current upstream chart to determine what changed and whether human input
is needed again.
"""

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


PROFILE_DIR = '.quickpat'
PROFILE_FILE = 'profile.yaml'
CURRENT_VERSION = '1.0'


@dataclass
class SecretDecision:
    """One secret field's classification."""
    name: str
    group: str
    classification: str  # vault-secret | static-config | auto-generate
    vault_key: str
    source_path: str
    default_value: str = ""


@dataclass
class ComputedFieldDecision:
    """A derived secret field composed from other fields."""
    group: str
    field_name: str
    template: str
    source_fields: list = field(default_factory=list)


@dataclass
class DriftEntry:
    """An ignoreDifferences entry for ArgoCD."""
    group: str
    kind: str
    json_pointers: list = field(default_factory=list)
    reason: str = ""


@dataclass
class OverrideEntry:
    """A value override in the app overlay file."""
    path: str
    value: Any = None
    reason: str = ""


@dataclass
class InfraDecision:
    """Infrastructure chart inclusion decision."""
    chart_type: str  # nfd | nvidia-config | dsc
    include: bool = False
    reason: str = ""


@dataclass
class SourceFingerprint:
    """Hash-based fingerprint of the source chart at decision time."""
    chart_yaml_hash: str = ""
    values_yaml_hash: str = ""
    subchart_hashes: dict = field(default_factory=dict)
    operator_versions: dict = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class PatternProfile:
    """All human decisions for a pattern, keyed to a source fingerprint."""
    profile_version: str = CURRENT_VERSION
    source_repo_url: str = ""
    source_chart_path: str = ""
    source_fingerprint: SourceFingerprint = field(default_factory=SourceFingerprint)
    secret_decisions: list = field(default_factory=list)
    computed_fields: list = field(default_factory=list)
    drift_entries: list = field(default_factory=list)
    override_entries: list = field(default_factory=list)
    infra_decisions: list = field(default_factory=list)
    vault_prefix: str = "hub"
    secret_target_names: dict = field(default_factory=dict)


@dataclass
class ProfileDiff:
    """Result of diffing a profile against a new analysis."""
    change_level: str = "low"  # low | medium | high
    new_secrets: list = field(default_factory=list)
    removed_secrets: list = field(default_factory=list)
    new_resource_types: list = field(default_factory=list)
    changed_subcharts: list = field(default_factory=list)
    new_subcharts: list = field(default_factory=list)
    summary: str = ""


# ── Save / Load ──────────────────────────────────────────────────────


def save_profile(output_dir: str, profile: PatternProfile) -> Path:
    """Write profile to .quickpat/profile.yaml in the pattern directory."""
    profile_dir = Path(output_dir) / PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)

    data = _profile_to_dict(profile)
    path = profile_dir / PROFILE_FILE
    with open(path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return path


def load_profile(output_dir: str) -> PatternProfile | None:
    """Load profile from .quickpat/profile.yaml. Returns None if not found."""
    path = Path(output_dir) / PROFILE_DIR / PROFILE_FILE
    if not path.exists():
        return None

    with open(path) as f:
        data = yaml.safe_load(f)

    if not data or not isinstance(data, dict):
        return None

    return _dict_to_profile(data)


# ── Fingerprinting ───────────────────────────────────────────────────


def compute_fingerprint(
    chart_path: str,
    subchart_info: dict = None,
    operators: list = None,
) -> SourceFingerprint:
    """Compute a hash-based fingerprint of the source chart."""
    chart_dir = Path(chart_path)

    chart_yaml_hash = _hash_file(chart_dir / 'Chart.yaml')
    values_yaml_hash = _hash_file(chart_dir / 'values.yaml')

    subchart_hashes = {}
    if subchart_info:
        for name, info in subchart_info.items():
            subchart_hashes[name] = getattr(info, 'template_hash', '')

    operator_versions = {}
    if operators:
        for op in operators:
            if isinstance(op, str):
                operator_versions[op] = ''
            elif hasattr(op, 'version'):
                operator_versions[op.name] = op.version

    return SourceFingerprint(
        chart_yaml_hash=chart_yaml_hash,
        values_yaml_hash=values_yaml_hash,
        subchart_hashes=subchart_hashes,
        operator_versions=operator_versions,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Diffing ──────────────────────────────────────────────────────────


def diff_profile(
    profile: PatternProfile,
    new_fingerprint: SourceFingerprint,
    new_secrets: list = None,
    new_resource_types: list = None,
) -> ProfileDiff:
    """Diff a stored profile against new analysis results.

    Returns a ProfileDiff with change_level:
      - low: version bumps only, no structural changes
      - medium: new optional sub-charts or changed defaults
      - high: new secrets, new resource types, changed sub-chart templates
    """
    diff = ProfileDiff()
    old_fp = profile.source_fingerprint
    reasons = []

    # Check for new/changed sub-charts
    old_subs = set(old_fp.subchart_hashes.keys())
    new_subs = set(new_fingerprint.subchart_hashes.keys())

    added_subs = new_subs - old_subs
    if added_subs:
        diff.new_subcharts = sorted(added_subs)
        reasons.append(f"New sub-charts: {', '.join(sorted(added_subs))}")

    changed_subs = []
    for name in old_subs & new_subs:
        if old_fp.subchart_hashes[name] != new_fingerprint.subchart_hashes.get(name, ''):
            changed_subs.append(name)
    if changed_subs:
        diff.changed_subcharts = changed_subs
        reasons.append(f"Changed sub-charts: {', '.join(changed_subs)}")

    # Check for new secrets
    if new_secrets is not None:
        old_secret_keys = {
            (s.group, s.name) for s in profile.secret_decisions
        }
        for s in new_secrets:
            key = (getattr(s, 'group', ''), getattr(s, 'name', ''))
            if key not in old_secret_keys:
                diff.new_secrets.append(f"{key[0]}.{key[1]}")

    # Check for removed secrets
    if new_secrets is not None:
        new_secret_keys = set()
        for s in new_secrets:
            new_secret_keys.add((getattr(s, 'group', ''), getattr(s, 'name', '')))
        for s in profile.secret_decisions:
            if (s.group, s.name) not in new_secret_keys:
                diff.removed_secrets.append(f"{s.group}.{s.name}")

    # Check for new resource types
    if new_resource_types is not None:
        old_drift_kinds = {e.kind for e in profile.drift_entries}
        for rt in new_resource_types:
            kind = rt[1] if isinstance(rt, tuple) else rt
            if kind not in old_drift_kinds:
                diff.new_resource_types.append(kind)
        if diff.new_resource_types:
            reasons.append(f"New resource types: {', '.join(diff.new_resource_types)}")

    # Classify change level
    if diff.new_secrets or diff.changed_subcharts:
        diff.change_level = "high"
    elif diff.new_subcharts or diff.new_resource_types:
        diff.change_level = "medium"
    elif old_fp.chart_yaml_hash != new_fingerprint.chart_yaml_hash:
        diff.change_level = "low"
        reasons.append("Chart.yaml changed (version bump)")
    else:
        diff.change_level = "low"

    diff.summary = "; ".join(reasons) if reasons else "No significant changes"
    return diff


# ── Serialization Helpers ────────────────────────────────────────────


def _profile_to_dict(profile: PatternProfile) -> dict:
    """Convert profile to a plain dict for YAML serialization."""
    d = asdict(profile)
    # Ensure nested dataclasses are plain dicts
    return d


def _dict_to_profile(data: dict) -> PatternProfile:
    """Reconstruct a PatternProfile from a loaded YAML dict."""
    fp_data = data.get('source_fingerprint', {})
    fingerprint = SourceFingerprint(**fp_data) if fp_data else SourceFingerprint()

    secret_decisions = [
        SecretDecision(**s) for s in data.get('secret_decisions', [])
    ]
    computed_fields = [
        ComputedFieldDecision(**c) for c in data.get('computed_fields', [])
    ]
    drift_entries = [
        DriftEntry(**e) for e in data.get('drift_entries', [])
    ]
    override_entries = [
        OverrideEntry(**o) for o in data.get('override_entries', [])
    ]
    infra_decisions = [
        InfraDecision(**i) for i in data.get('infra_decisions', [])
    ]

    return PatternProfile(
        profile_version=data.get('profile_version', CURRENT_VERSION),
        source_repo_url=data.get('source_repo_url', ''),
        source_chart_path=data.get('source_chart_path', ''),
        source_fingerprint=fingerprint,
        secret_decisions=secret_decisions,
        computed_fields=computed_fields,
        drift_entries=drift_entries,
        override_entries=override_entries,
        infra_decisions=infra_decisions,
        vault_prefix=data.get('vault_prefix', 'hub'),
        secret_target_names=data.get('secret_target_names', {}),
    )


def _hash_file(path: Path) -> str:
    """SHA-256 hash of a file's contents. Returns empty string if missing."""
    if not path.exists():
        return ''
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_directory(path: Path) -> str:
    """SHA-256 hash of all files in a directory, sorted by name."""
    if not path.is_dir():
        return ''
    h = hashlib.sha256()
    for f in sorted(path.rglob('*')):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()
