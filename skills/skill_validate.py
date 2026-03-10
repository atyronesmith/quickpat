"""
Sub-skill: Validate a Validated Pattern

Runs deterministic structural checks and optional LLM-powered semantic
review with a self-correcting fix loop.

Usage:
    # Deterministic only
    from skills.skill_validate import validate
    result = validate("/path/to/pattern")

    # With LLM review + auto-fix loop
    from skills.skill_validate import validate_and_fix
    result = validate_and_fix("/path/to/pattern", llm=my_llm)

    # CLI
    python skills/skill_validate.py /path/to/pattern
    python skills/skill_validate.py /path/to/pattern --llm ollama --fix
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml


LLMCallable = Callable


@dataclass
class Issue:
    """A single validation finding."""
    file: str
    severity: str  # "error" or "warning"
    message: str
    auto_fixable: bool = False
    fix_applied: bool = False


@dataclass
class ValidationResult:
    """Full validation outcome."""
    valid: bool
    issues: list = field(default_factory=list)
    fixes_applied: int = 0
    iterations: int = 0


# ── Public API ───────────────────────────────────────────────────────


def validate(pattern_dir: str, config: dict = None, llm: LLMCallable = None) -> ValidationResult:
    """Validate a pattern directory. Returns issues found."""
    out = Path(pattern_dir)
    if not out.is_dir():
        return ValidationResult(valid=False, issues=[
            Issue("", "error", f"Directory does not exist: {pattern_dir}")
        ])

    issues = []
    issues.extend(_check_file_structure(out))
    issues.extend(_check_values_global(out))
    issues.extend(_check_values_hub(out, config))
    issues.extend(_check_values_secret(out))
    issues.extend(_check_makefile(out))
    issues.extend(_check_pattern_sh(out))
    issues.extend(_check_overrides(out))
    issues.extend(_check_no_legacy(out))

    if llm:
        issues.extend(_llm_review(out, llm))

    return ValidationResult(
        valid=not any(i.severity == "error" for i in issues),
        issues=issues,
        iterations=1,
    )


def validate_and_fix(
    pattern_dir: str,
    config: dict = None,
    llm: LLMCallable = None,
    max_iterations: int = 3,
) -> ValidationResult:
    """Validate, auto-fix issues, and re-validate in a loop."""
    total_fixes = 0

    for i in range(max_iterations):
        result = validate(pattern_dir, config, llm)
        result.iterations = i + 1

        fixable = [issue for issue in result.issues if issue.auto_fixable]
        if not fixable:
            break

        fixed = _apply_fixes(Path(pattern_dir), fixable)
        total_fixes += fixed
        if fixed == 0:
            break  # nothing was actually fixed, stop looping

    result.fixes_applied = total_fixes
    return result


# ── Deterministic checks ────────────────────────────────────────────


def _check_file_structure(out: Path) -> list:
    issues = []
    required = [
        "values-global.yaml",
        "values-hub.yaml",
        "Makefile",
        "pattern.sh",
        "Makefile-common",
        "ansible.cfg",
    ]
    for f in required:
        if not (out / f).exists():
            issues.append(Issue(f, "error", f"Missing required file: {f}"))
    return issues


def _check_values_global(out: Path) -> list:
    issues = []
    path = out / "values-global.yaml"
    if not path.exists():
        return issues  # already caught by file structure check

    data = _load_yaml(path)
    if not data:
        issues.append(Issue("values-global.yaml", "error", "File is empty or invalid YAML"))
        return issues

    # main: must be at root level
    if "main" not in data:
        # Check if it's incorrectly nested under global:
        global_data = data.get("global", {})
        if isinstance(global_data, dict) and "main" in global_data:
            issues.append(Issue(
                "values-global.yaml", "error",
                "main: is nested under global: — must be a root-level key",
                auto_fixable=True,
            ))
        else:
            issues.append(Issue(
                "values-global.yaml", "error",
                "Missing main: key at root level",
            ))
    else:
        main = data["main"]
        if not isinstance(main, dict):
            issues.append(Issue("values-global.yaml", "error", "main: must be a dict"))
        else:
            msc = main.get("multiSourceConfig", {})
            if not msc.get("enabled"):
                issues.append(Issue(
                    "values-global.yaml", "error",
                    "multiSourceConfig.enabled must be true",
                    auto_fixable=True,
                ))
            if "clusterGroupChartVersion" not in msc:
                issues.append(Issue(
                    "values-global.yaml", "warning",
                    "Missing clusterGroupChartVersion in multiSourceConfig",
                    auto_fixable=True,
                ))

    return issues


def _check_values_hub(out: Path, config: dict = None) -> list:
    issues = []
    path = out / "values-hub.yaml"
    if not path.exists():
        return issues

    data = _load_yaml(path)
    if not data:
        issues.append(Issue("values-hub.yaml", "error", "File is empty or invalid YAML"))
        return issues

    cg = data.get("clusterGroup", {})
    if not cg:
        issues.append(Issue("values-hub.yaml", "error", "Missing clusterGroup: key"))
        return issues

    # projects must be a list
    projects = cg.get("projects")
    if projects is None:
        issues.append(Issue("values-hub.yaml", "warning", "Missing projects: key"))
    elif not isinstance(projects, list):
        issues.append(Issue("values-hub.yaml", "error", "projects: must be a list"))

    # sharedValueFiles
    svf = cg.get("sharedValueFiles")
    if not svf:
        issues.append(Issue(
            "values-hub.yaml", "warning",
            "Missing sharedValueFiles — platform overrides won't load",
            auto_fixable=True,
        ))

    # subscriptions must be a dict
    subs = cg.get("subscriptions")
    if subs is not None and not isinstance(subs, dict):
        issues.append(Issue(
            "values-hub.yaml", "error",
            "subscriptions: must be a dict (not a list)",
        ))

    # applications check
    apps = cg.get("applications", {})

    # Infrastructure apps: vault and external-secrets should use chart:, not path:
    for infra_app in ("vault", "golang-external-secrets"):
        if infra_app in apps:
            app = apps[infra_app]
            if "path" in app and "chart" not in app:
                issues.append(Issue(
                    "values-hub.yaml", "error",
                    f"Infrastructure app '{infra_app}' uses path: but should use chart: + chartVersion:",
                    auto_fixable=True,
                ))

    # Application chart: should use path: (local) or repoURL: (external)
    for app_name, app in apps.items():
        if app_name in ("vault", "golang-external-secrets"):
            continue
        has_path = "path" in app
        has_repo = "repoURL" in app or "chart" in app
        if not has_path and not has_repo:
            issues.append(Issue(
                "values-hub.yaml", "warning",
                f"App '{app_name}' has neither path: nor repoURL:/chart:",
            ))
        # Local chart path should be charts/all/
        if has_path and not app["path"].startswith("charts/all/"):
            issues.append(Issue(
                "values-hub.yaml", "error",
                f"App '{app_name}' path should start with charts/all/, got: {app['path']}",
                auto_fixable=True,
            ))

    return issues


def _check_values_secret(out: Path) -> list:
    issues = []
    path = out / "values-secret.yaml.template"
    if not path.exists():
        return issues  # optional file

    data = _load_yaml(path)
    if not data:
        issues.append(Issue("values-secret.yaml.template", "error", "File is empty or invalid YAML"))
        return issues

    # version must be "2.0"
    version = data.get("version")
    if version is None:
        issues.append(Issue(
            "values-secret.yaml.template", "error",
            "Missing version: key — must be '2.0' (defaults to deprecated 1.0)",
            auto_fixable=True,
        ))
    elif str(version) != "2.0":
        issues.append(Issue(
            "values-secret.yaml.template", "error",
            f"version: is '{version}' — must be '2.0'",
            auto_fixable=True,
        ))

    # Check each secret entry
    for secret in data.get("secrets", []):
        # Must use vaultPrefixes (plural, list), not vaultPrefixOverride
        if "vaultPrefixOverride" in secret:
            issues.append(Issue(
                "values-secret.yaml.template", "error",
                "Uses vaultPrefixOverride — must be vaultPrefixes (plural, list)",
                auto_fixable=True,
            ))
        prefixes = secret.get("vaultPrefixes")
        if prefixes is not None and not isinstance(prefixes, list):
            issues.append(Issue(
                "values-secret.yaml.template", "error",
                "vaultPrefixes must be a list",
                auto_fixable=True,
            ))

    return issues


def _check_makefile(out: Path) -> list:
    issues = []
    path = out / "Makefile"
    if not path.exists():
        return issues

    content = path.read_text().strip()
    if "include common/Makefile" in content:
        issues.append(Issue(
            "Makefile", "error",
            "Uses 'include common/Makefile' — must be 'include Makefile-common'",
            auto_fixable=True,
        ))
    elif "include Makefile-common" not in content:
        issues.append(Issue(
            "Makefile", "warning",
            "Makefile does not include Makefile-common",
        ))

    mc_path = out / "Makefile-common"
    if mc_path.exists():
        mc_content = mc_path.read_text()
        if "rhvp.cluster_utils" not in mc_content:
            issues.append(Issue(
                "Makefile-common", "warning",
                "Makefile-common does not reference rhvp.cluster_utils Ansible collection",
            ))

    return issues


def _check_pattern_sh(out: Path) -> list:
    issues = []
    path = out / "pattern.sh"
    if not path.exists():
        return issues

    if not path.stat().st_mode & 0o111:
        issues.append(Issue(
            "pattern.sh", "error",
            "pattern.sh is not executable",
            auto_fixable=True,
        ))

    content = path.read_text()
    if "utility-container" not in content:
        issues.append(Issue(
            "pattern.sh", "warning",
            "pattern.sh does not reference the utility container",
        ))

    return issues


def _check_overrides(out: Path) -> list:
    issues = []
    overrides = out / "overrides"

    if not overrides.is_dir():
        issues.append(Issue(
            "overrides/", "warning",
            "Missing overrides/ directory for platform-specific values",
            auto_fixable=True,
        ))
        return issues

    for platform in ("AWS", "Azure", "GCP", "IBMCloud", "None"):
        pf = overrides / f"values-{platform}.yaml"
        if not pf.exists():
            issues.append(Issue(
                f"overrides/values-{platform}.yaml", "warning",
                f"Missing platform override file for {platform}",
                auto_fixable=True,
            ))

    return issues


def _check_no_legacy(out: Path) -> list:
    issues = []

    if (out / "common").is_dir():
        issues.append(Issue(
            "common/", "warning",
            "common/ directory exists — not needed with multisource configuration",
        ))

    if (out / "setup-common.sh").exists():
        issues.append(Issue(
            "setup-common.sh", "warning",
            "setup-common.sh is obsolete — modern patterns use multisource",
        ))

    return issues


# ── LLM review ──────────────────────────────────────────────────────


VALIDATION_CHECKLIST = """
Validate this Validated Pattern against these rules:

