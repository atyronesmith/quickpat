"""Transform Helm chart files for Validated Pattern compatibility.

Applies rule-based rewrites to chart files after they are copied into
the pattern output directory. Each rule is independent and idempotent.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .analyzer import QuickstartAnalysis, SecretRef, ChartInfo


ALL_RULES = ['secrets', 'hooks', 'registry']

HOOK_TO_WAVE = {
    'pre-install': '-5',
    'pre-upgrade': '-5',
    'post-install': '5',
    'post-upgrade': '5',
    'pre-delete': '-10',
    'post-delete': '10',
}


@dataclass
class TransformResult:
    """Result of applying chart transformations."""
    rules_applied: list = field(default_factory=list)
    files_modified: list = field(default_factory=list)
    files_created: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def merge(self, other: 'TransformResult'):
        self.rules_applied.extend(other.rules_applied)
        self.files_modified.extend(other.files_modified)
        self.files_created.extend(other.files_created)
        self.warnings.extend(other.warnings)


def transform_chart(
    chart_dir: str,
    analysis: QuickstartAnalysis,
    chart_info: ChartInfo = None,
    rules: list = None,
) -> TransformResult:
    """Apply rewrite rules to a chart directory.

    Args:
        chart_dir: Path to the chart directory (already copied to output).
        analysis: The quickstart analysis with detected secrets etc.
        chart_info: Optional ChartInfo for this specific chart.
        rules: List of rule names to apply. Default: all rules.
    """
    result = TransformResult()
    path = Path(chart_dir)

    if not path.is_dir():
        result.warnings.append(f"Chart directory not found: {chart_dir}")
        return result

    active_rules = rules or ALL_RULES

    if 'secrets' in active_rules and analysis.detected_secrets:
        r = _externalize_secrets(path, analysis.detected_secrets, chart_info)
        result.merge(r)

    if 'hooks' in active_rules:
        r = _convert_hooks(path)
        result.merge(r)

    if 'registry' in active_rules:
        r = _add_registry_override(path)
        result.merge(r)

    return result


# ── Helpers ────────────────────────────────────────────────────────────


def _detect_helper_prefix(chart_dir: Path) -> str:
    """Read _helpers.tpl to find the chart's template name prefix."""
    helpers = chart_dir / 'templates' / '_helpers.tpl'
    if not helpers.exists():
        return chart_dir.name

    content = helpers.read_text()
    m = re.search(r'{{\-?\s*define\s+"([^"]+)\.fullname"', content)
    if m:
        return m.group(1)

    m = re.search(r'{{\-?\s*define\s+"([^"]+)\.name"', content)
    if m:
        return m.group(1)

    return chart_dir.name


def _group_secrets(secrets: list, chart_name: str) -> dict:
    """Group secrets by first meaningful path segment.

    E.g., path "rag.pgvector.secret.password" → group "pgvector"
          path "rag.hf_token" → group "rag"
    """
    groups = {}
    for s in secrets:
        parts = [p for p in s.path.replace('[', '.').replace(']', '').split('.')
                 if p]
        # Skip the chart name prefix if present
        if parts and parts[0] == chart_name:
            parts = parts[1:]
        # Use first segment as group, or chart name if only one segment
        group = parts[0] if len(parts) > 1 else chart_name
        groups.setdefault(group, []).append(s)
    return groups


# ── Rule 1: Secret Externalization ─────────────────────────────────────


def _externalize_secrets(
    chart_dir: Path,
    secrets: list,
    chart_info: ChartInfo = None,
) -> TransformResult:
    """Replace plaintext secrets with ExternalSecret CRDs."""
    result = TransformResult()
    chart_name = chart_info.name if chart_info else chart_dir.name
    prefix = _detect_helper_prefix(chart_dir)
    groups = _group_secrets(secrets, chart_name)

    # 1. Clear secret values in values.yaml
    values_file = chart_dir / 'values.yaml'
    if values_file.exists():
        modified = _clear_secret_values(values_file, secrets)
        if modified:
            _add_secret_store_config(values_file)
            result.files_modified.append('values.yaml')

    # 2. Generate ExternalSecret templates
    templates_dir = chart_dir / 'templates'
    templates_dir.mkdir(exist_ok=True)

    for group_name, group_secrets in groups.items():
        es_file = templates_dir / f'external-secret-{group_name}.yaml'
        if not es_file.exists():
            content = _render_external_secret(prefix, group_name, group_secrets)
            es_file.write_text(content)
            result.files_created.append(f'templates/external-secret-{group_name}.yaml')

    # 3. Rewrite deployment templates to use secretKeyRef
    for tmpl in templates_dir.glob('*.yaml'):
        if tmpl.name.startswith('external-secret-'):
            continue
        modified = _rewrite_secret_refs(tmpl, secrets, prefix, groups)
        if modified:
            result.files_modified.append(f'templates/{tmpl.name}')

    if result.files_modified or result.files_created:
        result.rules_applied.append('secrets')

    return result


