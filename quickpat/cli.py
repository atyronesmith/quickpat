"""CLI entry point for QuickPat."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .analyzer import QuickstartAnalyzer
from .config import get as cfg
from .generator import build_report
from .providers import make_provider
from .operators import OPERATORS
from .pipeline import transform, transform_remote, skill_analyze, create_from_spec, TransformResult
from .profile import load_profile
from .readiness import check_readiness
from .registry import (
    fetch_registry, resolve_name, check_dependency_freshness,
    detect_local_forks, fetch_chart_index,
)
from .validator import validate, validate_and_fix


def main():
    parser = argparse.ArgumentParser(
        prog='quickpat',
        description='Convert AI Quickstarts into Validated Patterns',
    )
    parser.add_argument(
        '--version', action='version', version=f'%(prog)s {__version__}'
    )
    default_patterns_dir = str(Path(cfg("pattern.output_dir", "~/patterns")).expanduser())
    parser.add_argument(
        '--patterns-dir', default=default_patterns_dir,
        help=f'Root directory for generated patterns (default: {default_patterns_dir})',
    )

    subparsers = parser.add_subparsers(dest='command', required=True)

    # list subcommand
    subparsers.add_parser(
        'list', help='List available AI Quickstarts from the registry'
    )

    # analyze subcommand
    analyze_p = subparsers.add_parser(
        'analyze', help='Analyze an AI Quickstart'
    )
    analyze_p.add_argument(
        'path', help='Path, GitHub URL, or registry name (e.g. RAG)'
    )
    analyze_p.add_argument('--output', '-o', help='Output directory')
    analyze_p.add_argument('--name', help='Pattern name')

    # create subcommand
    create_p = subparsers.add_parser(
        'create', help='Create a Validated Pattern from a Quickstart'
    )
    create_p.add_argument(
        'path', help='Path, GitHub URL, or registry name (e.g. RAG)'
    )
    create_p.add_argument('--output', '-o', help='Output directory')
    create_p.add_argument('--name', help='Pattern name')
    create_p.add_argument(
        '--non-interactive', action='store_true',
        help='Use defaults, skip interactive prompts',
    )
    create_p.add_argument(
        '--crc-scripts', action='store_true',
        help='Generate CRC deployment/validation scripts',
    )
    _add_llm_args(create_p)
    _add_transform_args(create_p)

    # transform subcommand
    transform_p = subparsers.add_parser(
        'transform', help='Apply Layer 2 chart transformations to a pattern'
    )
    transform_p.add_argument(
        'path', help='Path to pattern directory'
    )
    transform_p.add_argument(
        '--rules', help='Comma-separated rules: secrets,hooks,registry (default: all)',
    )
    transform_p.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be changed without writing',
    )

    # new subcommand
    new_p = subparsers.add_parser(
        'new', help='Create a Validated Pattern from a spec YAML file'
    )
    new_p.add_argument('spec', help='Path to spec YAML file')
    new_p.add_argument('--output', '-o', help='Output directory')
    new_p.add_argument('--name', help='Pattern name override')
    new_p.add_argument(
        '--non-interactive', action='store_true',
        help='Use defaults, skip interactive prompts',
    )

    # batch subcommand
    batch_p = subparsers.add_parser(
        'batch', help='Transform all registered quickstarts'
    )
    batch_p.add_argument('--output', '-o', help='Root output directory')
    batch_p.add_argument(
        '--filter', help='Only process quickstarts matching this substring'
    )
    batch_p.add_argument(
        '--keep-going', action='store_true',
        help='Continue on failure instead of stopping',
    )
    _add_llm_args(batch_p)

    # update subcommand
    update_p = subparsers.add_parser(
        'update', help='Update a remote-strategy pattern from its upstream quickstart'
    )
    update_p.add_argument('path', help='Path to pattern directory')
    update_p.add_argument(
        '--force', action='store_true',
        help='Regenerate even if no upstream changes detected',
    )
    _add_llm_args(update_p)

    # check-ready subcommand
    ready_p = subparsers.add_parser(
        'check-ready', help='Check if a quickstart is publication-ready'
    )
    ready_p.add_argument(
        'path', help='Path, GitHub URL, or registry name (e.g. RAG)'
    )

    # validate subcommand
    validate_p = subparsers.add_parser(
        'validate', help='Validate a generated pattern'
    )
    validate_p.add_argument('path', help='Path to pattern directory')
    validate_p.add_argument(
        '--fix', action='store_true', help='Auto-fix issues'
    )
    validate_p.add_argument(
        '--max-iterations', type=int, default=3,
        help='Max auto-fix iterations (default: 3)',
    )
    validate_p.add_argument(
        '--json', action='store_true', dest='json_output',
        help='Output results as JSON',
    )
    _add_llm_args(validate_p)

    args = parser.parse_args()

    if args.command == 'list':
        cmd_list()
    elif args.command == 'analyze':
        cmd_analyze(args)
    elif args.command == 'create':
        cmd_create(args)
    elif args.command == 'new':
        cmd_new(args)
    elif args.command == 'batch':
        cmd_batch(args)
    elif args.command == 'update':
        cmd_update(args)
    elif args.command == 'check-ready':
        cmd_check_ready(args)
    elif args.command == 'transform':
        cmd_transform(args)
    elif args.command == 'validate':
        cmd_validate(args)


def _add_transform_args(parser):
    """Add chart transform options to a subparser."""
    parser.add_argument(
        '--transform', action='store_true',
        help='Apply Layer 2 chart transformations (secret externalization, etc.)',
    )
    parser.add_argument(
        '--transform-rules',
        help='Comma-separated transform rules: secrets,hooks,registry (default: all)',
    )


def _add_llm_args(parser):
    """Add common LLM options to a subparser."""
    parser.add_argument(
        '--llm', choices=['none', 'openai', 'anthropic', 'ollama', 'vllm', 'deepinfra'],
        default='none', help='LLM provider for enhanced detection/validation',
    )
    parser.add_argument('--model', help='Model name override')
    parser.add_argument('--llm-url', help='Base URL for ollama/vllm')


def cmd_list():
    """List available quickstarts from the registry."""
    try:
        registry = fetch_registry()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Available AI Quickstarts ({len(registry)}):\n")
    for entry in registry:
        print(f"  {entry['name']}")
        print(f"    {entry.get('url', '')}")

    print(f"\nUse: quickpat create <name>")


def resolve_path(path_or_url):
    """Resolve a path, GitHub URL, or registry name to a local path."""
    # Direct URL
    if path_or_url.startswith(('https://github.com/', 'git@')):
        return _clone(path_or_url)

    # Local path
    if Path(path_or_url).exists():
        return path_or_url

    # Try registry name
    try:
        url = resolve_name(path_or_url)
        return _clone(url)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _clone(url):
    tmpdir = tempfile.mkdtemp(prefix='quickpat-')
    print(f"Cloning {url}...")
    subprocess.run(
        ['git', 'clone', '--depth', '1', url, tmpdir],
        check=True, capture_output=True,
    )
    return tmpdir


def cmd_analyze(args):
    path = resolve_path(args.path)
    analyzer = QuickstartAnalyzer(path)
    try:
        analysis = analyzer.analyze()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print_analysis(analysis)

    pattern_name = args.name or analysis.name
    output_dir = Path(args.output or str(Path(args.patterns_dir) / pattern_name))
    docs_dir = output_dir / 'docs'
    docs_dir.mkdir(parents=True, exist_ok=True)

    report_path = docs_dir / 'quickstart-analysis.md'
    report_path.write_text(build_report(analysis))

    print(f"Created: {output_dir}/")
    print(f"  docs/quickstart-analysis.md")
    print(f"\nRun 'quickpat create' to generate the full pattern here.")


def cmd_create(args):
    print("=== QuickPat: AI Quickstart -> Validated Pattern ===\n")

    path = resolve_path(args.path)

    # Show analysis first
    try:
        analysis = skill_analyze(path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print_analysis(analysis)

    if args.non_interactive or args.llm != 'none':
        # Pipeline mode
        llm = make_provider({
            "provider": args.llm,
            "model": args.model or None,
            "base_url": getattr(args, "llm_url", None),
        })
        config = build_default_config(analysis, args, path)
        crc = getattr(args, 'crc_scripts', False)

        if config['chart_strategy'] == 'remote':
            result = transform_remote(
                quickstart_path=path,
                output_dir=config['output_dir'],
                pattern_name=config['pattern_name'],
                llm=llm,
                extra_config={'generate_crc_scripts': crc},
            )
        else:
            tx_rules = None
            if getattr(args, 'transform_rules', None):
                tx_rules = [r.strip() for r in args.transform_rules.split(',')]
            result = transform(
                quickstart_path=path,
                output_dir=config['output_dir'],
                pattern_name=config['pattern_name'],
                llm=llm,
                use_vault=config['use_vault'],
                chart_strategy=config['chart_strategy'],
                extra_config={
                    k: v for k, v in config.items() if k in ('tier',)
                } | {'generate_crc_scripts': crc},
                enable_transform=getattr(args, 'transform', False),
                transform_rules=tx_rules,
            )
        _print_transform_result(result)
        sys.exit(0 if result.success else 1)
    else:
        # Interactive mode
        config = interactive_config(analysis, args)

        if config['chart_strategy'] == 'remote':
            result = transform_remote(
                quickstart_path=path,
                output_dir=config['output_dir'],
                pattern_name=config['pattern_name'],
            )
            _print_transform_result(result)
        else:
            extra_keys = ('tier', 'secret_config', 'namespace_overrides', 'global_options')
            extra = {k: config[k] for k in extra_keys if k in config}
            result = transform(
                quickstart_path=path,
                output_dir=config['output_dir'],
                pattern_name=config['pattern_name'],
                use_vault=config['use_vault'],
                chart_strategy=config['chart_strategy'],
                extra_config=extra or None,
            )
            print_results(result.config or config)


def cmd_new(args):
    """Create a Validated Pattern from a spec YAML file."""
    print("=== QuickPat: Create Pattern from Spec ===\n")

    output_dir = args.output or str(
        Path(args.patterns_dir) / (args.name or 'new-pattern')
    )

    result = create_from_spec(
        spec_path=args.spec,
        output_dir=output_dir,
        pattern_name=args.name,
    )

    if result.success:
        _print_transform_result(result)
    else:
        for w in result.warnings:
            print(f"Error: {w}", file=sys.stderr)
        sys.exit(1)


def cmd_batch(args):
    """Transform all registered quickstarts."""
    print("=== QuickPat Batch: Transform All Quickstarts ===\n")

    try:
        registry = fetch_registry()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.filter:
        filt = args.filter.lower()
        registry = [e for e in registry if filt in e['name'].lower()]

    if not registry:
        print("No matching quickstarts found.")
        sys.exit(0)

    llm = make_provider({
        "provider": args.llm,
        "model": args.model or None,
        "base_url": getattr(args, "llm_url", None),
    })

    output_root = Path(args.output or args.patterns_dir)
    results = []

    for i, entry in enumerate(registry, 1):
        name = entry['name']
        url = entry.get('url')
        if not url:
            results.append((name, 'SKIP', 'no URL'))
            continue

        print(f"[{i}/{len(registry)}] {name}...")

        try:
            tmpdir = _clone(url)
        except subprocess.CalledProcessError:
            print(f"  clone failed")
            results.append((name, 'FAIL', 'clone failed'))
            if not args.keep_going:
                break
            continue

        pattern_name = name
        output_dir = str(output_root / pattern_name)

        try:
            result = transform(
                quickstart_path=tmpdir,
                output_dir=output_dir,
                pattern_name=pattern_name,
                llm=llm,
            )
            if result.success and result.validation and result.validation.valid:
                print(f"  OK -> {output_dir}/")
                results.append((name, 'OK', ''))
            else:
                issues = len(result.warnings)
                print(f"  WARN ({issues} issues)")
                results.append((name, 'WARN', f'{issues} issues'))
        except Exception as e:
            print(f"  FAIL: {e}")
            results.append((name, 'FAIL', str(e)))
            if not args.keep_going:
                break

    # Summary
    ok = sum(1 for _, s, _ in results if s == 'OK')
    warn = sum(1 for _, s, _ in results if s == 'WARN')
    fail = sum(1 for _, s, _ in results if s == 'FAIL')
    skip = sum(1 for _, s, _ in results if s == 'SKIP')

    print(f"\n--- Batch Summary ---\n")
    print(f"  {'Quickstart':<40} {'Status':<6} {'Detail'}")
    print(f"  {'─'*40} {'─'*6} {'─'*30}")
    for name, status, detail in results:
        print(f"  {name:<40} {status:<6} {detail}")
    print(f"\n  OK: {ok}  WARN: {warn}  FAIL: {fail}  SKIP: {skip}  Total: {len(results)}")

    sys.exit(1 if fail > 0 else 0)


def cmd_check_ready(args):
    """Check if a quickstart is publication-ready."""
    path = resolve_path(args.path)
    result = check_readiness(path)

    status = "READY" if result.ready else "NOT READY"
    print(f"Quickstart: {result.name}")
    print(f"Charts found: {result.charts_found}")
    print(f"Status: {status}\n")

    if result.issues:
        errors = [i for i in result.issues if i.severity == "error"]
        warnings = [i for i in result.issues if i.severity == "warning"]

        if errors:
            print("Errors:")
            for i in errors:
                print(f"  [{i.category}] {i.message}")

        if warnings:
            print("Warnings:")
            for i in warnings:
                print(f"  [{i.category}] {i.message}")

        print(f"\n  Errors: {len(errors)}  Warnings: {len(warnings)}")
    else:
        print("No issues found.")

    sys.exit(0 if result.ready else 1)


def cmd_transform(args):
    """Apply Layer 2 chart transformations to a generated pattern."""
    from .transformer import transform_chart as tx_chart, ALL_RULES

    pattern_dir = Path(args.path)
    charts_dir = pattern_dir / 'charts' / 'all'
    if not charts_dir.is_dir():
        print(f"Error: No charts/all/ directory in {args.path}", file=sys.stderr)
        sys.exit(1)

    rules = None
    if args.rules:
        rules = [r.strip() for r in args.rules.split(',')]
        invalid = set(rules) - set(ALL_RULES)
        if invalid:
            print(f"Error: Unknown rules: {invalid}. Valid: {ALL_RULES}",
                  file=sys.stderr)
            sys.exit(1)

    # Analyze each chart in the pattern
    total_result = tx_chart.__class__.__bases__  # just need the import
    from .transformer import TransformResult
    total = TransformResult()

    for chart_path in sorted(charts_dir.iterdir()):
        if not chart_path.is_dir():
            continue
        chart_yaml = chart_path / 'Chart.yaml'
        if not chart_yaml.exists():
            continue

        # Build a minimal analysis from the chart
        analyzer = QuickstartAnalyzer(str(chart_path))
        try:
            analysis = analyzer.analyze()
        except FileNotFoundError:
            continue

        chart_info = analysis.charts[0] if analysis.charts else None

        if args.dry_run:
            print(f"Would transform: {chart_path.name}")
            if rules:
                print(f"  Rules: {', '.join(rules)}")
            else:
                print(f"  Rules: {', '.join(ALL_RULES)}")
            if analysis.detected_secrets:
                print(f"  Secrets detected: {len(analysis.detected_secrets)}")
            continue

        result = tx_chart(str(chart_path), analysis, chart_info, rules=rules)
        total.merge(result)

        if result.rules_applied:
            print(f"Transformed: {chart_path.name}")
            for r in result.rules_applied:
                print(f"  Rule: {r}")
            for f in result.files_modified:
                print(f"  Modified: {f}")
            for f in result.files_created:
                print(f"  Created: {f}")
        for w in result.warnings:
            print(f"  Warning: {w}")

    if not args.dry_run:
        print(f"\nRules applied: {len(total.rules_applied)}")
        print(f"Files modified: {len(total.files_modified)}")
        print(f"Files created: {len(total.files_created)}")


def print_analysis(analysis):
    if len(analysis.charts) > 1:
        print(f"Quickstart: {analysis.name} ({len(analysis.charts)} charts)")
        for ci in analysis.charts:
            desc = f" — {ci.description}" if ci.description else ""
            print(f"  - {ci.name} v{ci.version}{desc}")
        print(f"Location: {analysis.chart_path}")
    else:
        print(f"Chart: {analysis.name} v{analysis.version}")
        if analysis.description:
            print(f"Description: {analysis.description}")
        print(f"Location: {analysis.chart_path}")

    if analysis.dependencies:
        print(f"\nDependencies ({len(analysis.dependencies)}):")
        for dep in analysis.dependencies:
            repo = f" (from {dep.repository})" if dep.repository else ""
            print(f"  - {dep.name} {dep.version}{repo}")

    features = []
    if analysis.has_vector_db:
        features.append("Vector Database")
    if analysis.has_llm_service:
        features.append("LLM Serving")
    if analysis.has_object_storage:
        features.append("Object Storage")
    if analysis.has_pipeline:
        features.append("Data Pipeline")
    if analysis.has_gpu_requirement:
        features.append("GPU Required")

    if features:
        print("\nDetected features:")
        for f in features:
            print(f"  + {f}")

    if analysis.detected_operators:
        print("\nRecommended operators:")
        for op_key in analysis.detected_operators:
            op = OPERATORS[op_key]
            print(f"  - {op['display_name']} ({op['subscription_name']})")

    if analysis.detected_secrets:
        print("\nPotential secrets:")
        for s in analysis.detected_secrets:
            print(f"  - {s.name} (at {s.path})")

    # Fetch shared chart index once for both checks
    chart_index = None
    if analysis.dependencies or len(analysis.charts) > 1:
        try:
            chart_index = fetch_chart_index()
        except RuntimeError:
            pass

    if analysis.dependencies and chart_index:
        stale = check_dependency_freshness(analysis.dependencies, chart_index)
        if stale:
            print("\nStale dependencies:")
            for name, pinned, latest in stale:
                print(f"  - {name} {pinned} -> {latest} available")

    if chart_index:
        forks = detect_local_forks(analysis.charts, chart_index)
        if forks:
            print("\nLocal forks of shared charts:")
            for name, path, latest in forks:
                print(f"  - {name} (at {path})")
                print(f"    shared version {latest} available in ai-architecture-charts")

    print()


def interactive_config(analysis, args):
    config = {}

    print("--- Configuration ---\n")

    default_name = args.name or analysis.name
    config['pattern_name'] = ask("Pattern name", default_name)
    config['app_name'] = ask("Application name", analysis.name)
    config['app_namespace'] = ask("Target namespace", analysis.name)

    # Pattern tier
    config['tier'] = ask_choice(
        "Pattern tier",
        ['sandbox', 'tested', 'maintained'],
        default='sandbox',
    )

    # Operator selection
    if analysis.detected_operators:
        print("\nOperators to install:")
        all_ops = list(analysis.detected_operators)
        for i, op_key in enumerate(all_ops, 1):
            op = OPERATORS[op_key]
            print(f"  {i}. {op['display_name']}")

        answer = ask(
            "Enter numbers to remove, or Enter to accept all", "all"
        )
        if answer.lower() == 'all' or not answer:
            config['operators'] = all_ops
        else:
            remove_indices = set()
            for part in answer.split(','):
                try:
                    idx = int(part.strip()) - 1
                    if 0 <= idx < len(all_ops):
                        remove_indices.add(idx)
                except ValueError:
                    pass
            config['operators'] = [
                o for i, o in enumerate(all_ops) if i not in remove_indices
            ]

        # Offer to add undetected operators
        available = [k for k in OPERATORS if k not in config['operators']]
        if available:
            add_more = ask_yes_no("Add additional operators?", False)
            if add_more:
                print("  Available operators:")
                for i, op_key in enumerate(available, 1):
                    print(f"    {i}. {OPERATORS[op_key]['display_name']} ({op_key})")
                answer = ask("Enter numbers to add (comma-separated)", "")
                if answer:
                    for part in answer.split(','):
                        try:
                            idx = int(part.strip()) - 1
                            if 0 <= idx < len(available):
                                config['operators'].append(available[idx])
                        except ValueError:
                            pass
    else:
        config['operators'] = []

    # Namespace overrides (multi-chart only)
    if len(analysis.charts) > 1:
        print("\nNamespace assignments:")
        print(f"  {'Chart':<20} {'Namespace':<20} {'OAI Labels'}")
        print(f"  {'─'*20} {'─'*20} {'─'*10}")
        for ci in analysis.charts:
            ns = ci.group or ci.name
            labels = "yes" if ci.needs_oai_labels else "no"
            print(f"  {ci.name:<20} {ns:<20} {labels}")
        if ask_yes_no("\nOverride namespace assignments?", False):
            config['namespace_overrides'] = {}
            for ci in analysis.charts:
                default_ns = ci.group or ci.name
                ns = ask(f"  Namespace for {ci.name}", default_ns)
                if ns != default_ns:
                    config['namespace_overrides'][ci.name] = ns

    # Secret classification
    if analysis.detected_secrets:
        print("\nSecrets detected:")
        for i, s in enumerate(analysis.detected_secrets, 1):
            print(f"  {i}. {s.name} (at {s.path})")
        if ask_yes_no("Classify secrets? (prompt/generate/skip)", False):
            config['secret_config'] = {}
            for s in analysis.detected_secrets:
                action = ask_choice(
                    f"  {s.name}",
                    ['prompt', 'generate', 'skip'],
                    default='prompt',
                )
                if action != 'prompt':
                    config['secret_config'][s.name] = action

    # Chart strategy
    print("\nChart inclusion strategy:")
    print("  1. Local    - Copy chart into pattern repository")
    print("  2. External - Reference chart from Helm repository")
    print("  3. Remote   - Track upstream Git repository (recommended)")
    strategy = ask("Choose", "3")
    if strategy == '2':
        config['chart_strategy'] = 'external'
    elif strategy == '3':
        config['chart_strategy'] = 'remote'
    else:
        config['chart_strategy'] = 'local'

    if config['chart_strategy'] == 'external':
        default_repo = ''
        for dep in analysis.dependencies:
            if dep.repository:
                default_repo = dep.repository
                break
        config['chart_repo_url'] = ask("Helm repository URL", default_repo)
        config['chart_version'] = ask("Chart version", analysis.version)
    elif config['chart_strategy'] == 'remote':
        analyzer = QuickstartAnalyzer(resolve_path(args.path))
        git_url, chart_path_in_repo = analyzer.detect_git_origin()
        config['git_repo_url'] = ask("Git repository URL", git_url)
        config['chart_path_in_repo'] = ask("Chart path in repo", chart_path_in_repo)
        config['chart_branch'] = ask("Git branch", "main")

    # Vault
    config['use_vault'] = ask_yes_no(
        "Enable HashiCorp Vault for secrets?", True
    )

    # Global options
    if ask_yes_no("Customize global options?", False):
        config['global_options'] = {}
        config['global_options']['syncPolicy'] = ask_choice(
            "Sync policy", ['Automatic', 'Manual'], default='Automatic',
        )
        config['global_options']['installPlanApproval'] = ask_choice(
            "Install plan approval", ['Automatic', 'Manual'], default='Automatic',
        )

    # Output directory
    default_output = args.output or str(
        Path(args.patterns_dir) / config['pattern_name']
    )
    config['output_dir'] = ask("Output directory", default_output)

    config['clustergroup_version'] = cfg("pattern.clustergroup_version", "0.9.*")

    return config


def cmd_update(args):
    """Update a remote-strategy pattern from its upstream quickstart."""
    print("=== QuickPat: Update Pattern ===\n")

    pattern_dir = args.path
    profile = load_profile(pattern_dir)
    if not profile:
        print("Error: No profile found. Run 'quickpat create' first.", file=sys.stderr)
        sys.exit(1)

    if not profile.source_repo_url:
        print("Error: Profile has no source_repo_url. Not a remote-strategy pattern.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Source: {profile.source_repo_url}")
    print(f"Chart path: {profile.source_chart_path}")

    # Clone upstream
    qs_path = _clone(profile.source_repo_url)

    llm = make_provider({
        "provider": args.llm,
        "model": args.model or None,
        "base_url": getattr(args, "llm_url", None),
    })

    result = transform_remote(
        quickstart_path=qs_path,
        output_dir=pattern_dir,
        llm=llm,
    )
    _print_transform_result(result)
    sys.exit(0 if result.success else 1)


def build_default_config(analysis, args, quickstart_path=None):
    name = args.name or analysis.name

    # Detect git origin to decide default strategy
    strategy = 'local'
    git_url = ''
    chart_path_in_repo = ''
    if quickstart_path:
        analyzer = QuickstartAnalyzer(quickstart_path)
        git_url, chart_path_in_repo = analyzer.detect_git_origin()
        if git_url:
            strategy = 'remote'

    return {
        'pattern_name': name,
        'app_name': analysis.name,
        'app_namespace': analysis.name,
        'operators': list(analysis.detected_operators),
        'chart_strategy': strategy,
        'use_vault': bool(analysis.detected_secrets),
        'output_dir': args.output or str(Path(args.patterns_dir) / name),
        'clustergroup_version': '0.9.*',
        'tier': 'sandbox',
        'git_repo_url': git_url,
        'chart_path_in_repo': chart_path_in_repo,
    }


def print_results(config):
    output = config['output_dir']
    print(f"\n--- Pattern Generated ---\n")
    print(f"Created: {output}/")
    print(f"  values-global.yaml")
    gn = config.get('cluster_group_name', 'prod')
    print(f"  values-{gn}.yaml")
    if config.get('use_vault'):
        print(f"  values-secret.yaml.template")
    print(f"  Makefile")
    print(f"  Makefile-common")
    print(f"  pattern.sh")
    print(f"  pattern-metadata.yaml")
    print(f"  ansible.cfg")
    print(f"  .gitignore")
    if config.get('chart_strategy') == 'local':
        charts_dir = Path(output) / 'charts' / 'all'
        if charts_dir.is_dir():
            for d in sorted(charts_dir.iterdir()):
                if d.is_dir():
                    print(f"  charts/all/{d.name}/")
        else:
            print(f"  charts/all/{config.get('app_name', 'app')}/")
    print(f"  overrides/")
    print(f"  docs/quickstart-analysis.md")

    print(f"\nNext steps:")
    print(f"  1. cd {output}")
    print(f"  2. git init && git add -A && git commit -m 'Initial pattern'")
    print(f"  3. Review and customize values-{gn}.yaml")
    print(f"  4. cp values-secret.yaml.template ~/values-secret-{config['pattern_name']}.yaml")
    print(f"  5. Edit ~/values-secret-{config['pattern_name']}.yaml with your secrets")
    print(f"  6. oc login <cluster>")
    print(f"  7. ./pattern.sh make install")


def cmd_validate(args):
    """Validate a generated pattern."""
    llm = make_provider({
        "provider": args.llm,
        "model": args.model or None,
        "base_url": getattr(args, "llm_url", None),
    })
    if args.fix:
        result = validate_and_fix(
            args.path, llm=llm, max_iterations=args.max_iterations,
        )
    else:
        result = validate(args.path, llm=llm)

    if args.json_output:
        import json
        from dataclasses import asdict
        print(json.dumps(asdict(result), indent=2))
    else:
        status = "VALID" if result.valid else "INVALID"
        print(f"Validation: {status}")
        for issue in result.issues:
            fixed = " [FIXED]" if issue.fix_applied else ""
            print(f"  [{issue.severity}] {issue.file}: {issue.message}{fixed}")
        if result.fixes_applied:
            print(f"\nAuto-fixes applied: {result.fixes_applied}")
            print(f"Iterations: {result.iterations}")
    sys.exit(0 if result.valid else 1)


def _print_transform_result(result: TransformResult):
    if result.success:
        print(f"\nPattern generated: {result.pattern_dir}/")
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


def ask(prompt, default=None):
    if default:
        answer = input(f"{prompt} [{default}]: ").strip()
        return answer if answer else default
    return input(f"{prompt}: ").strip()


def ask_yes_no(prompt, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ('y', 'yes')


def ask_choice(prompt, choices, default=None):
    """Ask user to pick from a list of choices."""
    labels = '/'.join(
        c.upper() if c == default else c for c in choices
    )
    answer = input(f"{prompt} ({labels}): ").strip().lower()
    if not answer:
        return default
    for c in choices:
        if c.lower() == answer:
            return c
    return default
