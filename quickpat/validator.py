"""Validate a Validated Pattern directory.

Runs deterministic structural checks and optional LLM-powered semantic
review with a self-correcting fix loop.

Usage:
    from quickpat.validator import validate, validate_and_fix
    result = validate("/path/to/pattern")
    result = validate_and_fix("/path/to/pattern", llm=my_llm)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .config import get as cfg
from .providers.base import Provider


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


def validate(pattern_dir: str, config: dict = None, llm: Provider = None) -> ValidationResult:
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
    issues.extend(_check_namespace_format(out))
    issues.extend(_check_chart_path_convention(out))
    issues.extend(_check_secrets_chart_values(out))
    issues.extend(_check_single_argocd(out))
    issues.extend(_check_eso_backtick_escaping(out))

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
    llm: Provider = None,
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
            break

    result.fixes_applied = total_fixes
    return result


# ── Deterministic checks ────────────────────────────────────────────


def _find_values_group_file(out: Path) -> str:
    """Find the values-{clusterGroupName}.yaml file."""
    global_path = out / "values-global.yaml"
    if global_path.exists():
        data = _load_yaml(global_path)
        if data:
            group_name = (data.get("main") or {}).get("clusterGroupName", "prod")
            candidate = f"values-{group_name}.yaml"
            if (out / candidate).exists():
                return candidate
    for name in ("values-prod.yaml", "values-hub.yaml"):
        if (out / name).exists():
            return name
    for p in out.glob("values-*.yaml"):
        name = p.name
        if name not in ("values-global.yaml", "values-secret.yaml.template"):
            return name
    return "values-prod.yaml"


def _check_file_structure(out: Path) -> list:
    issues = []
    values_hub = _find_values_group_file(out)
    required = [
        "values-global.yaml",
        values_hub,
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
        return issues

    data = _load_yaml(path)
    if not data:
        issues.append(Issue("values-global.yaml", "error", "File is empty or invalid YAML"))
        return issues

    if "main" not in data:
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
    hub_file = _find_values_group_file(out)
    path = out / hub_file
    if not path.exists():
        return issues

    data = _load_yaml(path)
    if not data:
        issues.append(Issue(hub_file, "error", "File is empty or invalid YAML"))
        return issues

    cg = data.get("clusterGroup", {})
    if not cg:
        issues.append(Issue(hub_file, "error", "Missing clusterGroup: key"))
        return issues

    projects = cg.get("projects")
    if projects is not None and not isinstance(projects, list):
        issues.append(Issue(hub_file, "error", "projects: must be a list"))

    subs = cg.get("subscriptions")
    if subs is not None and not isinstance(subs, dict):
        issues.append(Issue(
            hub_file, "error",
            "subscriptions: must be a dict (not a list)",
        ))

    apps = cg.get("applications", {})

    for infra_app in ("vault", "openshift-external-secrets", "golang-external-secrets"):
        if infra_app in apps:
            app = apps[infra_app]
            if "path" in app and "chart" not in app:
                issues.append(Issue(
                    hub_file, "error",
                    f"Infrastructure app '{infra_app}' uses path: but should use chart: + chartVersion:",
                    auto_fixable=True,
                ))

    for app_name, app in apps.items():
        if app_name in ("vault", "openshift-external-secrets", "golang-external-secrets"):
            continue
        has_path = "path" in app
        has_repo = "repoURL" in app or "chart" in app
        if not has_path and not has_repo:
            issues.append(Issue(
                hub_file, "warning",
                f"App '{app_name}' has neither path: nor repoURL:/chart:",
            ))
        if has_path and "repoURL" not in app and not app["path"].startswith("charts/"):
            issues.append(Issue(
                hub_file, "error",
                f"App '{app_name}' path should start with charts/, got: {app['path']}",
                auto_fixable=True,
            ))

    # Remote strategy checks
    issues.extend(_check_remote_strategy(out, apps, hub_file))

    return issues


def _check_values_secret(out: Path) -> list:
    issues = []
    path = out / "values-secret.yaml.template"
    if not path.exists():
        return issues

    data = _load_yaml(path)
    if not data:
        issues.append(Issue("values-secret.yaml.template", "error", "File is empty or invalid YAML"))
        return issues

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

    for secret in data.get("secrets", []):
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

    for platform in cfg("platforms", ["AWS", "Azure", "GCP", "IBMCloud", "None"]):
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


# ── SKILL.md conformance checks ──────────────────────────────────────


def _check_namespace_format(out: Path) -> list:
    issues = []
    hub_file = _find_values_group_file(out)
    path = out / hub_file
    if not path.exists():
        return issues
    data = _load_yaml(path)
    if not data:
        return issues
    ns = (data.get("clusterGroup") or {}).get("namespaces")
    if isinstance(ns, list):
        issues.append(Issue(
            hub_file, "error",
            "namespaces must be a map (dict), not a list — lists override across values files",
            auto_fixable=True,
        ))
    return issues


def _check_chart_path_convention(out: Path) -> list:
    issues = []
    hub_file = _find_values_group_file(out)
    path = out / hub_file
    if not path.exists():
        return issues
    data = _load_yaml(path)
    if not data:
        return issues
    apps = (data.get("clusterGroup") or {}).get("applications", {})
    for app_name, app in apps.items():
        p = app.get("path", "")
        if p.startswith("charts/all/"):
            issues.append(Issue(
                hub_file, "error",
                f"App '{app_name}' uses charts/all/ path — convention is charts/{app_name}",
                auto_fixable=True,
            ))
    return issues


def _check_secrets_chart_values(out: Path) -> list:
    issues = []
    hub_file = _find_values_group_file(out)
    path = out / hub_file
    if not path.exists():
        return issues
    data = _load_yaml(path)
    if not data:
        return issues
    apps = (data.get("clusterGroup") or {}).get("applications", {})
    for app_name in apps:
        if not app_name.endswith("-secrets"):
            continue
        chart_path = apps[app_name].get("path", f"charts/{app_name}")
        values_path = out / chart_path / "values.yaml"
        if not values_path.exists():
            continue
        values_data = _load_yaml(values_path)
        if not values_data or "secretStore" not in values_data:
            issues.append(Issue(
                f"{chart_path}/values.yaml", "warning",
                "Secrets chart values.yaml missing secretStore defaults",
                auto_fixable=True,
            ))
    return issues


def _check_single_argocd(out: Path) -> list:
    issues = []
    path = out / "values-global.yaml"
    if not path.exists():
        return issues
    data = _load_yaml(path)
    if not data:
        return issues
    g = data.get("global", {})
    if not g.get("singleArgoCD"):
        issues.append(Issue(
            "values-global.yaml", "warning",
            "global.singleArgoCD should be true for new Patterns",
            auto_fixable=True,
        ))
    return issues


def _check_eso_backtick_escaping(out: Path) -> list:
    import re
    issues = []
    for tmpl in out.glob("charts/*/templates/*.yaml"):
        try:
            content = tmpl.read_text()
        except OSError:
            continue
        if "ExternalSecret" not in content:
            continue
        unescaped = re.findall(r'(?<!`)(\{\{\s*\.\w+\s*\}\})(?!.*`)', content)
        if unescaped:
            rel = str(tmpl.relative_to(out))
            issues.append(Issue(
                rel, "warning",
                f"ExternalSecret has unescaped ESO template expressions: {unescaped[:3]}",
            ))
    return issues


def _check_remote_strategy(out: Path, apps: dict, values_file: str = "values-prod.yaml") -> list:
    """Validate remote-strategy specific requirements."""
    issues = []

    # Detect remote apps (have repoURL + path, no chart key)
    remote_apps = {
        name: app for name, app in apps.items()
        if "repoURL" in app and "path" in app and "chart" not in app
        and name not in ("vault", "openshift-external-secrets", "golang-external-secrets")
    }
    if not remote_apps:
        return issues

    # Secrets chart (named {app}-secrets) must exist if declared
    infra_apps = {"vault", "openshift-external-secrets", "golang-external-secrets"}
    secrets_apps = [name for name in apps if name.endswith("-secrets") and name not in infra_apps]
    for secrets_app in secrets_apps:
        app = apps[secrets_app]
        chart_path = app.get("path", f"charts/{secrets_app}")
        ps_dir = out / chart_path
        if not ps_dir.is_dir():
            issues.append(Issue(
                f"{chart_path}/", "error",
                f"{secrets_app} app declared but {chart_path}/ directory missing",
            ))
        elif not (ps_dir / "Chart.yaml").exists():
            issues.append(Issue(
                f"{chart_path}/Chart.yaml", "error",
                f"{chart_path}/ missing Chart.yaml",
            ))
        else:
            tmpl_dir = ps_dir / "templates"
            if tmpl_dir.is_dir():
                for tmpl in tmpl_dir.glob("*.yaml"):
                    data = _load_yaml(tmpl)
                    if not data:
                        continue
                    api_ver = data.get("apiVersion", "")
                    if "external-secrets.io" in api_ver and api_ver != "external-secrets.io/v1":
                        issues.append(Issue(
                            f"{chart_path}/templates/{tmpl.name}", "warning",
                            f"ExternalSecret uses {api_ver} — recommend external-secrets.io/v1",
                        ))

    # ignoreDifferences entries must have kind and jsonPointers
    for name, app in remote_apps.items():
        for i, diff in enumerate(app.get("ignoreDifferences", [])):
            if "kind" not in diff:
                issues.append(Issue(
                    values_file, "error",
                    f"App '{name}' ignoreDifferences[{i}] missing 'kind'",
                ))
            if "jsonPointers" not in diff:
                issues.append(Issue(
                    values_file, "error",
                    f"App '{name}' ignoreDifferences[{i}] missing 'jsonPointers'",
                ))

    return issues


# ── LLM review ──────────────────────────────────────────────────────


VALIDATION_CHECKLIST = """
Validate this Validated Pattern against these rules:

