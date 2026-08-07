"""Semantic validation of ApplicationSpec objects.

Runs after parsing (parser.py raises AppSpecError on syntax errors) and before
compilation. Catches cross-reference errors, missing files, logical inconsistencies,
and provides actionable suggestions.

Usage:
    from quickpat.compose.spec_validator import validate_spec
    result = validate_spec(spec, spec_dir="/path/to/spec/repo")
    if not result.valid:
        # errors found — abort compose
"""

import re
from collections.abc import Iterator
from difflib import get_close_matches
from pathlib import Path

from ..validator import Issue, ValidationResult
from .blocks import BLOCK_TYPES
from .parser import ApplicationSpec

# Template expression pattern: {{ blocks.BLOCKNAME.anything }}
_BLOCK_REF = re.compile(r'\{\{[^}]*\bblocks\.([a-zA-Z0-9_-]+)\b[^}]*\}\}')

# Doc marker patterns (must match full stripped lines)
_VP_OPEN  = re.compile(r'^\s*<!--\s*vp-only\s*-->\s*$', re.IGNORECASE)
_QS_OPEN  = re.compile(r'^\s*<!--\s*qs-only\s*-->\s*$', re.IGNORECASE)
_END      = re.compile(r'^\s*<!--\s*end\s*-->\s*$',      re.IGNORECASE)


def validate_spec(spec: ApplicationSpec, spec_dir: str | None = None) -> ValidationResult:
    """Run all spec-level checks. Returns a ValidationResult.

    Error-severity issues indicate the spec cannot produce correct output.
    Warning-severity issues are actionable suggestions that don't block compose.
    """
    issues = []

    issues.extend(_check_wiring_references(spec))
    issues.extend(_check_block_template_refs(spec))
    issues.extend(_check_block_types(spec))
    issues.extend(_check_block_combos(spec))
    issues.extend(_check_block_secrets_conflict(spec))
    issues.extend(_check_secrets(spec))
    issues.extend(_check_vault(spec))
    if spec_dir:
        issues.extend(_check_chart_paths(spec, spec_dir))
        issues.extend(_check_doc_files(spec, spec_dir))

    valid = not any(i.severity == 'error' for i in issues)
    return ValidationResult(valid=valid, issues=issues)


# ── SV-1: Wiring references ──────────────────────────────────────────────────

def _check_wiring_references(spec: ApplicationSpec) -> list:
    """SV-1: wiring from/to must name blocks that exist.
    SV-3: wiring via should be non-empty (suggestion only)."""
    issues = []
    block_names = set(spec.blocks)

    for i, entry in enumerate(spec.wiring):
        loc = f"wiring[{i}]"
        if entry.from_block not in block_names:
            issues.append(Issue(
                file='spec.yaml',
                severity='error',
                message=f"{loc}: 'from: {entry.from_block}' references a block that does not exist "
                        f"(known blocks: {_fmt(block_names)})",
            ))
        if entry.to_block not in block_names:
            issues.append(Issue(
                file='spec.yaml',
                severity='error',
                message=f"{loc}: 'to: {entry.to_block}' references a block that does not exist "
                        f"(known blocks: {_fmt(block_names)})",
            ))
        if not entry.via:
            issues.append(Issue(
                file='spec.yaml',
                severity='warning',
                message=f"{loc}: 'via:' is empty — add a label describing the connection "
                        f"(e.g. 'via: oidc-jwks') to document the data flow",
            ))

    return issues


# ── SV-2 / SV-4: Template expressions reference existing blocks ──────────────

def _check_block_template_refs(spec: ApplicationSpec) -> list:
    """SV-2/4: {{ blocks.X.* }} in inputs, config values, and custom env must
    reference blocks that exist in this spec."""
    issues = []
    block_names = set(spec.blocks)

    def _scan(value: str, location: str):
        for match in _BLOCK_REF.finditer(value):
            ref = match.group(1)
            if ref not in block_names:
                issues.append(Issue(
                    file='spec.yaml',
                    severity='error',
                    message=f"{location}: template references '{{{{ blocks.{ref}.* }}}}' "
                            f"but block '{ref}' does not exist "
                            f"(known blocks: {_fmt(block_names)})",
                ))

    for block_name, block in spec.blocks.items():
        for key, val in block.inputs.items():
            if isinstance(val, str):
                _scan(val, f"blocks.{block_name}.inputs.{key}")
        for key, val in _flatten(block.config):
            if isinstance(val, str):
                _scan(val, f"blocks.{block_name}.config.{key}")

    for comp_name, comp in spec.custom.items():
        for key, val in comp.env.items():
            if isinstance(val, str):
                _scan(val, f"custom.{comp_name}.env.{key}")

    return issues


