"""Fetch and analyze sub-chart dependencies.

Downloads sub-charts from Helm repositories and inspects their templates
to understand secret creation patterns, computed fields, and resource types.
"""

import io
import re
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import get as cfg
from .profile import hash_directory


@dataclass
class SecretGate:
    """A condition that controls whether a sub-chart creates its own Secret."""
    condition_path: str
    default_value: bool = True
    k8s_secret_name: str = ""


@dataclass
class ComputedField:
    """A secret field composed from other fields."""
    name: str
    template: str
    source_fields: list = field(default_factory=list)


@dataclass
class SubChartInfo:
    """Information extracted from a sub-chart's templates and values."""
    name: str = ""
    version: str = ""
    secret_gates: list = field(default_factory=list)
    secret_fields: list = field(default_factory=list)
    computed_fields: list = field(default_factory=list)
    resource_types: list = field(default_factory=list)
    env_secret_refs: dict = field(default_factory=dict)
    template_hash: str = ""


# ── Fetch ────────────────────────────────────────────────────────────


def fetch_subchart(name: str, version: str, repo_url: str,
                   cache_dir: str = None) -> Path | None:
    """Download a sub-chart archive and extract to cache directory.

    Returns the extracted chart directory, or None on failure.
    """
    if cache_dir is None:
        cache_dir = str(
            Path(cfg("pattern.subchart_cache_dir",
                      "~/.cache/quickpat/charts")).expanduser()
        )

    cache_path = Path(cache_dir) / f"{name}-{version}"
    if cache_path.is_dir() and any(cache_path.iterdir()):
        return cache_path

    archive_url = _resolve_archive_url(name, version, repo_url)
    if not archive_url:
        return None

    try:
        req = urllib.request.Request(archive_url)
        timeout = cfg("registry.timeout", 10)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception:
        return None

    try:
        cache_path.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            tar.extractall(cache_path, filter='data')
    except Exception:
        return None

    return cache_path


def _resolve_archive_url(name: str, version: str, repo_url: str) -> str:
    """Resolve a chart archive URL from the Helm repo index."""
    index_url = repo_url.rstrip('/') + '/index.yaml'
    try:
        req = urllib.request.Request(index_url)
        timeout = cfg("registry.timeout", 10)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            index = yaml.safe_load(resp.read())
    except Exception:
        return ""

    entries = index.get("entries", {}).get(name, [])
    for entry in entries:
        if entry.get("version") == version:
            urls = entry.get("urls", [])
            if urls:
                url = urls[0]
                if url.startswith("http"):
                    return url
                return repo_url.rstrip('/') + '/' + url
    return ""


# ── Analyze ──────────────────────────────────────────────────────────


def analyze_subchart(chart_path: Path) -> SubChartInfo:
    """Analyze an extracted sub-chart directory.

    Reads templates and values to extract:
    - Secret gates (conditions that control Secret creation)
    - Secret field names
    - Computed fields (derived values like jdbc-uri)
    - Resource types (apiGroup, kind)
    - Environment variable secretKeyRef references
    """
    info = SubChartInfo()

    # Find the actual chart dir (may be nested inside extraction dir)
    chart_dir = _find_chart_dir(chart_path)
    if not chart_dir:
        return info

    chart_yaml = chart_dir / 'Chart.yaml'
    if chart_yaml.exists():
        with open(chart_yaml) as f:
            meta = yaml.safe_load(f) or {}
        info.name = meta.get('name', chart_dir.name)
        info.version = meta.get('version', '')

    values_file = chart_dir / 'values.yaml'
    values = {}
    if values_file.exists():
        with open(values_file) as f:
            values = yaml.safe_load(f) or {}

    templates_dir = chart_dir / 'templates'

    # Analyze values.yaml for secret gates and env secretKeyRefs
    info.secret_gates = _detect_secret_gates(values, templates_dir)
    info.env_secret_refs = _detect_env_secret_refs(values)

    # Analyze templates
    if templates_dir.is_dir():
        for tmpl in templates_dir.glob('*.yaml'):
            content = tmpl.read_text()
            _extract_resource_types(content, info)
            _extract_secret_info(content, tmpl.name, info)

        # Also check .tpl files for helpers
        for tmpl in templates_dir.glob('*.tpl'):
            pass  # helpers don't contain resources

        info.template_hash = hash_directory(templates_dir)

    return info


def fetch_and_analyze_subcharts(
    dependencies: list,
    cache_dir: str = None,
) -> dict:
    """Fetch and analyze all sub-chart dependencies.

    Args:
        dependencies: list of ChartDependency objects
        cache_dir: optional cache directory override

    Returns dict mapping sub-chart name to SubChartInfo.
    """
    results = {}
    for dep in dependencies:
        if not dep.repository:
            continue
        chart_path = fetch_subchart(
            dep.name, dep.version, dep.repository, cache_dir,
        )
        if chart_path:
            info = analyze_subchart(chart_path)
            if info.name:
                results[info.name] = info
    return results