def _clear_secret_values(values_file: Path, secrets: list) -> bool:
    """Clear plaintext secret values in values.yaml via regex."""
    content = values_file.read_text()
    original = content

    for s in secrets:
        key = s.name
        # Match: key: <scalar-value> on a single line
        # Uses [ \t]+ (not \s+) after colon to avoid matching across lines
        # Skips lines where value is already "" or ''
        pattern = re.compile(
            rf'^([ \t]+{re.escape(key)}:[ \t]+)(?!""|\'\')(\S[^\n]*)$',
            re.MULTILINE,
        )
        content = pattern.sub(rf'\g<1>""', content)

    if content != original:
        values_file.write_text(content)
        return True
    return False


def _add_secret_store_config(values_file: Path):
    """Add secretStore config block to values.yaml if not present."""
    content = values_file.read_text()
    if 'secretStore:' in content:
        return

    block = (
        '\n'
        'secretStore:\n'
        '  name: vault-backend\n'
        '  kind: ClusterSecretStore\n'
    )
    values_file.write_text(content + block)


def _render_external_secret(
    prefix: str, group_name: str, secrets: list,
) -> str:
    """Generate an ExternalSecret CRD template for a secret group."""
    return (
        f'{{{{- if .Values.secretStore }}}}\n'
        f'apiVersion: "external-secrets.io/v1beta1"\n'
        f'kind: ExternalSecret\n'
        f'metadata:\n'
        f'  name: {{{{- include "{prefix}.fullname" . }}}}-{group_name}-secret\n'
        f'spec:\n'
        f'  refreshInterval: 15s\n'
        f'  secretStoreRef:\n'
        f'    name: {{{{ .Values.secretStore.name }}}}\n'
        f'    kind: {{{{ .Values.secretStore.kind }}}}\n'
        f'  target:\n'
        f'    name: {{{{- include "{prefix}.fullname" . }}}}-{group_name}-secret\n'
        f'    template:\n'
        f'      type: Opaque\n'
        f'  dataFrom:\n'
        f'  - extract:\n'
        f'      key: secret/data/global/{{{{ .Release.Name }}}}/{group_name}\n'
        f'{{{{- end }}}}\n'
    )


def _rewrite_secret_refs(
    template_file: Path, secrets: list, prefix: str, groups: dict,
) -> bool:
    """Rewrite direct .Values secret references to secretKeyRef."""
    content = template_file.read_text()
    original = content

    # Build a map: values path suffix → (group_name, secret_key)
    path_map = {}
    for group_name, group_secrets in groups.items():
        for s in group_secrets:
            # Build the .Values path fragment: e.g., "pgvector.secret.password"
            parts = s.path.split('.')
            # Find the path starting from the group name
            try:
                idx = parts.index(group_name)
                values_path = '.'.join(parts[idx:])
            except ValueError:
                values_path = s.path
            path_map[values_path] = (group_name, s.name)

    # Pattern 1: value: {{ .Values.path.to.secret | quote }}
    for values_path, (group_name, secret_key) in path_map.items():
        escaped_path = re.escape(values_path)
        pattern = re.compile(
            rf'^(\s+)value:\s*\{{\{{\s*\.Values\.{escaped_path}\s*'
            rf'(?:\|\s*quote\s*)?\}}\}}\s*$',
            re.MULTILINE,
        )
        secret_name = f'{{{{- include "{prefix}.fullname" . }}}}-{group_name}-secret'
        replacement = (
            rf'\g<1>valueFrom:\n'
            rf'\g<1>  secretKeyRef:\n'
            rf'\g<1>    name: {secret_name}\n'
            rf'\g<1>    key: {secret_key}'
        )
        content = pattern.sub(replacement, content)

    # Pattern 2: value: {{ (index .Values "hyphenated-key").sub.path | quote }}
    for values_path, (group_name, secret_key) in path_map.items():
        parts = values_path.split('.')
        if '-' not in parts[0]:
            continue
        indexed = f'(index .Values "{parts[0]}")'
        rest = '.'.join(parts[1:])
        escaped = re.escape(f'{indexed}.{rest}')
        pattern = re.compile(
            rf'^(\s+)value:\s*\{{\{{\s*{escaped}\s*'
            rf'(?:\|\s*quote\s*)?\}}\}}\s*$',
            re.MULTILINE,
        )
        secret_name = f'{{{{- include "{prefix}.fullname" . }}}}-{group_name}-secret'
        replacement = (
            rf'\g<1>valueFrom:\n'
            rf'\g<1>  secretKeyRef:\n'
            rf'\g<1>    name: {secret_name}\n'
            rf'\g<1>    key: {secret_key}'
        )
        content = pattern.sub(replacement, content)

    if content != original:
        template_file.write_text(content)
        return True
    return False


# ── Rule 2: Helm Hook → ArgoCD Sync Wave ──────────────────────────────


