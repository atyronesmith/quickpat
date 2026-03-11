"""Pipeline orchestration for quickpat.

Chains sub-skills: analyze -> detect -> generate -> validate/fix.
Each sub-skill can run independently or as part of the full pipeline.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .analyzer import QuickstartAnalyzer, QuickstartAnalysis
from .config import get as cfg
from .generator import PatternGenerator
from .operators import OPERATORS
from .validator import validate_and_fix, validate, ValidationResult

LLMCallable = Callable


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

OPERATOR_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "operators": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Operator keys to add (from the allowed list)",
        },
    },
    "required": ["operators"],
    "additionalProperties": False,
}

SECRET_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "false_positives": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Secret names that are NOT real secrets",
        },
        "summary": {
            "type": "string",
            "description": "Brief summary of findings",
        },
    },
    "required": ["false_positives", "summary"],
    "additionalProperties": False,
}


# ── Sub-skill: Analyze ──────────────────────────────────────────────


def skill_analyze(quickstart_path: str) -> QuickstartAnalysis:
    """Parse quickstart Helm chart(s). Pure deterministic."""
    analyzer = QuickstartAnalyzer(quickstart_path)
    return analyzer.analyze()


# ── Sub-skill: Detect ───────────────────────────────────────────────


def skill_detect(
    analysis: QuickstartAnalysis,
    llm: LLMCallable = None,
) -> tuple:
    """Detect operators and review secrets. LLM enhances if provided."""
    operators = list(analysis.detected_operators)
    secrets_review = ""

    if llm and analysis.dependencies:
        llm_ops = _llm_check_operators(llm, analysis)
        for op in llm_ops:
            if op in OPERATORS and op not in operators:
                operators.append(op)

    if llm and analysis.detected_secrets:
        secrets_review = _llm_review_secrets(llm, analysis)

    return operators, secrets_review


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
    llm: LLMCallable = None,
    use_vault: bool = True,
    chart_strategy: str = "local",
    auto_fix: bool = True,
    max_fix_iterations: int = 3,
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
        pattern_name = f"{analysis.name}-pattern"
    if not output_dir:
        base = Path(cfg("pattern.output_dir", "~/patterns")).expanduser()
        output_dir = str(base / pattern_name)
    result.pattern_dir = output_dir

    # 3. Detect (with optional LLM)
    operators, secrets_review = skill_detect(analysis, llm)
    if secrets_review:
        result.llm_decisions.append(f"LLM secret review: {secrets_review}")
    if operators != list(analysis.detected_operators):
        added = set(operators) - set(analysis.detected_operators)
        if added:
            result.llm_decisions.append(
                f"LLM suggested additional operators: {sorted(added)}"
            )

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
    result.config = config

    # 5. Generate
    skill_generate(analysis, config)

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


# ── LLM helpers ─────────────────────────────────────────────────────


def _llm_check_operators(llm: LLMCallable, analysis: QuickstartAnalysis) -> list:
    dep_list = "\n".join(
        f"- {d.name} {d.version} (from {d.repository or 'local'})"
        for d in analysis.dependencies
    )
    valid_keys = ", ".join(OPERATORS.keys())
    system = (
        "You are an OpenShift operator expert. Given a list of Helm chart "
        "dependencies, identify any that require OpenShift operators not "
        f"already detected. Only use operator keys from this list: {valid_keys}. "
        "Return an empty list if none are needed."
    )
    user = (
        f"Chart: {analysis.name}\n"
        f"Already detected operators: {analysis.detected_operators}\n"
        f"Dependencies:\n{dep_list}"
    )
    try:
        result = llm(system, user, response_schema=OPERATOR_CHECK_SCHEMA)
        if isinstance(result, dict):
            return [k for k in result.get("operators", []) if k in OPERATORS]
        response = result.strip().lower()
        if response == "none":
            return []
        candidates = [k.strip() for k in response.split(",")]
        return [k for k in candidates if k in OPERATORS]
    except Exception:
        return []


def _llm_review_secrets(llm: LLMCallable, analysis: QuickstartAnalysis) -> str:
    secret_list = "\n".join(
        f"- {s.name} (at {s.path})" for s in analysis.detected_secrets
    )
    system = (
        "You are a security reviewer. Given a list of detected potential "
        "secrets from a Helm values.yaml, identify any false positives "
        "(keys that look like secrets but aren't). Be brief."
    )
    user = f"Chart: {analysis.name}\nDetected secrets:\n{secret_list}"
    try:
        result = llm(system, user, response_schema=SECRET_REVIEW_SCHEMA)
        if isinstance(result, dict):
            summary = result.get("summary", "")
            fps = result.get("false_positives", [])
            if fps:
                return f"{summary} False positives: {', '.join(fps)}"
            return summary
        return result.strip()
    except Exception:
        return ""


def _list_created_files(output_dir: str, config: dict) -> list:
    files = [
        "values-global.yaml",
        "values-hub.yaml",
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
    for platform in cfg("platforms", ["AWS", "Azure", "GCP", "IBMCloud", "None"]):
        files.append(f"overrides/values-{platform}.yaml")
    return files