# ── Analysis Helpers ─────────────────────────────────────────────────


def _find_chart_dir(path: Path) -> Path | None:
    """Find the actual chart directory inside an extraction path.

    Helm tarballs typically extract to <name>/Chart.yaml.
    """
    if (path / 'Chart.yaml').exists():
        return path
    # Check one level of nesting (e.g., helm/ subdirectory)
    for child in path.iterdir():
        if child.is_dir():
            if (child / 'Chart.yaml').exists():
                return child
            # Two levels: e.g., pgvector/helm/Chart.yaml
            for grandchild in child.iterdir():
                if grandchild.is_dir() and (grandchild / 'Chart.yaml').exists():
                    return grandchild
    return None


def _detect_secret_gates(values: dict, templates_dir: Path = None) -> list:
    """Find secret creation gates in values.yaml.

    Looks for patterns like:
      secret:
        create: true
      secret:
        enabled: true
    """
    gates = []

    def _walk(d, prefix=""):
        if not isinstance(d, dict):
            return
        for key, val in d.items():
            path = f"{prefix}.{key}" if prefix else key
            if key == 'secret' and isinstance(val, dict):
                if 'create' in val:
                    gate_path = f"{path}.create"
                    gate = SecretGate(
                        condition_path=gate_path,
                        default_value=bool(val['create']),
                    )
                    # Try to find the K8s secret name from templates
                    if templates_dir:
                        gate.k8s_secret_name = _find_secret_name(
                            templates_dir, gate_path,
                        )
                    gates.append(gate)
                elif 'enabled' in val:
                    gate_path = f"{path}.enabled"
                    gate = SecretGate(
                        condition_path=gate_path,
                        default_value=bool(val['enabled']),
                    )
                    if templates_dir:
                        gate.k8s_secret_name = _find_secret_name(
                            templates_dir, gate_path,
                        )
                    gates.append(gate)
            elif isinstance(val, dict):
                _walk(val, path)

    _walk(values)
    return gates


def _find_secret_name(templates_dir: Path, gate_path: str) -> str:
    """Find the K8s Secret name from templates gated by this condition.

    Looks for templates containing both 'kind: Secret' and the gate
    condition, then extracts the metadata.name.
    """
    if not templates_dir.is_dir():
        return ""

    for tmpl in templates_dir.glob('*.yaml'):
        content = tmpl.read_text()
        if 'kind: Secret' not in content:
            continue

        # Extract metadata.name — common patterns:
        # name: {{ include "chart.fullname" . }}
        # name: pgvector
        # name: huggingface-secret
        m = re.search(r'metadata:\s*\n\s+name:\s+(.+)', content)
        if m:
            name_val = m.group(1).strip()
            # If it's a plain string (no templates), use it directly
            if not name_val.startswith('{{'):
                return name_val
            # Try to extract the static part from template helpers
            static = re.search(r'include\s+"[^"]+\.fullname"', name_val)
            if static:
                # Can't resolve templates, but we know it's a fullname helper
                return ""
    return ""


def _detect_env_secret_refs(values: dict) -> dict:
    """Find secretKeyRef references in values.yaml env blocks.

    Returns dict: env_var_name -> (secret_name, key)
    """
    refs = {}
    env_list = values.get('env', [])
    if not isinstance(env_list, list):
        return refs

    for entry in env_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name', '')
        value_from = entry.get('valueFrom', {})
        if not isinstance(value_from, dict):
            continue
        secret_ref = value_from.get('secretKeyRef', {})
        if isinstance(secret_ref, dict) and 'name' in secret_ref:
            refs[name] = (secret_ref['name'], secret_ref.get('key', ''))

    return refs


def _extract_resource_types(content: str, info: SubChartInfo):
    """Extract (apiGroup, kind) pairs from a template."""
    # Match apiVersion and kind on adjacent lines
    api_matches = re.finditer(
        r'apiVersion:\s*([^\s\n]+)', content,
    )
    kind_matches = re.finditer(
        r'kind:\s*([^\s\n{]+)', content,
    )

    api_versions = [m.group(1).strip('"\'') for m in api_matches]
    kinds = [m.group(1).strip('"\'') for m in kind_matches]

    for api_ver, kind in zip(api_versions, kinds):
        if kind in ('Secret', 'ConfigMap', 'ServiceAccount'):
            continue  # skip common resources
        group = api_ver.rsplit('/', 1)[0] if '/' in api_ver else ''
        pair = (group, kind)
        if pair not in info.resource_types:
            info.resource_types.append(pair)