1. values-global.yaml: main: must be a root-level key (sibling of global:, NOT nested under it)
2. values-global.yaml: multiSourceConfig.enabled must be true
3. values-hub.yaml: vault + golang-external-secrets apps must be present if vault is enabled
4. values-hub.yaml: Infrastructure apps (vault, external-secrets) must use chart: + chartVersion: (NOT path:)
5. values-hub.yaml: Application apps should use path: charts/all/<name> (local) or repoURL: (external)
6. values-hub.yaml: sharedValueFiles must reference the overrides template
7. values-hub.yaml: Operators needing dedicated namespaces must have operatorGroup: true
8. values-hub.yaml: projects: must be a list (not a string)
9. values-hub.yaml: subscriptions: must be a dict (not a list)
10. values-secret.yaml.template: Must have version: "2.0"
11. values-secret.yaml.template: Must use vaultPrefixes: (plural, list) — NOT vaultPrefixOverride
12. Makefile: Should contain only "include Makefile-common"
13. No common/ directory (not needed with multisource)
14. No charts/hub/ path (should be charts/all/)

For each issue found, respond with exactly one line per issue in this format:
ISSUE|<filename>|<error or warning>|<description>

If everything is valid, respond with exactly: VALID
"""


VALIDATION_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean", "description": "Whether the pattern is valid"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["error", "warning"],
                    },
                    "message": {"type": "string"},
                },
                "required": ["file", "severity", "message"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["valid", "issues"],
    "additionalProperties": False,
}


def _llm_review(out: Path, llm: LLMCallable) -> list:
    """Ask the LLM to review generated files against the validation checklist."""
    # Gather file contents
    files_content = []
    for fname in ("values-global.yaml", "values-hub.yaml", "values-secret.yaml.template", "Makefile"):
        fpath = out / fname
        if fpath.exists():
            content = fpath.read_text()
            files_content.append(f"--- {fname} ---\n{content}")

    if not files_content:
        return []

    user_msg = "Review these pattern files:\n\n" + "\n\n".join(files_content)

    try:
        result = llm(
            VALIDATION_CHECKLIST, user_msg,
            response_schema=VALIDATION_REVIEW_SCHEMA,
        )
    except Exception as e:
        return [Issue("", "warning", f"LLM review failed: {e}")]

    # Structured output: result is a dict
    if isinstance(result, dict):
        return _parse_structured_review(result)

    # Fallback: text parsing for adapters without structured output
    return _parse_text_review(result)


def _parse_structured_review(data: dict) -> list:
    """Parse structured validation review response."""
    if data.get("valid") and not data.get("issues"):
        return []

    issues = []
    for item in data.get("issues", []):
        severity = item.get("severity", "warning")
        if severity not in ("error", "warning"):
            severity = "warning"
        issues.append(Issue(
            file=item.get("file", ""),
            severity=severity,
            message=f"[LLM] {item.get('message', '')}",
        ))
    return issues


def _parse_text_review(response: str) -> list:
    """Parse free-text validation review response (fallback)."""
    response = response.strip()
    if response == "VALID":
        return []

    issues = []
    for line in response.splitlines():
        line = line.strip()
        if not line.startswith("ISSUE|"):
            continue
        parts = line.split("|", 3)
        if len(parts) == 4:
            _, filename, severity, description = parts
            severity = severity.strip().lower()
            if severity not in ("error", "warning"):
                severity = "warning"
            issues.append(Issue(
                file=filename.strip(),
                severity=severity,
                message=f"[LLM] {description.strip()}",
            ))
    return issues


# ── Auto-fix ────────────────────────────────────────────────────────


def _apply_fixes(out: Path, issues: list) -> int:
    """Apply auto-fixes for known issue types. Returns count of fixes applied."""
    fixed = 0

    for issue in issues:
        if issue.fix_applied:
            continue

        if _try_fix(out, issue):
            issue.fix_applied = True
            fixed += 1

    return fixed


def _try_fix(out: Path, issue: Issue) -> bool:
    """Attempt to fix a single issue. Returns True if fixed."""

    # values-global.yaml: main nested under global
    if issue.file == "values-global.yaml" and "nested under global" in issue.message:
        return _fix_main_nesting(out / "values-global.yaml")

    # values-global.yaml: multiSourceConfig not enabled
    if issue.file == "values-global.yaml" and "multiSourceConfig.enabled" in issue.message:
        return _fix_multisource_enabled(out / "values-global.yaml")

    # values-global.yaml: missing clusterGroupChartVersion
    if issue.file == "values-global.yaml" and "clusterGroupChartVersion" in issue.message:
        return _fix_chart_version(out / "values-global.yaml")

    # values-secret: missing or wrong version
    if issue.file == "values-secret.yaml.template" and "version" in issue.message.lower():
        return _fix_secret_version(out / "values-secret.yaml.template")

    # values-secret: vaultPrefixOverride -> vaultPrefixes
    if issue.file == "values-secret.yaml.template" and "vaultPrefixOverride" in issue.message:
        return _fix_vault_prefix(out / "values-secret.yaml.template")

    # values-secret: vaultPrefixes not a list
    if issue.file == "values-secret.yaml.template" and "vaultPrefixes must be a list" in issue.message:
        return _fix_vault_prefix_type(out / "values-secret.yaml.template")

    # Makefile: wrong include
    if issue.file == "Makefile" and "common/Makefile" in issue.message:
        return _fix_makefile_include(out / "Makefile")

    # pattern.sh not executable
    if issue.file == "pattern.sh" and "not executable" in issue.message:
        (out / "pattern.sh").chmod(0o755)
        return True

    # values-hub.yaml: chart path not charts/all/
    if issue.file == "values-hub.yaml" and "charts/all/" in issue.message:
        return _fix_chart_path(out / "values-hub.yaml")

    # values-hub.yaml: missing sharedValueFiles
    if issue.file == "values-hub.yaml" and "sharedValueFiles" in issue.message:
        return _fix_shared_value_files(out / "values-hub.yaml")

    # values-hub.yaml: infra app uses path instead of chart
    if issue.file == "values-hub.yaml" and "should use chart:" in issue.message:
        return _fix_infra_app_chart(out / "values-hub.yaml", issue.message)

    # Missing overrides directory or files
    if "overrides/" in issue.file or "overrides/ directory" in issue.message:
        return _fix_overrides(out, issue)

    return False


def _fix_main_nesting(path: Path) -> bool:
    """Move main: from under global: to root level."""
    data = _load_yaml(path)
    if not data:
        return False

    global_data = data.get("global", {})
    if not isinstance(global_data, dict) or "main" not in global_data:
        return False

    main_data = global_data.pop("main")
    data["main"] = main_data
    _save_yaml(path, data, doc_start=True)
    return True


def _fix_multisource_enabled(path: Path) -> bool:
    data = _load_yaml(path)
    if not data or "main" not in data:
        return False
    main = data["main"]
    if not isinstance(main, dict):
        return False
    msc = main.setdefault("multiSourceConfig", {})
    msc["enabled"] = True
    _save_yaml(path, data, doc_start=True)
    return True


def _fix_chart_version(path: Path) -> bool:
    data = _load_yaml(path)
    if not data or "main" not in data:
        return False
    msc = data["main"].setdefault("multiSourceConfig", {})
    msc.setdefault("clusterGroupChartVersion", "0.9.*")
    _save_yaml(path, data, doc_start=True)
    return True


def _fix_secret_version(path: Path) -> bool:
    data = _load_yaml(path)
    if not data:
        return False
    data["version"] = "2.0"
    _save_yaml(path, data)
    return True


def _fix_vault_prefix(path: Path) -> bool:
    data = _load_yaml(path)
    if not data:
        return False
    changed = False
    for secret in data.get("secrets", []):
        if "vaultPrefixOverride" in secret:
            old = secret.pop("vaultPrefixOverride")
            if isinstance(old, str):
                secret["vaultPrefixes"] = [old]
            elif isinstance(old, list):
                secret["vaultPrefixes"] = old
            else:
                secret["vaultPrefixes"] = ["global"]
            changed = True
    if changed:
        _save_yaml(path, data)
    return changed


def _fix_vault_prefix_type(path: Path) -> bool:
    data = _load_yaml(path)
    if not data:
        return False
    changed = False
    for secret in data.get("secrets", []):
        prefixes = secret.get("vaultPrefixes")
        if prefixes is not None and not isinstance(prefixes, list):
            secret["vaultPrefixes"] = [str(prefixes)]
            changed = True
    if changed:
        _save_yaml(path, data)
    return changed


def _fix_makefile_include(path: Path) -> bool:
    content = path.read_text()
    new_content = content.replace("include common/Makefile", "include Makefile-common")
    if new_content != content:
        path.write_text(new_content)
        return True
    return False


def _fix_chart_path(path: Path) -> bool:
    """Fix application chart paths from charts/hub/ to charts/all/."""
    data = _load_yaml(path)
    if not data:
        return False
    apps = data.get("clusterGroup", {}).get("applications", {})
    changed = False
    for app_name, app in apps.items():
        if app_name in ("vault", "golang-external-secrets"):
            continue
        p = app.get("path", "")
        if p and not p.startswith("charts/all/"):
            # Replace charts/hub/ or charts/<anything>/ with charts/all/
            parts = p.split("/")
            if len(parts) >= 2 and parts[0] == "charts":
                parts[1] = "all"
                app["path"] = "/".join(parts)
                changed = True
    if changed:
        _save_yaml(path, data)
    return changed


def _fix_shared_value_files(path: Path) -> bool:
    data = _load_yaml(path)
    if not data:
        return False
    cg = data.get("clusterGroup", {})
    if "sharedValueFiles" not in cg:
        cg["sharedValueFiles"] = [
            "/overrides/values-{{ $.Values.global.clusterPlatform }}.yaml"
        ]
        _save_yaml(path, data)
        return True
    return False


def _fix_infra_app_chart(path: Path, message: str) -> bool:
    """Fix infrastructure apps to use chart: instead of path:."""
    data = _load_yaml(path)
    if not data:
        return False

    infra_charts = {
        "vault": {"chart": "hashicorp-vault", "chartVersion": "0.1.*"},
        "golang-external-secrets": {"chart": "golang-external-secrets", "chartVersion": "0.2.*"},
    }

    apps = data.get("clusterGroup", {}).get("applications", {})
    changed = False
    for app_name, chart_info in infra_charts.items():
        if app_name in apps and "path" in apps[app_name]:
            apps[app_name].pop("path", None)
            apps[app_name].update(chart_info)
            changed = True

    if changed:
        _save_yaml(path, data)
    return changed


def _fix_overrides(out: Path, issue: Issue) -> bool:
    overrides = out / "overrides"
    overrides.mkdir(exist_ok=True)

    if "directory" in issue.message:
        # Create all platform files
        for platform in ("AWS", "Azure", "GCP", "IBMCloud", "None"):
            pf = overrides / f"values-{platform}.yaml"
            if not pf.exists():
                pf.write_text(f"# Platform-specific overrides for {platform}\n")
        return True

    # Individual platform file
    fname = issue.file.split("/")[-1] if "/" in issue.file else issue.file
    pf = overrides / fname
    if not pf.exists():
        platform = fname.replace("values-", "").replace(".yaml", "")
        pf.write_text(f"# Platform-specific overrides for {platform}\n")
        return True

    return False


# ── YAML helpers ────────────────────────────────────────────────────


def _load_yaml(path: Path) -> Optional[dict]:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return None


def _save_yaml(path: Path, data: dict, doc_start: bool = False):
    with open(path, "w") as f:
        if doc_start:
            f.write("---\n")
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# ── CLI ─────────────────────────────────────────────────────────────


def _print_result(result: ValidationResult):
    if result.valid:
        status = "VALID"
    else:
        status = "INVALID"

    error_count = sum(1 for i in result.issues if i.severity == "error")
    warn_count = sum(1 for i in result.issues if i.severity == "warning")
    print(f"Status: {status} ({error_count} errors, {warn_count} warnings)")

    if result.fixes_applied:
        print(f"Auto-fixes applied: {result.fixes_applied}")
    if result.iterations > 1:
        print(f"Validation iterations: {result.iterations}")

    for issue in result.issues:
        marker = "ERROR" if issue.severity == "error" else "WARN "
        fixed = " [FIXED]" if issue.fix_applied else ""
        print(f"  {marker} {issue.file}: {issue.message}{fixed}")


if __name__ == "__main__":
    import argparse

    # Add parent for quickpat imports (needed by LLM adapters)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    parser = argparse.ArgumentParser(description="Validate a Validated Pattern")
    parser.add_argument("path", help="Path to pattern directory")
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues")
    parser.add_argument(
        "--llm", choices=["none", "openai", "anthropic", "ollama", "vllm"],
        default="none", help="LLM provider for semantic review",
    )
    parser.add_argument("--model", help="Model name override for LLM provider")
    parser.add_argument("--llm-url", help="Base URL for vLLM or Ollama server")
    parser.add_argument(
        "--max-iterations", type=int, default=3,
        help="Max fix iterations (default: 3)",
    )
    args = parser.parse_args()

    llm_callable = None
    if args.llm != "none":
        from transform_quickstart import (
            make_openai_llm, make_anthropic_llm, make_ollama_llm, make_vllm_llm,
        )
        if args.llm == "openai":
            llm_callable = make_openai_llm(model=args.model or "gpt-4o-mini")
        elif args.llm == "anthropic":
            llm_callable = make_anthropic_llm(model=args.model or "claude-sonnet-4-20250514")
        elif args.llm == "ollama":
            kwargs = {"model": args.model or "llama3.1"}
            if args.llm_url:
                kwargs["base_url"] = args.llm_url
            llm_callable = make_ollama_llm(**kwargs)
        elif args.llm == "vllm":
            llm_callable = make_vllm_llm(
                model=args.model or "default",
                base_url=args.llm_url or "http://localhost:8000",
            )

    if args.fix:
        result = validate_and_fix(
            args.path, llm=llm_callable, max_iterations=args.max_iterations,
        )
    else:
        result = validate(args.path, llm=llm_callable)

    _print_result(result)
    sys.exit(0 if result.valid else 1)