1. values-global.yaml: main: must be a root-level key (sibling of global:, NOT nested under it)
2. values-global.yaml: multiSourceConfig.enabled must be true
3. values-{clusterGroupName}.yaml: vault + openshift-external-secrets apps must be present if vault is enabled
4. values-{clusterGroupName}.yaml: Infrastructure apps (vault, external-secrets) must use chart: + chartVersion: (NOT path:)
5. values-{clusterGroupName}.yaml: Application apps should use path: charts/<name> (local) or repoURL: (external)
6. values-{clusterGroupName}.yaml: sharedValueFiles must reference the overrides template
7. values-{clusterGroupName}.yaml: Operators needing dedicated namespaces must have operatorGroup: true
8. values-{clusterGroupName}.yaml: projects: must be a list (not a string)
9. values-{clusterGroupName}.yaml: subscriptions: must be a dict (not a list)
10. values-secret.yaml.template: Must have version: "2.0"
11. values-secret.yaml.template: Must use vaultPrefixes: (plural, list) — NOT vaultPrefixOverride
12. Makefile: Should contain only "include Makefile-common"
13. No common/ directory (not needed with multisource)
14. Local chart paths should be charts/<name> (not charts/all/ or charts/hub/)
15. Secrets chart values.yaml should have secretStore defaults (name: vault-backend, kind: ClusterSecretStore)
16. Vault application and namespace belong only on the hub/main cluster, not spoke clusters
17. ExternalSecret template data values must use backtick escaping so Helm passes them through to ESO
18. namespaces: should be a map (dict) for merge compatibility, not a list
19. Operator subscriptions should specify namespace and channel
20. Local chart paths should be charts/<name>, not charts/all/<name>
21. Chart values.yaml should have default stubs for .Values.global.* and .Values.clusterGroup.* references

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


