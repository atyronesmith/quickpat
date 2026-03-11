"""Analyzes AI Quickstart Helm charts to extract components and requirements."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .operators import OPERATORS, resolve_co_dependencies

SECRET_PATTERNS = [
    'token', 'key', 'password', 'secret', 'credential',
    'api_key', 'apikey', 'api-key', 'access_key', 'secret_key',
]

# Keys that match SECRET_PATTERNS but aren't actually secrets
SECRET_FALSE_POSITIVES = {
    'secretkey',      # often a dict key name, not a secret value
    'key',            # too generic on its own (e.g. secretKeyRef.key)
    'secrets',        # plural = container, not a value
    'bearertokenauth',  # OTEL extension name
}

# Suffixes/patterns that indicate a reference TO a secret, not a secret value
SECRET_REFERENCE_SUFFIXES = [
    'name', 'ref', 'path', 'namespace', 'mount', 'class',
    'store', 'backend', 'provider', 'type', 'kind', 'version',
]

# Prefixes that indicate config about secrets, not secret values
SECRET_CONFIG_PREFIXES = [
    'use', 'enable', 'disable', 'is', 'has', 'no',
]


@dataclass
class ChartDependency:
    name: str
    version: str
    repository: str = ""
    condition: str = ""


@dataclass
class SecretRef:
    name: str
    path: str
    description: str = ""


# Template-level CRDs and dependency names that indicate OpenShift AI namespace labels are needed
INFERENCE_INDICATORS = [
    'inferenceservice', 'servingruntime', 'datasciencecluster',
]
INFERENCE_DEPENDENCY_NAMES = {
    'llm-service', 'vllm', 'llama-stack', 'model-service', 'tgi',
}


@dataclass
class ChartInfo:
    """Info about a single Helm chart within a quickstart."""
    name: str = ""
    version: str = ""
    description: str = ""
    chart_path: str = ""
    group: str = ""  # subdirectory group for namespace sharing
    dependencies: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    needs_oai_labels: bool = False
    strategy: str = ""  # 'local' or 'external'; empty = use config default
    repo_url: str = ""  # Helm repo URL for external charts


@dataclass
class QuickstartAnalysis:
    """Results of analyzing an AI Quickstart."""
    name: str = ""
    version: str = ""
    description: str = ""
    chart_path: str = ""
    charts: list = field(default_factory=list)  # list[ChartInfo]
    dependencies: list = field(default_factory=list)
    detected_operators: list = field(default_factory=list)
    detected_secrets: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    has_gpu_requirement: bool = False
    has_pipeline: bool = False
    has_llm_service: bool = False
    has_vector_db: bool = False
    has_object_storage: bool = False


class QuickstartAnalyzer:
    """Analyzes an AI Quickstart Helm chart directory."""

    def __init__(self, path: str):
        self.path = Path(path).resolve()

    def analyze(self) -> QuickstartAnalysis:
        """Run full analysis and return results."""
        analysis = QuickstartAnalysis()

        search_root, chart_paths = self._find_charts()

        # Analyze each chart
        search_texts = []
        for chart_path in chart_paths:
            ci = ChartInfo()
            ci.chart_path = str(chart_path)

            # Compute group from subdirectory structure
            try:
                rel = chart_path.relative_to(search_root)
                parts = rel.parts
                if len(parts) > 1:
                    ci.group = self._strip_numeric_prefix(parts[0])
            except ValueError:
                pass

            self._parse_chart_info(ci, chart_path)
            self._parse_chart_values(ci, chart_path)

            chart_text = self._build_search_text(chart_path)
            chart_text_lower = chart_text.lower()
            dep_names = {d.name.lower() for d in ci.dependencies}
            ci.needs_oai_labels = (
                any(ind in chart_text_lower for ind in INFERENCE_INDICATORS)
                or bool(dep_names & INFERENCE_DEPENDENCY_NAMES)
            )

            analysis.charts.append(ci)

            # Aggregate dependencies and values into analysis
            analysis.dependencies.extend(ci.dependencies)
            for key, val in ci.values.items():
                analysis.values.setdefault(key, val)

            search_texts.append(chart_text)

        # Set top-level fields from first chart (single-chart) or repo name (multi-chart)
        if len(analysis.charts) == 1:
            first = analysis.charts[0]
            analysis.name = first.name
            analysis.version = first.version
            analysis.description = first.description
            analysis.chart_path = first.chart_path
        else:
            analysis.name = self.path.name
            analysis.version = "0.1.0"
            analysis.description = f"Multi-chart quickstart ({len(analysis.charts)} charts)"
            analysis.chart_path = str(chart_paths[0].parent)

        # Detect operators/secrets/features across all charts
        combined_text = '\n'.join(search_texts)
        self._detect_operators(analysis, combined_text)
        self._detect_secrets(analysis)
        self._detect_features(analysis, combined_text)

        return analysis

    def _find_charts(self):
        """Locate all Chart.yaml files in common quickstart layouts.

        Returns (search_root, chart_paths) where search_root is the
        directory that was searched and chart_paths is a list of Paths
        to directories containing Chart.yaml.
        """
        search_dirs = [
            self.path / 'deploy' / 'helm',
            self.path / 'deploy' / 'cluster' / 'helm',
            self.path / 'helm',
            self.path / 'chart',
            self.path,
        ]

        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue

            # Check for Chart.yaml directly in this dir
            if (search_dir / 'Chart.yaml').exists():
                return search_dir, [search_dir]

            # Collect all subdirectories (recursively) that have Chart.yaml
            found = []
            for child in sorted(search_dir.rglob('Chart.yaml')):
                found.append(child.parent)

            if found:
                return search_dir, found

        raise FileNotFoundError(
            f"No Chart.yaml found in {self.path}. "
            "Searched: deploy/helm/, deploy/cluster/helm/, helm/, "
            "chart/, root, and their subdirectories."
        )

    @staticmethod
    def _strip_numeric_prefix(name):
        """Strip leading numeric prefix like '01-' from directory names."""
        parts = name.split('-', 1)
        if len(parts) == 2 and parts[0].isdigit():
            return parts[1]
        return name

    def _parse_chart_info(self, ci, chart_path):
        with open(chart_path / 'Chart.yaml') as f:
            chart = yaml.safe_load(f) or {}

        ci.name = chart.get('name', chart_path.name)
        ci.version = chart.get('version', '0.1.0')
        ci.description = chart.get('description', '')

        for dep in chart.get('dependencies', []):
            ci.dependencies.append(ChartDependency(
                name=dep.get('name', ''),
                version=dep.get('version', '*'),
                repository=dep.get('repository', ''),
                condition=dep.get('condition', ''),
            ))

    def _parse_chart_values(self, ci, chart_path):
        values_file = chart_path / 'values.yaml'
        if values_file.exists():
            with open(values_file) as f:
                ci.values = yaml.safe_load(f) or {}

    def _detect_operators(self, analysis, search_text):
        text_lower = search_text.lower()
        detected = set()

        for op_key, op_info in OPERATORS.items():
            for indicator in op_info['indicators']:
                if indicator in text_lower:
                    detected.add(op_key)
                    break

        analysis.detected_operators = resolve_co_dependencies(detected)

    def _detect_secrets(self, analysis):
        for ci in analysis.charts:
            self._walk_for_secrets(analysis, ci.values, ci.name)

    def _walk_for_secrets(self, analysis, obj, path):
        if isinstance(obj, dict):
            for key, value in obj.items():
                current = f"{path}.{key}" if path else key
                if self._is_secret_key(key):
                    # Only flag leaf values or dicts that look like secret containers
                    # Skip if the value is a complex nested structure (it's a config block)
                    if not isinstance(value, dict) or self._is_secret_leaf_dict(value):
                        analysis.detected_secrets.append(SecretRef(
                            name=key,
                            path=current,
                            description=f"Potential secret: {key}",
                        ))
                self._walk_for_secrets(analysis, value, current)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._walk_for_secrets(analysis, item, f"{path}[{i}]")

    def _is_secret_key(self, key: str) -> bool:
        """Check if a YAML key likely holds a secret value."""
        key_lower = key.lower()

        if key_lower in SECRET_FALSE_POSITIVES:
            return False

        # Must match at least one secret pattern
        if not any(p in key_lower for p in SECRET_PATTERNS):
            return False

        # Filter out references TO secrets (secretName, secretKeyRef, etc.)
        for suffix in SECRET_REFERENCE_SUFFIXES:
            # e.g. secretName, tokenSecretName, secretKeyRef
            if key_lower.endswith(suffix) and key_lower != suffix:
                return False

        # Filter out boolean/config flags (useToken, enableSecret, etc.)
        for prefix in SECRET_CONFIG_PREFIXES:
            if key_lower.startswith(prefix) and len(key_lower) > len(prefix):
                # Check the char after prefix is uppercase (camelCase) or underscore
                next_char = key[len(prefix)]
                if next_char.isupper() or next_char == '_':
                    return False

        return True

    def _is_secret_leaf_dict(self, d: dict) -> bool:
        """Check if a dict is a simple secret container (e.g. {password: '', token: ''})
        vs a complex config block."""
        if len(d) > 5:
            return False
        # If all values are scalars, it's likely a secret container
        return all(not isinstance(v, (dict, list)) for v in d.values())

    def _detect_features(self, analysis, search_text):
        dep_names = {d.name.lower() for d in analysis.dependencies}
        text = search_text.lower()

        vector_names = {'pgvector', 'redis', 'elasticsearch', 'milvus', 'chroma', 'qdrant'}
        analysis.has_vector_db = bool(dep_names & vector_names) or 'vector' in text

        llm_names = {'llm-service', 'vllm', 'llama-stack', 'tgi', 'model-service'}
        analysis.has_llm_service = bool(dep_names & llm_names) or 'vllm' in text

        storage_names = {'minio', 's3', 'object-storage'}
        analysis.has_object_storage = bool(dep_names & storage_names) or 'minio' in text

        pipeline_names = {'ingestion-pipeline', 'pipeline', 'data-pipeline'}
        analysis.has_pipeline = bool(dep_names & pipeline_names)

        analysis.has_gpu_requirement = (
            'nvidia-gpu' in analysis.detected_operators or 'gpu' in text
        )

    def _build_search_text(self, chart_path) -> str:
        """Concatenate all YAML/template files for keyword scanning."""
        texts = []
        for ext in ('*.yaml', '*.yml', '*.tpl'):
            for f in chart_path.rglob(ext):
                try:
                    texts.append(f.read_text(errors='ignore'))
                except Exception:
                    pass
        return '\n'.join(texts)
