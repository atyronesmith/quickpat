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
    'secretkey',  # often a dict key name, not a secret value
}


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


@dataclass
class QuickstartAnalysis:
    """Results of analyzing an AI Quickstart."""
    name: str = ""
    version: str = ""
    description: str = ""
    chart_path: str = ""
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

        chart_path = self._find_chart()
        analysis.chart_path = str(chart_path)

        self._parse_chart_yaml(analysis, chart_path)
        self._parse_values(analysis, chart_path)

        search_text = self._build_search_text(chart_path)
        self._detect_operators(analysis, search_text)
        self._detect_secrets(analysis)
        self._detect_features(analysis, search_text)

        return analysis

    def _find_chart(self) -> Path:
        """Locate the Chart.yaml in common quickstart layouts."""
        # Known AI Quickstart conventions, checked in priority order
        search_dirs = [
            self.path / 'deploy' / 'helm',    # RAG, ppe-compliance-monitor
            self.path / 'deploy' / 'cluster' / 'helm',  # ai-virtual-agent
            self.path / 'helm',                # llm-cpu-serving
            self.path / 'chart',               # lemonade-stand-assistant
            self.path,                         # Chart.yaml at root
        ]

        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue
            # Check for Chart.yaml directly in this dir
            if (search_dir / 'Chart.yaml').exists():
                return search_dir
            # Check one level of subdirectories (e.g. deploy/helm/rag/)
            for child in sorted(search_dir.iterdir()):
                if child.is_dir() and (child / 'Chart.yaml').exists():
                    return child

        raise FileNotFoundError(
            f"No Chart.yaml found in {self.path}. "
            "Searched: deploy/helm/, deploy/cluster/helm/, helm/, "
            "chart/, root, and their subdirectories."
        )

    def _parse_chart_yaml(self, analysis, chart_path):
        with open(chart_path / 'Chart.yaml') as f:
            chart = yaml.safe_load(f) or {}

        analysis.name = chart.get('name', self.path.name)
        analysis.version = chart.get('version', '0.1.0')
        analysis.description = chart.get('description', '')

        for dep in chart.get('dependencies', []):
            analysis.dependencies.append(ChartDependency(
                name=dep.get('name', ''),
                version=dep.get('version', '*'),
                repository=dep.get('repository', ''),
                condition=dep.get('condition', ''),
            ))

    def _parse_values(self, analysis, chart_path):
        values_file = chart_path / 'values.yaml'
        if values_file.exists():
            with open(values_file) as f:
                analysis.values = yaml.safe_load(f) or {}

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
        self._walk_for_secrets(analysis, analysis.values, '')

    def _walk_for_secrets(self, analysis, obj, path):
        if isinstance(obj, dict):
            for key, value in obj.items():
                current = f"{path}.{key}" if path else key
                key_lower = key.lower()
                if (
                    any(p in key_lower for p in SECRET_PATTERNS)
                    and key_lower not in SECRET_FALSE_POSITIVES
                ):
                    analysis.detected_secrets.append(SecretRef(
                        name=key,
                        path=current,
                        description=f"Potential secret: {key}",
                    ))
                self._walk_for_secrets(analysis, value, current)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._walk_for_secrets(analysis, item, f"{path}[{i}]")

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