def _llm_review(out: Path, llm: Provider) -> list:
    """Ask the LLM to review generated files against the validation checklist."""
    files_content = []
    values_group = _find_values_group_file(out)
    for fname in ("values-global.yaml", values_group, "values-secret.yaml.template", "Makefile"):
        fpath = out / fname
        if fpath.exists():
            content = fpath.read_text()
            files_content.append(f"--- {fname} ---\n{content}")

    if not files_content:
        return []

    user_msg = "Review these pattern files:\n\n" + "\n\n".join(files_content)

    try:
        response = llm.complete(
            VALIDATION_CHECKLIST, user_msg,
            response_schema=VALIDATION_REVIEW_SCHEMA,
        )
    except Exception as e:
        return [Issue("", "warning", f"LLM review failed: {e}")]

    if response.parsed:
        return _parse_structured_review(response.parsed)

    return _parse_text_review(response.content)


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

    if issue.file == "values-global.yaml" and "nested under global" in issue.message:
        return _fix_main_nesting(out / "values-global.yaml")

    if issue.file == "values-global.yaml" and "multiSourceConfig.enabled" in issue.message:
        return _fix_multisource_enabled(out / "values-global.yaml")

    if issue.file == "values-global.yaml" and "clusterGroupChartVersion" in issue.message:
        return _fix_chart_version(out / "values-global.yaml")

    if issue.file == "values-secret.yaml.template" and "version" in issue.message.lower():
        return _fix_secret_version(out / "values-secret.yaml.template")

    if issue.file == "values-secret.yaml.template" and "vaultPrefixOverride" in issue.message:
        return _fix_vault_prefix(out / "values-secret.yaml.template")

    if issue.file == "values-secret.yaml.template" and "vaultPrefixes must be a list" in issue.message:
        return _fix_vault_prefix_type(out / "values-secret.yaml.template")

    if issue.file == "Makefile" and "common/Makefile" in issue.message:
        return _fix_makefile_include(out / "Makefile")

    if issue.file == "pattern.sh" and "not executable" in issue.message:
        (out / "pattern.sh").chmod(0o755)
        return True

    values_group = _find_values_group_file(out)
    if issue.file == values_group and "charts/all/" in issue.message:
        return _fix_chart_path(out / values_group)

    if issue.file == values_group and "sharedValueFiles" in issue.message:
        return _fix_shared_value_files(out / values_group)

    if issue.file == values_group and "should use chart:" in issue.message:
        return _fix_infra_app_chart(out / values_group, issue.message)

    if "overrides/" in issue.file or "overrides/ directory" in issue.message:
        return _fix_overrides(out, issue)

    if issue.file == values_group and "must be a map" in issue.message:
        return _fix_namespace_format(out / values_group)

    if issue.file == "values-global.yaml" and "singleArgoCD" in issue.message:
        return _fix_single_argocd(out / "values-global.yaml")

    if "Secrets chart" in issue.message and "secretStore" in issue.message:
        chart_path = issue.file.replace("/values.yaml", "")
        return _fix_secrets_chart_values(out, chart_path)

    return False