def _extract_secret_info(content: str, filename: str, info: SubChartInfo):
    """Extract secret field names and computed fields from a Secret template."""
    if 'kind: Secret' not in content:
        return

    # Extract data/stringData keys
    in_data = False
    indent = 0
    for line in content.splitlines():
        stripped = line.lstrip()

        if re.match(r'(data|stringData)\s*:', stripped):
            in_data = True
            indent = len(line) - len(stripped) + 2
            continue

        if in_data:
            current_indent = len(line) - len(stripped)
            if stripped and current_indent < indent:
                in_data = False
                continue
            if not stripped:
                continue

            # Match: field_name: <value>
            m = re.match(r'(\w[\w-]*):\s*(.+)', stripped)
            if not m:
                continue

            field_name = m.group(1)
            field_value = m.group(2).strip()

            if field_name not in info.secret_fields:
                info.secret_fields.append(field_name)

            # Check if it's a computed field (contains printf or multiple .Values refs)
            if 'printf' in field_value or field_value.count('.Values.') > 1:
                source_fields = re.findall(
                    r'\.Values\.(?:secret\.)?(\w+)', field_value,
                )
                if not source_fields:
                    source_fields = re.findall(r'\.(\w+)', field_value)
                    source_fields = [
                        f for f in source_fields
                        if f not in ('Values', 'Release', 'Namespace')
                    ]

                info.computed_fields.append(ComputedField(
                    name=field_name,
                    template=field_value,
                    source_fields=source_fields,
                ))


# ── Go Template Stripping ──────────────────────────────────────────

_STANDARD_KINDS = frozenset({
    'ConfigMap', 'Deployment', 'Job', 'CronJob', 'DaemonSet',
    'HorizontalPodAutoscaler', 'Ingress', 'NetworkPolicy',
    'PersistentVolumeClaim', 'Pod', 'ReplicaSet', 'Secret',
    'Service', 'ServiceAccount', 'StatefulSet',
})


def strip_go_templates(content: str) -> str:
    """Replace Go template expressions with YAML-safe placeholders.

    Handles ``{{ ... }}``, ``{{- ... }}``, and ``{{- ... -}}`` variants.
    Block directives (if/else/end/range/with/define/block) are replaced
    with YAML comments so the body lines remain parseable.
    """
    lines = []
    for line in content.splitlines():
        stripped = line.lstrip()
        # Lines that are purely a block directive → YAML comment
        if re.match(r'\{\{-?\s*(if|else|end|range|with|define|block|template)\b', stripped):
            indent = len(line) - len(stripped)
            lines.append(' ' * indent + '# __TMPL_BLOCK__')
            continue
        # Inline expressions → placeholder string
        line = re.sub(r'\{\{-?.*?-?\}\}', '__TMPL__', line)
        lines.append(line)
    return '\n'.join(lines)


def extract_resource_types_from_templates(
    templates_dir: Path,
) -> list[tuple[str, str]]:
    """Extract (apiGroup, kind) pairs from Go-templated Helm templates.

    Strips Go template expressions, attempts YAML parsing, and falls
    back to regex extraction on parse failure.  Skips standard K8s
    resource types (Deployment, Service, etc.).
    """
    results: list[tuple[str, str]] = []

    if not templates_dir.is_dir():
        return results

    for tmpl in templates_dir.glob('*.yaml'):
        try:
            raw = tmpl.read_text(errors='ignore')
        except Exception:
            continue

        cleaned = strip_go_templates(raw)

        pairs = _parse_resource_types_yaml(cleaned)
        if not pairs:
            pairs = _parse_resource_types_regex(raw)

        for pair in pairs:
            if pair[1] not in _STANDARD_KINDS and pair not in results:
                results.append(pair)

    return results


def _parse_resource_types_yaml(content: str) -> list[tuple[str, str]]:
    """Try to extract (group, kind) via YAML parsing."""
    pairs = []
    try:
        for doc in yaml.safe_load_all(content):
            if not isinstance(doc, dict):
                continue
            api_ver = doc.get('apiVersion', '')
            kind = doc.get('kind', '')
            if not api_ver or not kind or kind == '__TMPL__':
                continue
            api_ver = str(api_ver).replace('__TMPL__', '').strip('/')
            kind = str(kind)
            group = api_ver.rsplit('/', 1)[0] if '/' in api_ver else ''
            pairs.append((group, kind))
    except Exception:
        pass
    return pairs


def _parse_resource_types_regex(content: str) -> list[tuple[str, str]]:
    """Fallback: extract (group, kind) via regex on raw template text."""
    api_versions = [
        m.group(1).strip('"\'')
        for m in re.finditer(r'apiVersion:\s*([^\s\n{]+)', content)
    ]
    kinds = [
        m.group(1).strip('"\'')
        for m in re.finditer(r'kind:\s*([^\s\n{]+)', content)
    ]
    pairs = []
    for api_ver, kind in zip(api_versions, kinds):
        group = api_ver.rsplit('/', 1)[0] if '/' in api_ver else ''
        pairs.append((group, kind))
    return pairs
