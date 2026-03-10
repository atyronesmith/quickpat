"""
Skill: Transform AI Quickstart to Validated Pattern

Model-agnostic Python skill composed of chainable sub-skills.
Each sub-skill can run independently or as part of the full pipeline.

Sub-skills:
    skill_analyze    — Parse quickstart Helm chart
    skill_detect     — Identify operators and secrets (optional LLM)
    skill_generate   — Produce pattern files
    skill_validate   — Check correctness and auto-fix (optional LLM)
    transform        — Full pipeline chaining all sub-skills

Usage:
    # Full pipeline (deterministic)
    from skills.transform_quickstart import transform
    result = transform("/path/to/quickstart")

    # Full pipeline with LLM validation loop
    result = transform("/path/to/quickstart", llm=my_llm)

    # Individual sub-skills
    from skills.transform_quickstart import skill_analyze, skill_detect, skill_generate
    analysis = skill_analyze("/path/to/quickstart")
    operators, secrets_review = skill_detect(analysis, llm=my_llm)
    output_dir = skill_generate(analysis, config)

    # Validate any existing pattern
    from skills.skill_validate import validate, validate_and_fix
    result = validate("/path/to/pattern")
    result = validate_and_fix("/path/to/pattern", llm=my_llm)
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Add parent directory so we can import quickpat
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quickpat.analyzer import QuickstartAnalyzer, QuickstartAnalysis
from quickpat.config import get as cfg
from quickpat.generator import PatternGenerator, build_report
from quickpat.operators import OPERATORS

from skill_validate import validate_and_fix, validate, ValidationResult


# Type alias: an LLM callable takes (system, user) -> str
# or with structured output: (system, user, response_schema=schema) -> dict
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


# ── Sub-skill: Analyze ──────────────────────────────────────────────


def skill_analyze(quickstart_path: str) -> QuickstartAnalysis:
    """Sub-skill: Analyze a quickstart Helm chart.

    Locates Chart.yaml, parses dependencies, values, and detects features.
    Pure deterministic — no LLM needed.

    Args:
        quickstart_path: Path to the quickstart repository root.

    Returns:
        QuickstartAnalysis with all detected information.

    Raises:
        FileNotFoundError: If no Chart.yaml found.
    """
    analyzer = QuickstartAnalyzer(quickstart_path)
    return analyzer.analyze()


# ── Sub-skill: Detect ───────────────────────────────────────────────


def skill_detect(
    analysis: QuickstartAnalysis,
    llm: LLMCallable = None,
) -> tuple:
    """Sub-skill: Detect operators and review secrets.

    Deterministic detection is already done by skill_analyze.
    When an LLM is provided, it checks for additional operators from
    unusual dependencies and reviews secrets for false positives.

    Args:
        analysis: Output from skill_analyze.
        llm: Optional LLM for enhanced detection.

    Returns:
        Tuple of (operators: list, secrets_review: str).
    """
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
    """Sub-skill: Generate pattern files.

    Pure deterministic — produces all pattern files from analysis + config.

    Args:
        analysis: Output from skill_analyze.
        config: Pattern configuration dict with keys:
            pattern_name, app_name, app_namespace, operators,
            chart_strategy, use_vault, output_dir, clustergroup_version

    Returns:
        The output directory path.
    """
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
    """Full pipeline: analyze -> detect -> generate -> validate/fix.

    Chains all sub-skills. When an LLM is provided, it enhances detection
    and runs a self-correcting validation loop after generation.

    Args:
        quickstart_path: Path to the quickstart repository root.
        output_dir: Where to write the pattern. Defaults to ~/patterns/<name>-pattern/.
        pattern_name: Override the pattern name. Defaults to <chart-name>-pattern.
        llm: Optional callable(system, user) -> str for edge-case reasoning.
        use_vault: Enable HashiCorp Vault for secrets management.
        chart_strategy: "local" (copy chart) or "external" (reference by URL).
        auto_fix: Run self-correcting validation loop (default: True).
        max_fix_iterations: Max fix loop iterations (default: 3).

    Returns:
        TransformResult with details of what was generated.
    """
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
        # Fallback: text parsing for adapters without structured output
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
        # List actual chart dirs from the output
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


# ── LLM adapters ────────────────────────────────────────────────────


def make_openai_llm(model: str = None, api_key: str = None):
    """Create an LLM callable using OpenAI's API.

    Supports structured output via response_schema kwarg.
    Also works with vLLM's OpenAI-compatible API (pass api_key and
    set OPENAI_BASE_URL or use openai.OpenAI(base_url=...)).
    """
    import json as _json
    import openai
    model = model or cfg("llm.openai.model", "gpt-4o-mini")
    api_key = api_key or cfg("llm.openai.api_key") or None
    client = openai.OpenAI(api_key=api_key)

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if response_schema:
            return _json.loads(content)
        return content
    return call


def make_anthropic_llm(model: str = None, api_key: str = None):
    """Create an LLM callable using Anthropic's API.

    Supports structured output via response_schema kwarg (uses tool_use).
    """
    import anthropic
    model = model or cfg("llm.anthropic.model", "claude-sonnet-4-20250514")
    api_key = api_key or cfg("llm.anthropic.api_key") or None
    client = anthropic.Anthropic(api_key=api_key)

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if response_schema:
            kwargs["tools"] = [{
                "name": "structured_response",
                "description": "Provide a structured response",
                "input_schema": response_schema,
            }]
            kwargs["tool_choice"] = {
                "type": "tool", "name": "structured_response",
            }
        response = client.messages.create(**kwargs)
        if response_schema:
            for block in response.content:
                if block.type == "tool_use":
                    return block.input
            return {}
        return response.content[0].text
    return call


def make_ollama_llm(model: str = None, base_url: str = None):
    """Create an LLM callable using a local Ollama instance.

    Supports structured output via response_schema kwarg.
    """
    import json as _json
    import urllib.request
    model = model or cfg("llm.ollama.model", "llama3.1")
    base_url = base_url or cfg("llm.ollama.base_url", "http://localhost:11434")

    def call(system: str, user: str, response_schema: dict = None):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if response_schema:
            payload["format"] = response_schema
        data = _json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            result = _json.loads(resp.read())
        content = result["message"]["content"]
        if response_schema:
            return _json.loads(content)
        return content
    return call


def make_vllm_llm(model: str = None, base_url: str = None):
    """Create an LLM callable using vLLM's OpenAI-compatible API.

    Supports structured output via guided_json in extra_body.
    """
    import json as _json
    import openai
    model = model or cfg("llm.vllm.model", "default")
    base_url = base_url or cfg("llm.vllm.base_url", "http://localhost:8000")
    client = openai.OpenAI(api_key="unused", base_url=f"{base_url}/v1")

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema:
            kwargs["extra_body"] = {"guided_json": response_schema}
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if response_schema:
            return _json.loads(content)
        return content
    return call


def make_deepinfra_llm(model: str = None, api_key: str = None):
    """Create an LLM callable using DeepInfra's OpenAI-compatible API.

    Supports structured output via response_format with JSON schema.
    Uses DEEPINFRA_API_KEY env var or config file if api_key not provided.
    Model names use HuggingFace format (e.g. Qwen/Qwen2.5-72B-Instruct).
    """
    import json as _json
    import os
    import openai

    model = model or cfg("llm.deepinfra.model", "Qwen/Qwen2.5-72B-Instruct")
    key = api_key or cfg("llm.deepinfra.api_key") or os.environ.get("DEEPINFRA_API_KEY")
    if not key:
        raise ValueError(
            "DeepInfra API key required. Set DEEPINFRA_API_KEY env var "
            "or pass api_key parameter."
        )
    client = openai.OpenAI(
        api_key=key,
        base_url="https://api.deepinfra.com/v1/openai",
    )

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": response_schema,
                },
            }
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if response_schema:
            return _json.loads(content)
        return content
    return call


# ── CLI ─────────────────────────────────────────────────────────────


def _build_llm(args):
    model = args.model or None  # None = use config default
    url = getattr(args, "llm_url", None)
    if args.llm == "openai":
        return make_openai_llm(model=model)
    elif args.llm == "anthropic":
        return make_anthropic_llm(model=model)
    elif args.llm == "ollama":
        return make_ollama_llm(model=model, base_url=url)
    elif args.llm == "vllm":
        return make_vllm_llm(model=model, base_url=url)
    elif args.llm == "deepinfra":
        return make_deepinfra_llm(model=model)
    return None


def _print_transform_result(result: TransformResult):
    if result.success:
        print(f"Pattern generated: {result.pattern_dir}/")
        print(f"Files: {len(result.files_created)}")
        for f in result.files_created:
            print(f"  {f}")
        if result.llm_decisions:
            print("\nLLM decisions:")
            for d in result.llm_decisions:
                print(f"  {d}")
        if result.validation and result.validation.fixes_applied:
            print(f"\nAuto-fixes applied: {result.validation.fixes_applied}")
            print(f"Validation iterations: {result.validation.iterations}")
        if result.warnings:
            print("\nWarnings:")
            for w in result.warnings:
                print(f"  {w}")
    else:
        print("Transform failed:")
        for w in result.warnings:
            print(f"  {w}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Transform AI Quickstarts into Validated Patterns"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Common args
    def add_llm_args(p):
        p.add_argument(
            "--llm", choices=["none", "openai", "anthropic", "ollama", "vllm", "deepinfra"],
            default="none", help="LLM provider (default: none)",
        )
        p.add_argument("--model", help="Model name override for LLM provider")
        p.add_argument("--llm-url", help="Base URL for vLLM or Ollama server")

    # transform (default when no subcommand)
    t_parser = subparsers.add_parser("transform", help="Full pipeline: analyze -> detect -> generate -> validate")
    t_parser.add_argument("path", help="Path to quickstart repository")
    t_parser.add_argument("--output", "-o", help="Output directory")
    t_parser.add_argument("--name", help="Pattern name")
    t_parser.add_argument("--no-vault", action="store_true", help="Disable vault")
    t_parser.add_argument("--no-fix", action="store_true", help="Skip auto-fix loop")
    add_llm_args(t_parser)

    # analyze
    a_parser = subparsers.add_parser("analyze", help="Sub-skill: Analyze a quickstart")
    a_parser.add_argument("path", help="Path to quickstart repository")

    # detect
    d_parser = subparsers.add_parser("detect", help="Sub-skill: Detect operators and secrets")
    d_parser.add_argument("path", help="Path to quickstart repository")
    add_llm_args(d_parser)

    # validate
    v_parser = subparsers.add_parser("validate", help="Sub-skill: Validate a pattern")
    v_parser.add_argument("path", help="Path to pattern directory")
    v_parser.add_argument("--fix", action="store_true", help="Auto-fix issues")
    v_parser.add_argument("--max-iterations", type=int, default=3, help="Max fix iterations")
    add_llm_args(v_parser)

    args = parser.parse_args()

    # Default to transform if no subcommand but args present
    if not args.command:
        # If called with just a path (backwards compat)
        remaining = sys.argv[1:]
        if remaining and not remaining[0].startswith("-"):
            args.command = "transform"
            args.path = remaining[0]
            args.output = None
            args.name = None
            args.no_vault = False
            args.no_fix = False
            args.llm = "none"
            args.model = None
        else:
            parser.print_help()
            sys.exit(1)

    if args.command == "transform":
        llm = _build_llm(args)
        result = transform(
            quickstart_path=args.path,
            output_dir=args.output,
            pattern_name=args.name,
            llm=llm,
            use_vault=not args.no_vault,
            auto_fix=not args.no_fix,
        )
        _print_transform_result(result)
        sys.exit(0 if result.success else 1)

    elif args.command == "analyze":
        try:
            analysis = skill_analyze(args.path)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"Chart: {analysis.name} v{analysis.version}")
        if analysis.description:
            print(f"Description: {analysis.description}")
        print(f"Location: {analysis.chart_path}")
        if analysis.dependencies:
            print(f"\nDependencies ({len(analysis.dependencies)}):")
            for dep in analysis.dependencies:
                repo = f" (from {dep.repository})" if dep.repository else ""
                print(f"  - {dep.name} {dep.version}{repo}")
        if analysis.detected_operators:
            print("\nDetected operators:")
            for op_key in analysis.detected_operators:
                print(f"  - {OPERATORS[op_key]['display_name']}")
        if analysis.detected_secrets:
            print("\nDetected secrets:")
            for s in analysis.detected_secrets:
                print(f"  - {s.name} (at {s.path})")
        features = []
        if analysis.has_vector_db: features.append("Vector DB")
        if analysis.has_llm_service: features.append("LLM Serving")
        if analysis.has_object_storage: features.append("Object Storage")
        if analysis.has_pipeline: features.append("Data Pipeline")
        if analysis.has_gpu_requirement: features.append("GPU Required")
        if features:
            print(f"\nFeatures: {', '.join(features)}")

    elif args.command == "detect":
        try:
            analysis = skill_analyze(args.path)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        llm = _build_llm(args)
        operators, secrets_review = skill_detect(analysis, llm)

        print("Operators:")
        for op_key in operators:
            print(f"  - {OPERATORS[op_key]['display_name']} ({op_key})")
        if secrets_review:
            print(f"\nSecrets review: {secrets_review}")

    elif args.command == "validate":
        from skill_validate import _print_result
        llm = _build_llm(args)

        if args.fix:
            result = validate_and_fix(
                args.path, llm=llm, max_iterations=args.max_iterations,
            )
        else:
            result = validate(args.path, llm=llm)

        _print_result(result)
        sys.exit(0 if result.valid else 1)