def _fix_main_nesting(path: Path) -> bool:
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
    msc.setdefault("clusterGroupChartVersion", cfg("pattern.clustergroup_version", "0.9.*"))
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
    data = _load_yaml(path)
    if not data:
        return False
    apps = data.get("clusterGroup", {}).get("applications", {})
    changed = False
    for app_name, app in apps.items():
        p = app.get("path", "")
        if p.startswith("charts/all/"):
            app["path"] = p.replace("charts/all/", "charts/", 1)
            changed = True
    if changed:
        _save_yaml(path, data)
    return changed


def _fix_namespace_format(path: Path) -> bool:
    data = _load_yaml(path)
    if not data:
        return False
    cg = data.get("clusterGroup", {})
    ns = cg.get("namespaces")
    if not isinstance(ns, list):
        return False
    ns_map = {}
    for entry in ns:
        if isinstance(entry, dict):
            ns_map.update(entry)
        elif isinstance(entry, str):
            ns_map[entry] = {}
    cg["namespaces"] = ns_map
    _save_yaml(path, data)
    return True


def _fix_single_argocd(path: Path) -> bool:
    data = _load_yaml(path)
    if not data:
        return False
    g = data.setdefault("global", {})
    g["singleArgoCD"] = True
    _save_yaml(path, data, doc_start=True)
    return True


def _fix_secrets_chart_values(out: Path, chart_path: str) -> bool:
    values_path = out / chart_path / "values.yaml"
    data = _load_yaml(values_path) or {}
    data.setdefault("secretStore", {
        "name": "vault-backend",
        "kind": "ClusterSecretStore",
    })
    _save_yaml(values_path, data)
    return True


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
    data = _load_yaml(path)
    if not data:
        return False

    infra_charts = {
        "vault": {
            "chart": "hashicorp-vault",
            "chartVersion": cfg("infrastructure.vault_chart_version", "0.1.*"),
        },
        "openshift-external-secrets": {
            "chart": "openshift-external-secrets",
            "chartVersion": cfg("infrastructure.external_secrets_chart_version", "0.2.*"),
        },
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
        for platform in cfg("platforms", ["AWS", "Azure", "GCP", "IBMCloud", "None"]):
            pf = overrides / f"values-{platform}.yaml"
            if not pf.exists():
                pf.write_text(f"# Platform-specific overrides for {platform}\n")
        return True

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