# ── SV-11: Unknown block types — "did you mean?" ─────────────────────────────

def _check_block_types(spec: ApplicationSpec) -> list:
    """SV-11: Block types not in BLOCK_TYPES get a 'did you mean?' suggestion.
    (Hard parse errors from get_block_def are caught in compile_spec; this
    provides a friendlier pre-compose message.)"""
    issues = []
    for block_name, block in spec.blocks.items():
        if block.block_type not in BLOCK_TYPES:
            suggestions = get_close_matches(
                block.block_type, BLOCK_TYPES.keys(), n=3, cutoff=0.5
            )
            hint = f" — did you mean: {', '.join(suggestions)}?" if suggestions else ""
            issues.append(Issue(
                file='spec.yaml',
                severity='warning',
                message=f"blocks.{block_name}: unknown block type {block.block_type!r}{hint} "
                        f"(known types: {_fmt(BLOCK_TYPES)})",
            ))
    return issues


# ── SV-13 / SV-14: Block combination rules ───────────────────────────────────

def _check_block_combos(spec: ApplicationSpec) -> list:
    """SV-13: vm-workspace requires openshift-virtualization.
    SV-14: keycloak-oidc + vm-workspace should be wired together."""
    issues = []
    block_types = {b.block_type for b in spec.blocks.values()}
    block_by_type = {b.block_type: name for name, b in spec.blocks.items()}

    # SV-13
    if 'vm-workspace' in block_types and 'openshift-virtualization' not in block_types:
        issues.append(Issue(
            file='spec.yaml',
            severity='error',
            message="A 'vm-workspace' block is present but 'openshift-virtualization' is missing — "
                    "the vm-workspace block requires KubeVirt runtime (add an openshift-virtualization block)",
        ))

    # SV-14: both present but no wiring between them
    if 'keycloak-oidc' in block_types and 'vm-workspace' in block_types:
        kc_name = block_by_type['keycloak-oidc']
        vm_name = block_by_type['vm-workspace']
        wired = any(
            (e.from_block == kc_name and e.to_block == vm_name) or
            (e.from_block == vm_name and e.to_block == kc_name)
            for e in spec.wiring
        )
        if not wired:
            issues.append(Issue(
                file='spec.yaml',
                severity='warning',
                message=f"Block '{kc_name}' (keycloak-oidc) and '{vm_name}' (vm-workspace) are both "
                        f"present but not connected in wiring — add a wiring entry so the OIDC issuer "
                        f"URL flows to the sandbox (e.g. 'from: {kc_name}, to: {vm_name}, via: oidc-jwks')",
            ))

    return issues


# ── SV-12: Block secrets conflicting with custom pattern-secrets ─────────────

def _check_block_secrets_conflict(spec: ApplicationSpec) -> list:
    """SV-12: If any block declares block-level secrets AND a custom 'pattern-secrets'
    component exists, the auto-generated secrets chart will duplicate the ExternalSecrets
    already in pattern-secrets."""
    issues = []
    has_block_secrets = any(block.secrets for block in spec.blocks.values())
    has_pattern_secrets = 'pattern-secrets' in spec.custom

    if has_block_secrets and has_pattern_secrets:
        blocks_with_secrets = [
            name for name, b in spec.blocks.items() if b.secrets
        ]
        issues.append(Issue(
            file='spec.yaml',
            severity='warning',
            message=f"Block(s) {blocks_with_secrets} declare block-level secrets AND a 'pattern-secrets' "
                    f"custom chart is present — quickpat will auto-generate a "
                    f"'{spec.name}-secrets' chart that duplicates the ExternalSecrets in "
                    f"charts/pattern-secrets/. Move block secrets to the top-level 'secrets:' list "
                    f"and remove them from the block declaration.",
        ))
    return issues


# ── SV-6 / SV-7 / SV-8: Top-level secrets ───────────────────────────────────

