"""CLI entry point for QuickPat."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .analyzer import QuickstartAnalyzer
from .generator import PatternGenerator, build_report
from .operators import OPERATORS
from .registry import (
    fetch_registry, resolve_name, check_dependency_freshness,
    detect_local_forks, fetch_chart_index,
)


def main():
    parser = argparse.ArgumentParser(
        prog='quickpat',
        description='Convert AI Quickstarts into Validated Patterns',
    )
    parser.add_argument(
        '--version', action='version', version=f'%(prog)s {__version__}'
    )
    parser.add_argument(
        '--patterns-dir', default=str(Path.home() / 'patterns'),
        help='Root directory for generated patterns (default: ~/patterns)',
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

    args = parser.parse_args()

    if args.command == 'list':
        cmd_list()
    elif args.command == 'analyze':
        cmd_analyze(args)
    elif args.command == 'create':
        cmd_create(args)


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

    pattern_name = args.name or f"{analysis.name}-pattern"
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
    analyzer = QuickstartAnalyzer(path)
    try:
        analysis = analyzer.analyze()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print_analysis(analysis)

    if args.non_interactive:
        config = build_default_config(analysis, args)
    else:
        config = interactive_config(analysis, args)

    generator = PatternGenerator(analysis, config)
    generator.generate()

    print_results(config)


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

    default_name = args.name or f"{analysis.name}-pattern"
    config['pattern_name'] = ask("Pattern name", default_name)
    config['app_name'] = ask("Application name", analysis.name)
    config['app_namespace'] = ask("Target namespace", analysis.name)

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
    else:
        config['operators'] = []

    # Chart strategy
    print("\nChart inclusion strategy:")
    print("  1. Local    - Copy chart into pattern repository")
    print("  2. External - Reference chart from Helm repository")
    strategy = ask("Choose", "1")
    config['chart_strategy'] = 'external' if strategy == '2' else 'local'

    if config['chart_strategy'] == 'external':
        default_repo = ''
        for dep in analysis.dependencies:
            if dep.repository:
                default_repo = dep.repository
                break
        config['chart_repo_url'] = ask("Helm repository URL", default_repo)
        config['chart_version'] = ask("Chart version", analysis.version)

    # Vault
    config['use_vault'] = ask_yes_no(
        "Enable HashiCorp Vault for secrets?", True
    )

    # Output directory
    default_output = args.output or str(
        Path(args.patterns_dir) / config['pattern_name']
    )
    config['output_dir'] = ask("Output directory", default_output)

    config['clustergroup_version'] = '0.9.*'

    return config


def build_default_config(analysis, args):
    name = args.name or f"{analysis.name}-pattern"
    return {
        'pattern_name': name,
        'app_name': analysis.name,
        'app_namespace': analysis.name,
        'operators': list(analysis.detected_operators),
        'chart_strategy': 'local',
        'use_vault': bool(analysis.detected_secrets),
        'output_dir': args.output or str(Path(args.patterns_dir) / name),
        'clustergroup_version': '0.9.*',
    }


def print_results(config):
    output = config['output_dir']
    print(f"\n--- Pattern Generated ---\n")
    print(f"Created: {output}/")
    print(f"  values-global.yaml")
    print(f"  values-hub.yaml")
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
    print(f"  3. Review and customize values-hub.yaml")
    print(f"  4. cp values-secret.yaml.template ~/values-secret-{config['pattern_name']}.yaml")
    print(f"  5. Edit ~/values-secret-{config['pattern_name']}.yaml with your secrets")
    print(f"  6. oc login <cluster>")
    print(f"  7. ./pattern.sh make install")


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