def _convert_hooks(chart_dir: Path) -> TransformResult:
    """Convert helm.sh/hook annotations to ArgoCD sync waves."""
    result = TransformResult()
    templates_dir = chart_dir / 'templates'
    if not templates_dir.is_dir():
        return result

    for tmpl in templates_dir.glob('*.yaml'):
        content = tmpl.read_text()
        if 'helm.sh/hook' not in content:
            continue

        modified = _rewrite_hooks(content)
        if modified != content:
            tmpl.write_text(modified)
            result.files_modified.append(f'templates/{tmpl.name}')

    if result.files_modified:
        result.rules_applied.append('hooks')

    return result


def _rewrite_hooks(content: str) -> str:
    """Replace helm hook annotations with sync wave annotations."""
    # Extract hook-weight if present (use as sync-wave value)
    weight_match = re.search(
        r'["\']?helm\.sh/hook-weight["\']?\s*:\s*["\']?(-?\d+)["\']?',
        content,
    )
    explicit_weight = weight_match.group(1) if weight_match else None

    # Find the hook type to determine default wave
    hook_match = re.search(
        r'["\']?helm\.sh/hook["\']?\s*:\s*["\']?([\w,-]+)["\']?',
        content,
    )
    if not hook_match:
        return content

    hook_types = [h.strip() for h in hook_match.group(1).split(',')]
    # Use the first recognized hook type for the default wave
    default_wave = '-5'
    for ht in hook_types:
        if ht in HOOK_TO_WAVE:
            default_wave = HOOK_TO_WAVE[ht]
            break

    wave = explicit_weight or default_wave

    # Replace hook annotation with sync-wave
    content = re.sub(
        r'\s*["\']?helm\.sh/hook["\']?\s*:\s*["\']?[\w,-]+["\']?\s*\n?',
        f'\n    argocd.argoproj.io/sync-wave: "{wave}"\n',
        content,
        count=1,
    )

    # Remove hook-weight and hook-delete-policy lines
    content = re.sub(
        r'\s*["\']?helm\.sh/hook-weight["\']?\s*:\s*["\']?-?\d+["\']?\s*\n?',
        '\n',
        content,
    )
    content = re.sub(
        r'\s*["\']?helm\.sh/hook-delete-policy["\']?\s*:\s*["\']?[\w,-]+["\']?\s*\n?',
        '\n',
        content,
    )

    # Clean up any resulting blank annotation blocks
    content = re.sub(r'\n\n\n+', '\n\n', content)

    return content


# ── Rule 3: Image Registry Override ────────────────────────────────────


_KNOWN_REGISTRIES = [
    'quay.io', 'registry.redhat.io', 'registry.access.redhat.com',
    'docker.io', 'ghcr.io', 'gcr.io', 'mcr.microsoft.com',
    'registry.k8s.io', 'public.ecr.aws',
]


def _add_registry_override(chart_dir: Path) -> TransformResult:
    """Split hardcoded image registries into overridable parts."""
    result = TransformResult()
    values_file = chart_dir / 'values.yaml'
    if not values_file.exists():
        return result

    content = values_file.read_text()
    original = content

    # Find image.repository lines with known registries
    # Match pattern: repository: registry.example.com/org/image
    registries_found = {}
    for registry in _KNOWN_REGISTRIES:
        pattern = re.compile(
            rf'^(\s+)(repository:\s+){re.escape(registry)}/(.+)$',
            re.MULTILINE,
        )
        for m in pattern.finditer(content):
            registries_found[m.group(0)] = (registry, m.group(1), m.group(3))

    if not registries_found:
        return result

    # Replace repository values and add registry field
    for original_line, (registry, indent, path) in registries_found.items():
        replacement = f'{indent}registry: {registry}\n{indent}repository: {path}'
        content = content.replace(original_line, replacement)

    # Add global.imageRegistry if not present
    if 'imageRegistry' not in content:
        if re.search(r'^global:', content, re.MULTILINE):
            content = re.sub(
                r'^(global:\s*\n)',
                r'\g<1>  imageRegistry: ""\n',
                content,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            content = f'global:\n  imageRegistry: ""\n\n{content}'

    if content != original:
        values_file.write_text(content)
        result.files_modified.append('values.yaml')

        # Rewrite template image references
        templates_dir = chart_dir / 'templates'
        if templates_dir.is_dir():
            for tmpl in templates_dir.glob('*.yaml'):
                if _rewrite_image_refs(tmpl):
                    result.files_modified.append(f'templates/{tmpl.name}')

        result.rules_applied.append('registry')

    return result


def _rewrite_image_refs(template_file: Path) -> bool:
    """Rewrite template image lines to use registry override."""
    content = template_file.read_text()
    original = content

    # Match: image: "{{ .Values.image.repository }}:{{ ... }}"
    pattern = re.compile(
        r'image:\s*"?\{\{\s*\.Values\.image\.repository\s*\}\}'
        r'(:\{\{[^}]+\}\})"?',
    )
    replacement = (
        r'image: "{{ .Values.global.imageRegistry | default .Values.image.registry }}'
        r'/{{ .Values.image.repository }}\g<1>"'
    )
    content = pattern.sub(replacement, content)

    if content != original:
        template_file.write_text(content)
        return True
    return False