def _check_secrets(spec: ApplicationSpec) -> list:
    """SV-6: secrets with no fields.
    SV-7: vault_path last segment should match name.
    SV-8: duplicate secret names."""
    issues = []
    seen_names = {}

    for i, secret in enumerate(spec.top_level_secrets):
        loc = f"secrets[{i}] ({secret.name!r})"

        # SV-8: duplicate names
        if secret.name in seen_names:
            issues.append(Issue(
                file='spec.yaml',
                severity='error',
                message=f"{loc}: duplicate secret name '{secret.name}' — first declared at "
                        f"secrets[{seen_names[secret.name]}]",
            ))
        else:
            seen_names[secret.name] = i

        # SV-6: no fields
        if not secret.fields:
            issues.append(Issue(
                file='spec.yaml',
                severity='warning',
                message=f"{loc}: has no fields — add at least one field entry "
                        f"(e.g. '- name: api_key') or remove this secret",
            ))

        # SV-7: vault_path convention
        if secret.vault_path:
            last_segment = secret.vault_path.split('/')[-1]
            if last_segment != secret.name:
                issues.append(Issue(
                    file='spec.yaml',
                    severity='warning',
                    message=f"{loc}: vault_path last segment '{last_segment}' does not match "
                            f"name '{secret.name}' — by convention these should match so Vault paths "
                            f"are predictable (e.g. vault_path: {spec.name}/{secret.name})",
                ))

    return issues


# ── SV-15: Vault enabled but no secrets ──────────────────────────────────────

def _check_vault(spec: ApplicationSpec) -> list:
    """SV-15: vault: enabled: true but no secrets declared anywhere."""
    issues = []
    has_any_secrets = (
        bool(spec.top_level_secrets) or
        any(block.secrets for block in spec.blocks.values())
    )
    if spec.vault_enabled and not has_any_secrets:
        issues.append(Issue(
            file='spec.yaml',
            severity='warning',
            message="vault: enabled: true but no secrets are declared — either add secrets to "
                    "the top-level 'secrets:' list or remove 'vault: {enabled: true}' if Vault "
                    "is not needed",
        ))
    return issues


# ── SV-5: Custom component chart paths ───────────────────────────────────────

def _check_chart_paths(spec: ApplicationSpec, spec_dir: str) -> list:
    """SV-5: source.chart paths on custom components must exist on disk."""
    issues = []
    base = Path(spec_dir)
    for comp_name, comp in spec.custom.items():
        if not comp.source_chart:
            continue
        chart_path = base / comp.source_chart
        if not chart_path.exists():
            issues.append(Issue(
                file='spec.yaml',
                severity='warning',
                message=f"custom.{comp_name}: source.chart path '{comp.source_chart}' does not "
                        f"exist at {chart_path} — the chart will be stubbed instead of copied",
            ))
    return issues


# ── SV-9 / SV-10: Doc source files and marker balance ────────────────────────

def _check_doc_files(spec: ApplicationSpec, spec_dir: str) -> list:
    """SV-9: doc source files must exist on disk.
    SV-10: doc markers must be balanced (every open has a matching end)."""
    issues = []
    base = Path(spec_dir)

    for i, entry in enumerate(spec.docs):
        loc = f"docs[{i}] ({entry.source!r})"
        source_path = base / entry.source

        # SV-9: file must exist
        if not source_path.exists():
            issues.append(Issue(
                file='spec.yaml',
                severity='warning',
                message=f"{loc}: source file '{entry.source}' does not exist at {source_path}",
            ))
            continue

        # SV-10: marker balance
        try:
            content = source_path.read_text(encoding='utf-8')
        except OSError:
            continue

        depth = 0
        for lineno, line in enumerate(content.splitlines(), start=1):
            if _VP_OPEN.match(line) or _QS_OPEN.match(line):
                depth += 1
            elif _END.match(line):
                if depth == 0:
                    issues.append(Issue(
                        file=entry.source,
                        severity='error',
                        message=f"line {lineno}: '<!-- end -->' has no matching opening marker "
                                f"(<!-- vp-only --> or <!-- qs-only -->)",
                    ))
                else:
                    depth -= 1

        if depth > 0:
            issues.append(Issue(
                file=entry.source,
                severity='error',
                message=f"{depth} unclosed marker(s) — every '<!-- vp-only -->' or "
                        f"'<!-- qs-only -->' must have a matching '<!-- end -->'",
            ))

    return issues


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(names) -> str:
    """Format a collection of names for error messages."""
    return ', '.join(sorted(names))


def _flatten(obj, prefix='') -> Iterator[tuple[str, object]]:
    """Yield (key_path, value) pairs from a nested dict/list, for scanning."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj
